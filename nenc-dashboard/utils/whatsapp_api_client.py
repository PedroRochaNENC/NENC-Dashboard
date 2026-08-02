"""
WhatsApp API Client — Comunica com a NencProsodiaWhatsapp-API.

Funções para buscar campanhas, contatos e áudios processados,
além de converter os resultados da API nos CSVs esperados pelo Dashboard.

NOTA IMPORTANTE: Na API, os áudios são recebidos via webhook do WhatsApp
e NÃO estão vinculados a campanhas. Qualquer pessoa que envie um áudio
ao número configurado terá seu áudio registrado, independente de campanhas.
A sincronização pode ser feita por:
    - Por telefone de um contato pertencente à organização
    - Por projeto externo pertencente à organização
    - Por campanha pertencente à organização, usando seus contatos registrados
"""

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx
import pandas as pd
from dotenv import load_dotenv

from utils import auth
from utils.organization_data import (
    claim_external_resource,
    list_external_resources,
    register_derived_external_resource,
    release_external_resource,
)


_APPLICATION_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_WORKSPACE_ENV_PATH = _APPLICATION_ENV_PATH.parent / ".env"
_ENV_PATH = next(
    (path for path in (_APPLICATION_ENV_PATH, _WORKSPACE_ENV_PATH) if path.exists()),
    _APPLICATION_ENV_PATH,
)
load_dotenv(_ENV_PATH)


def _normalize_phone(value: Any) -> str:
    return "".join(filter(str.isdigit, str(value or "")))


def _owned_resource_ids(resource_type: str) -> set[str]:
    return {
        resource["id"]
        for resource in list_external_resources(resource_type)
    }


def _owned_contact_ids_by_phone() -> dict[str, str]:
    contact_ids_by_phone = {}
    for resource in list_external_resources("whatsapp_contact"):
        phone = _normalize_phone(resource["metadata"].get("phone"))
        if phone:
            contact_ids_by_phone[phone] = resource["id"]
    return contact_ids_by_phone


def _require_owned_resource(resource_type: str, resource_id: Any) -> None:
    claim_external_resource(resource_type, resource_id)


def _register_audio_from_parent(
    audio: Dict, parent_resource_type: str, parent_resource_id: Any
) -> Optional[Dict]:
    audio_id = audio.get("id")
    if audio_id is None:
        return None
    try:
        register_derived_external_resource(
            "whatsapp_audio",
            audio_id,
            parent_resource_type,
            parent_resource_id,
            {
                "phone": _normalize_phone(audio.get("contact_phone")),
                "whatsapp_message_id": str(audio.get("whatsapp_message_id") or ""),
            },
        )
    except auth.AuthorizationError:
        return None
    return audio


def _registered_audios(
    audios: List[Dict], parent_resource_type: str, parent_resource_id: Any
) -> List[Dict]:
    registered_audios = []
    for audio in audios:
        registered_audio = _register_audio_from_parent(
            audio, parent_resource_type, parent_resource_id
        )
        if registered_audio is not None:
            registered_audios.append(registered_audio)
    return registered_audios


def list_owned_jobs(
    audio_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict]:
    """Return only jobs attached to audio resources owned by the active organization."""

    owned_audio_ids = _owned_resource_ids("whatsapp_audio")
    if not owned_audio_ids:
        return []
    if audio_id is not None and str(audio_id) not in owned_audio_ids:
        raise auth.AuthorizationError("O audio nao pertence a organizacao ativa.")

    owned_jobs = []
    for job in _list_jobs_from_api(
        audio_id=audio_id,
        status=status,
        skip=skip,
        limit=limit,
    ):
        audio_id = job.get("audio_id")
        job_id = job.get("id")
        if str(audio_id) not in owned_audio_ids or job_id is None:
            continue
        try:
            register_derived_external_resource(
                "whatsapp_job",
                job_id,
                "whatsapp_audio",
                audio_id,
            )
        except auth.AuthorizationError:
            continue
        owned_jobs.append(job)
    return owned_jobs


def list_jobs(
    audio_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Dict]:
    """Return only processing jobs owned by the active organization."""

    return list_owned_jobs(audio_id=audio_id, status=status, skip=skip, limit=limit)


def create_owned_campaign(
    name: str,
    template_name: str,
    language_code: str,
    contact_ids: List[Any],
    project_id: Optional[int] = None,
) -> Dict:
    """Create a campaign only for contacts owned by the active organization."""

    contact_id_list = list(contact_ids)
    contact_id_values = {
        str(contact_id).strip()
        for contact_id in contact_id_list
        if str(contact_id).strip()
    }
    if not contact_id_values:
        raise ValueError("Informe ao menos um contato para a campanha.")
    unauthorized_contact_ids = contact_id_values.difference(
        _owned_resource_ids("whatsapp_contact")
    )
    if unauthorized_contact_ids:
        raise auth.AuthorizationError(
            "Todos os contatos da campanha devem pertencer a organizacao ativa."
        )
    if project_id is not None:
        _require_owned_resource("whatsapp_api_project", project_id)

    campaign = _create_campaign_from_api(
        name=name,
        template_name=template_name,
        language_code=language_code,
        contact_ids=contact_id_list,
        project_id=project_id,
    )
    if not isinstance(campaign, dict) or campaign.get("id") is None:
        raise RuntimeError("A API nao retornou o identificador da campanha criada.")
    claim_external_resource(
        "whatsapp_campaign",
        campaign["id"],
        {"api_project_id": project_id},
        created=True,
    )
    return campaign


def list_owned_contacts(
    search: Optional[str] = None, limit: int = 500
) -> List[Dict]:
    """Return only contacts registered to the active organization."""

    owned_contact_ids = _owned_resource_ids("whatsapp_contact")
    return [
        contact
        for contact in _list_contacts_from_api(search=search, limit=limit)
        if str(contact.get("id", contact.get("contact_id"))) in owned_contact_ids
    ]


def create_owned_contact(phone: str, name: Optional[str] = None) -> Dict:
    """Create a contact and reject an API response that reuses an unowned record."""

    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("Informe um telefone valido.")
    existing_contact_ids = {
        str(contact.get("id", contact.get("contact_id")))
        for contact in _list_contacts_from_api(search=normalized_phone, limit=1000)
        if contact.get("id", contact.get("contact_id")) is not None
    }
    contact = _create_contact_from_api(phone=normalized_phone, name=name)
    if not isinstance(contact, dict):
        raise RuntimeError("A API nao retornou o contato criado.")
    contact_id = contact.get("id", contact.get("contact_id"))
    if contact_id is None:
        raise RuntimeError("A API nao retornou o identificador do contato criado.")
    claim_external_resource(
        "whatsapp_contact",
        contact_id,
        {
            "phone": _normalize_phone(contact.get("phone")) or normalized_phone,
            "name": contact.get("name") or name or "",
        },
        created=str(contact_id) not in existing_contact_ids,
    )
    return contact


def delete_owned_contact(contact_id: Any) -> None:
    """Delete a contact only after confirming its organization ownership."""

    _require_owned_resource("whatsapp_contact", contact_id)
    _delete_contact_from_api(contact_id)
    release_external_resource("whatsapp_contact", contact_id)


def import_owned_contacts_csv(
    content: bytes, phones: Iterable[Any]
) -> tuple[Dict, int, int]:
    """Import contacts and register only IDs that appeared after this import."""

    normalized_phones = {
        _normalize_phone(phone)
        for phone in phones
        if _normalize_phone(phone)
    }
    if not normalized_phones:
        raise ValueError("O arquivo nao contem telefones validos para importar.")

    existing_contact_ids = set()
    for phone in normalized_phones:
        existing_contact_ids.update(
            str(contact.get("id", contact.get("contact_id")))
            for contact in _list_contacts_from_api(search=phone, limit=1000)
            if contact.get("id", contact.get("contact_id")) is not None
        )

    result = _import_contacts_csv_from_api(content)
    if not isinstance(result, dict):
        raise RuntimeError("A API retornou uma resposta invalida para a importacao.")

    claimed_count = 0
    unavailable_count = 0
    for phone in normalized_phones:
        for contact in _list_contacts_from_api(search=phone, limit=1000):
            contact_id = contact.get("id", contact.get("contact_id"))
            if contact_id is None or str(contact_id) in existing_contact_ids:
                continue
            try:
                claim_external_resource(
                    "whatsapp_contact",
                    contact_id,
                    {
                        "phone": _normalize_phone(contact.get("phone")) or phone,
                        "name": contact.get("name") or "",
                    },
                    created=True,
                )
            except auth.AuthorizationError:
                unavailable_count += 1
            else:
                claimed_count += 1
    return result, claimed_count, unavailable_count


class AudioFileUnavailableError(RuntimeError):
    """Indica que a API não possui mais o arquivo de áudio solicitado."""


# ---------------------------------------------------------------------------
# Credenciais
# ---------------------------------------------------------------------------

def _get_api_url() -> str:
    """Retorna a URL da API de WhatsApp configurada no ambiente do servidor."""

    return os.getenv("WHATSAPP_API_URL", "").rstrip("/")


def _get_api_key() -> str:
    """Retorna a chave da API de WhatsApp configurada no ambiente do servidor."""

    return os.getenv("WHATSAPP_API_KEY", "")


def is_configured() -> bool:
    """Verifica se as credenciais da API estão preenchidas."""
    return bool(_get_api_url()) and bool(_get_api_key())


def _authorize_audio_file_request(request: httpx.Request) -> None:
    path_parts = [part for part in request.url.path.split("/") if part]
    for index, path_part in enumerate(path_parts):
        if (
            path_part == "audios"
            and len(path_parts) > index + 2
            and path_parts[index + 2] in {"file", "download"}
        ):
            _require_owned_resource("whatsapp_audio", path_parts[index + 1])
            return


def _client(timeout: float = 30.0) -> httpx.Client:
    """Cria um httpx.Client configurado com timeout e autenticação."""
    url = _get_api_url()
    key = _get_api_key()
    if not url or not key:
        raise RuntimeError(
            "WhatsApp API não configurada. Preencha URL e API Key na tela de Projetos."
        )
    return httpx.Client(
        base_url=url,
        headers={"X-API-Key": key},
        timeout=timeout,
        event_hooks={"request": [_authorize_audio_file_request]},
    )


# ---------------------------------------------------------------------------
# Teste de conectividade
# ---------------------------------------------------------------------------

def test_connection() -> tuple[bool, str]:
    """Testa a conexão com a API. Retorna (sucesso, mensagem)."""
    try:
        with _client() as c:
            resp = c.get("/health")
            resp.raise_for_status()
            return True, "Conexão OK"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Campanhas
# ---------------------------------------------------------------------------

def get_campaigns(project_id: Optional[int] = None) -> List[Dict]:
    """Lista todas as campanhas disponíveis na API, opcionalmente filtradas por projeto."""
    params: Dict[str, Any] = {"limit": 500}
    if project_id is not None:
        _require_owned_resource("whatsapp_api_project", project_id)
        params["project_id"] = project_id
    with _client() as c:
        resp = c.get("/campaigns", params=params)
        resp.raise_for_status()
        campaigns = resp.json()
    owned_campaign_ids = _owned_resource_ids("whatsapp_campaign")
    return [
        campaign
        for campaign in campaigns
        if str(campaign.get("id")) in owned_campaign_ids
    ]


def get_campaign(campaign_id: int) -> Dict:
    """Retorna detalhes de uma campanha pelo ID."""
    _require_owned_resource("whatsapp_campaign", campaign_id)
    with _client() as c:
        resp = c.get(f"/campaigns/{campaign_id}")
        resp.raise_for_status()
        return resp.json()


def get_campaign_contacts(campaign_id: int) -> List[Dict]:
    """Lista os contatos (com telefone) vinculados a uma campanha."""
    _require_owned_resource("whatsapp_campaign", campaign_id)
    with _client() as c:
        resp = c.get(f"/campaigns/{campaign_id}/contacts")
        resp.raise_for_status()
        contacts = resp.json()
    owned_contact_ids = _owned_resource_ids("whatsapp_contact")
    return [
        contact
        for contact in contacts
        if str(contact.get("id", contact.get("contact_id"))) in owned_contact_ids
    ]


# ---------------------------------------------------------------------------
# Áudios — o ponto central da integração
#
# Na API, os áudios chegam via webhook do WhatsApp e ficam na tabela `audios`
# com campos: id, whatsapp_message_id, contact_phone, media_id, local_path,
# duration_sec, received_at.
#
# O processamento (DevAIce + Whisper) fica na tabela `analysis_jobs`.
# O `GET /audios` aceita filtros: phone, from_date, to_date, has_file.
# ---------------------------------------------------------------------------

def get_all_audios(phone: Optional[str] = None, limit: int = 500, project_id: Optional[int] = None) -> List[Dict]:
    """
    Lista áudios da API, opcionalmente filtrando por telefone ou projeto.
    Retorna lista de AudioResponse: {id, whatsapp_message_id, contact_phone,
    media_id, local_path, duration_sec, received_at}
    """
    params: Dict[str, Any] = {"limit": limit}
    if project_id is not None:
        _require_owned_resource("whatsapp_api_project", project_id)
        params["project_id"] = project_id
        parent_resource_type = "whatsapp_api_project"
        parent_resource_id = project_id
    elif phone:
        normalized_phone = _normalize_phone(phone)
        contact_ids_by_phone = _owned_contact_ids_by_phone()
        if normalized_phone not in contact_ids_by_phone:
            raise auth.AuthorizationError("O telefone nao pertence a organizacao ativa.")
        params["phone"] = normalized_phone
        parent_resource_type = "whatsapp_contact"
        parent_resource_id = contact_ids_by_phone[normalized_phone]
    else:
        raise auth.AuthorizationError(
            "Informe um projeto ou telefone pertencente a organizacao ativa."
        )
    with _client() as c:
        resp = c.get("/audios", params=params)
        resp.raise_for_status()
        audios = resp.json()
    return _registered_audios(audios, parent_resource_type, parent_resource_id)


def get_audio_status(audio_id: int) -> Dict:
    """
    Retorna o status de processamento de um áudio.
    Campos relevantes: status, has_result_json, has_transcription
    """
    _require_owned_resource("whatsapp_audio", audio_id)
    with _client() as c:
        resp = c.get(f"/audios/{audio_id}/status")
        resp.raise_for_status()
        return resp.json()


def get_audio_result(audio_id: int) -> Dict:
    """
    Baixa o resultado completo do job mais recente (DevAIce + Whisper).
    A rota GET /audios/{id}/result retorna um JobResponse com result_json
    já parseado como dict pelo validator do Pydantic.
    """
    _require_owned_resource("whatsapp_audio", audio_id)
    with _client() as c:
        resp = c.get(f"/audios/{audio_id}/result")
        resp.raise_for_status()
        data = resp.json()
        # result_json já vem como dict (deserializado pelo Pydantic field_validator)
        result_json = data.get("result_json")
        if isinstance(result_json, str):
            result_json = json.loads(result_json)
        return result_json or {}


def get_audio_transcript(audio_id: int) -> str:
    """Baixa a transcrição em texto puro de um áudio."""
    _require_owned_resource("whatsapp_audio", audio_id)
    with _client() as c:
        resp = c.get(f"/audios/{audio_id}/transcript")
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Projetos API
# ---------------------------------------------------------------------------

def get_api_projects() -> List[Dict]:
    """Lista todos os projetos cadastrados na API."""
    with _client() as c:
        resp = c.get("/projects")
        resp.raise_for_status()
        projects = resp.json()
    owned_project_ids = _owned_resource_ids("whatsapp_api_project")
    return [project for project in projects if str(project.get("id")) in owned_project_ids]


def create_api_project(name: str, organization: str) -> Dict:
    """Cria um novo projeto na API."""
    body = {"name": name, "organization": organization}
    with _client() as c:
        resp = c.post("/projects", json=body)
        resp.raise_for_status()
        project = resp.json()
    project_id = project.get("id", project.get("project_id")) if isinstance(project, dict) else None
    if project_id is None:
        raise RuntimeError("A API nao retornou o identificador do projeto criado.")
    claim_external_resource(
        "whatsapp_api_project",
        project_id,
        {"name": name, "organization": organization},
        created=True,
    )
    return project


def get_api_project(project_id: int) -> Dict:
    """Retorna detalhes de um projeto na API pelo ID."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get(f"/projects/{project_id}")
        resp.raise_for_status()
        return resp.json()


def update_api_project(project_id: int, name: Optional[str] = None, organization: Optional[str] = None) -> Dict:
    """Atualiza um projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    body = {}
    if name is not None:
        body["name"] = name
    if organization is not None:
        body["organization"] = organization
    with _client() as c:
        resp = c.patch(f"/projects/{project_id}", json=body)
        resp.raise_for_status()
        return resp.json()


def delete_api_project(project_id: int) -> None:
    """Exclui um projeto na API pelo ID."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.delete(f"/projects/{project_id}")
        resp.raise_for_status()
    release_external_resource("whatsapp_api_project", project_id)
    return None


# Sub-rotas de projeto
def get_project_audios(project_id: int) -> List[Dict]:
    """Lista os áudios associados a um projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get(f"/projects/{project_id}/audios")
        resp.raise_for_status()
        audios = resp.json()
    return _registered_audios(audios, "whatsapp_api_project", project_id)


def upload_audio_to_project(project_id: int, file: Any, label: Optional[str] = None) -> Dict:
    """Faz upload de um arquivo de áudio diretamente para o projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    files = {"file": file}
    data = {}
    if label:
        data["label"] = label
    with _client(timeout=300.0) as c:
        resp = c.post(f"/projects/{project_id}/audios/upload", files=files, data=data)
        resp.raise_for_status()
        audio = resp.json()
    if isinstance(audio, dict):
        _register_audio_from_parent(audio, "whatsapp_api_project", project_id)
    return audio


def get_project_campaigns(project_id: int) -> List[Dict]:
    """Lista as campanhas associadas a um projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get(f"/projects/{project_id}/campaigns")
        resp.raise_for_status()
        campaigns = resp.json()
    owned_campaign_ids = _owned_resource_ids("whatsapp_campaign")
    return [
        campaign
        for campaign in campaigns
        if str(campaign.get("id")) in owned_campaign_ids
    ]


def get_project_contacts(project_id: int) -> List[Dict]:
    """Lista os contatos vinculados a um projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get(f"/projects/{project_id}/contacts")
        resp.raise_for_status()
        contacts = resp.json()
    owned_contact_ids = _owned_resource_ids("whatsapp_contact")
    return [
        contact
        for contact in contacts
        if str(contact.get("id", contact.get("contact_id"))) in owned_contact_ids
    ]


def link_contact_to_project(project_id: int, phone: str, name: Optional[str] = None) -> Dict:
    """Vincula/cria um contato para o projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    if _normalize_phone(phone) not in _owned_contact_ids_by_phone():
        raise auth.AuthorizationError("O contato nao pertence a organizacao ativa.")
    body = {"phone": phone}
    if name:
        body["name"] = name
    with _client() as c:
        resp = c.post(f"/projects/{project_id}/contacts", json=body)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Buscador de áudios para sincronização
# ---------------------------------------------------------------------------

def fetch_audios_for_sync(
    campaign_id: Optional[int] = None,
    phones: Optional[List[str]] = None,
    api_project_id: Optional[int] = None,
) -> List[Dict]:
    """
    Busca áudios da API prontos para sincronização.

    Estratégia:
    - Se api_project_id fornecido: busca áudios do projeto externo pertencente a organizacao
    - Se campaign_id fornecido: busca telefones pertencentes a organizacao na campanha
    - Se phones fornecido: busca somente telefones pertencentes a organizacao

    Retorna apenas áudios cujo processamento está 'done' (tem result_json).
    """
    def normalize_phone(value: Any) -> str:
        return "".join(filter(str.isdigit, str(value or "")))

    owned_api_project_ids = {
        resource["id"]
        for resource in list_external_resources("whatsapp_api_project")
    }
    owned_campaign_ids = {
        resource["id"]
        for resource in list_external_resources("whatsapp_campaign")
    }
    owned_contact_resources = list_external_resources("whatsapp_contact")
    owned_contact_ids = {resource["id"] for resource in owned_contact_resources}
    owned_phones = {
        normalize_phone(resource["metadata"].get("phone"))
        for resource in owned_contact_resources
        if normalize_phone(resource["metadata"].get("phone"))
    }

    target_phones: Optional[List[str]] = [
        normalize_phone(phone) for phone in phones or [] if normalize_phone(phone)
    ]
    all_audios: List[Dict] = []

    if api_project_id:
        if str(api_project_id) not in owned_api_project_ids:
            raise auth.AuthorizationError("O projeto externo nao pertence a organizacao ativa.")
        try:
            all_audios = get_project_audios(api_project_id)
        except Exception:
            all_audios = []
    elif campaign_id:
        if str(campaign_id) not in owned_campaign_ids:
            raise auth.AuthorizationError("A campanha externa nao pertence a organizacao ativa.")
        contacts = get_campaign_contacts(campaign_id)
        target_phones = [
            normalize_phone(contact.get("phone"))
            for contact in contacts
            if (
                str(contact.get("id", contact.get("contact_id"))) in owned_contact_ids
                or normalize_phone(contact.get("phone")) in owned_phones
            )
            and normalize_phone(contact.get("phone"))
        ]
    elif target_phones:
        unauthorized_phones = set(target_phones).difference(owned_phones)
        if unauthorized_phones:
            raise auth.AuthorizationError("Um ou mais telefones nao pertencem a organizacao ativa.")
    else:
        raise ValueError(
            "Informe um projeto, campanha ou telefone pertencente a organizacao para sincronizar."
        )

    if target_phones and not all_audios:
        audios_by_id: Dict[str, Dict] = {}
        for phone in target_phones:
            try:
                for audio in get_all_audios(phone=phone):
                    audios_by_id[str(audio.get("id"))] = audio
            except Exception:
                pass
        all_audios = list(audios_by_id.values())

    # Filtrar apenas com resultado pronto
    ready_audios: List[Dict] = []
    for audio in all_audios:
        try:
            status_info = get_audio_status(audio["id"])
            if status_info.get("status") == "done" and status_info.get("has_result_json"):
                ready_audios.append(audio)
        except Exception:
            pass

    return ready_audios


# ---------------------------------------------------------------------------
# Mapper: JSON da API → CSV Sincronizado
#
# O result_json do worker tem a seguinte estrutura:
# Se devaice_result é dict (caso normal):
#   { "vad": [...], "expressionLarge": {...}, "prosody": {...}, "asr": {...},
#     "whisper": { "segments": [...], "text": "...", ... } }
#
# Se devaice_result não é dict (raro):
#   { "devaice": <valor>, "whisper": {...} }
# ---------------------------------------------------------------------------

def map_api_result_to_sincronizado_csv(result: Dict, session_id: str) -> bytes:
    """
    Converte o JSON de resultado da API (DevAIce + Whisper) em um CSV
    no formato "Sincronizado" que o Dashboard entende nativamente.

    O CSV resultante contém colunas de VAD (start_s, end_s, duracao_s),
    colunas de transcrição (speakers, timestamp_inicio, texto_transcricao)
    e todas as colunas de features acústicas.
    """
    # --- Extrair VAD do DevAIce ---
    # No caso normal, vad está na raiz do result. No caso raro, dentro de "devaice".
    if "devaice" in result and isinstance(result["devaice"], dict):
        devaice = result["devaice"]
    else:
        devaice = result

    vad_raw = devaice.get("vad", [])
    if not isinstance(vad_raw, list):
        vad_raw = []

    expression_raw = devaice.get("expressionLarge", [])
    if not isinstance(expression_raw, list):
        expression_raw = []

    prosody_raw = devaice.get("prosody", [])
    if not isinstance(prosody_raw, list):
        prosody_raw = []

    vad_rows = []
    for idx, seg in enumerate(vad_raw):
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start", seg.get("begin", 0)))
        end = float(seg.get("end", 0))

        # Obter dados correspondentes de expressão e prosódia
        expr = expression_raw[idx] if idx < len(expression_raw) else {}
        if not isinstance(expr, dict):
            expr = {}

        pros = prosody_raw[idx] if idx < len(prosody_raw) else {}
        if not isinstance(pros, dict):
            pros = {}

        categorical = expr.get("categorical") or {}
        dimensional = expr.get("dimensional") or {}

        f0 = pros.get("f0") or {}
        loudness = pros.get("loudness") or {}

        row = {
            "start_s": round(start, 4),
            "end_s": round(end, 4),
            "duracao_s": round(end - start, 4),
            # Features Acústicas
            "f0_media": f0.get("average"),
            "f0_variacao": f0.get("variation"),
            "f0_min": f0.get("minimum"),
            "f0_max": f0.get("maximum"),
            "loudness_media": loudness.get("average"),
            "loudness_variacao": loudness.get("variation"),
            "speaking_rate": pros.get("speaking_rate"),
            "intonation_score": pros.get("intonation_score"),
            "emocao_angry": categorical.get("angry"),
            "emocao_happy": categorical.get("happy"),
            "emocao_neutral": categorical.get("neutral"),
            "emocao_sad": categorical.get("sad"),
            "dim_arousal": dimensional.get("arousal"),
            "dim_dominance": dimensional.get("dominance"),
            "dim_valence": dimensional.get("valence"),
        }
        vad_rows.append(row)

    # --- Extrair Transcrição (Whisper) ---
    whisper = result.get("whisper") or {}
    if not isinstance(whisper, dict):
        whisper = {}

    segments = whisper.get("segments", [])
    if not isinstance(segments, list):
        segments = []

    tr_rows = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start", 0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        # Whisper com diarização pode ter "speaker", senão genérico
        speaker = seg.get("speaker", "Entrevistado")
        if not speaker:
            speaker = "Entrevistado"
        # Converter segundos para timestamp HH:MM:SS.ss
        hours = int(start // 3600)
        minutes = int((start % 3600) // 60)
        seconds = start % 60
        ts = f"{hours:02d}:{minutes:02d}:{seconds:05.2f}"
        tr_rows.append({
            "speakers": speaker,
            "timestamp_inicio": ts,
            "texto_transcricao": text,
        })

    # Se Whisper não tiver segments mas tiver text, criar uma linha única
    if not tr_rows and whisper.get("text"):
        tr_rows.append({
            "speakers": "Entrevistado",
            "timestamp_inicio": "00:00:00.00",
            "texto_transcricao": str(whisper["text"]).strip(),
        })

    # --- Montar DataFrame unificado ---
    n_vad = len(vad_rows)
    n_tr = len(tr_rows)
    n = max(n_vad, n_tr, 1)

    while len(vad_rows) < n:
        vad_rows.append({
            "start_s": None,
            "end_s": None,
            "duracao_s": None,
            "f0_media": None,
            "f0_variacao": None,
            "f0_min": None,
            "f0_max": None,
            "loudness_media": None,
            "loudness_variacao": None,
            "speaking_rate": None,
            "intonation_score": None,
            "emocao_angry": None,
            "emocao_happy": None,
            "emocao_neutral": None,
            "emocao_sad": None,
            "dim_arousal": None,
            "dim_dominance": None,
            "dim_valence": None,
        })
    while len(tr_rows) < n:
        tr_rows.append({"speakers": None, "timestamp_inicio": None, "texto_transcricao": None})

    rows = []
    for i in range(n):
        row = {}
        row.update(vad_rows[i])
        row.update(tr_rows[i])
        rows.append(row)

    df = pd.DataFrame(rows)

    col_order = ["start_s", "end_s", "duracao_s", "speakers", "timestamp_inicio", "texto_transcricao"]
    existing = [c for c in col_order if c in df.columns]
    extra = [c for c in df.columns if c not in col_order]
    df = df[existing + extra]

    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def map_api_result_to_all_formats(result: Dict, session_id: str) -> tuple[bytes, bytes, bytes]:
    """
    Converte o JSON de resultado da API (DevAIce + Whisper) em três arquivos de bytes:
    1. prosodia_json: Estrutura JSON {"result": {"vad": [...], "expressionLarge": {...}, ...}}
    2. transcricao_csv: Estrutura CSV com colunas [SpeakerName, Timestamp, Text]
    3. sincronizado_csv: Estrutura CSV Sincronizado unificando ambos
    """
    if "devaice" in result and isinstance(result["devaice"], dict):
        devaice = result["devaice"]
    else:
        devaice = result

    # 1. Montar prosodia_json
    prosodia_dict = {
        "result": {
            "vad": devaice.get("vad", []),
            "expressionLarge": devaice.get("expressionLarge", {}),
            "prosody": devaice.get("prosody", {}),
            "asr": devaice.get("asr", {})
        }
    }
    prosodia_json_bytes = json.dumps(prosodia_dict, ensure_ascii=False).encode("utf-8")

    # 2. Montar transcricao_csv (Whisper)
    whisper = result.get("whisper") or {}
    if not isinstance(whisper, dict):
        whisper = {}

    segments = whisper.get("segments", [])
    if not isinstance(segments, list):
        segments = []

    tr_rows = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start", 0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        speaker = seg.get("speaker", "Entrevistado")
        if not speaker:
            speaker = "Entrevistado"

        # Converter segundos para timestamp HH:MM:SS
        hours = int(start // 3600)
        minutes = int((start % 3600) // 60)
        seconds = start % 60
        ts = f"{hours:02d}:{minutes:02d}:{int(seconds):02d}"

        tr_rows.append({
            "SpeakerName": speaker,
            "Timestamp": ts,
            "Text": text
        })

    # Se Whisper não tiver segments mas tiver text
    if not tr_rows and whisper.get("text"):
        tr_rows.append({
            "SpeakerName": "Entrevistado",
            "Timestamp": "00:00:00",
            "Text": str(whisper["text"]).strip()
        })

    # Se não houver transcrição no Whisper, tentar do ASR do DevAIce
    if not tr_rows:
        asr = devaice.get("asr", {})
        asr_text = ""
        if isinstance(asr, dict):
            asr_text = asr.get("transcript") or asr.get("transcription") or ""
        elif isinstance(asr, list) and asr:
            first = asr[0]
            if isinstance(first, dict):
                asr_text = first.get("transcript") or first.get("transcription") or ""
        
        if asr_text:
            tr_rows.append({
                "SpeakerName": "Entrevistado",
                "Timestamp": "00:00:00",
                "Text": str(asr_text).strip()
            })

    if tr_rows:
        df_tr = pd.DataFrame(tr_rows)
    else:
        df_tr = pd.DataFrame(columns=["SpeakerName", "Timestamp", "Text"])

    buf_tr = io.BytesIO()
    df_tr.to_csv(buf_tr, index=False, encoding="utf-8")
    transcricao_csv_bytes = buf_tr.getvalue()

    # 3. Montar sincronizado_csv (usando a função existente)
    sincronizado_csv_bytes = map_api_result_to_sincronizado_csv(result, session_id)

    return prosodia_json_bytes, transcricao_csv_bytes, sincronizado_csv_bytes


# ---------------------------------------------------------------------------
# Helpers de sincronização
# ---------------------------------------------------------------------------

def get_existing_whatsapp_message_ids(project_id: int) -> set:
    """Retorna o conjunto de whatsapp_message_id já salvos no projeto."""
    from utils.prosodia_db import get_audios
    audios = get_audios(project_id)
    return {
        a.get("whatsapp_message_id")
        for a in audios
        if a.get("whatsapp_message_id")
    }


def get_audio_file(audio_id: int, kind: str = "wav") -> bytes:
    """Busca o arquivo de áudio (original ou wav) da API."""
    with _client() as c:
        resp = c.get(f"/audios/{audio_id}/file", params={"kind": kind})
        if resp.status_code == httpx.codes.GONE:
            raise AudioFileUnavailableError(
                "O arquivo de áudio não está mais disponível na API. "
                "Reenvie-o ou recupere-o no serviço de origem."
            )
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------

def _list_contacts_from_api(search: Optional[str] = None, skip: int = 0, limit: int = 100) -> List[Dict]:
    """Lista os contatos cadastrados na API."""
    params = {"skip": skip, "limit": limit}
    if search:
        params["search"] = search
    with _client() as c:
        resp = c.get("/contacts", params=params)
        resp.raise_for_status()
        return resp.json()


def _create_contact_from_api(phone: str, name: Optional[str] = None) -> Dict:
    """Cria um novo contato na API."""
    body = {"phone": phone}
    if name:
        body["name"] = name
    with _client() as c:
        resp = c.post("/contacts", json=body)
        resp.raise_for_status()
        return resp.json()


def _delete_contact_from_api(contact_id: int) -> None:
    """Exclui um contato da API pelo ID."""
    with _client() as c:
        resp = c.delete(f"/contacts/{contact_id}")
        resp.raise_for_status()


def _import_contacts_csv_from_api(csv_bytes: bytes) -> Dict:
    """Importa contatos a partir de um arquivo CSV (formato com colunas phone, name)."""
    with _client() as c:
        files = {"file": ("contacts.csv", csv_bytes, "text/csv")}
        resp = c.post("/contacts/import", files=files)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Campanhas
# ---------------------------------------------------------------------------

def _create_campaign_from_api(name: str, template_name: str, language_code: str, contact_ids: List[int], project_id: Optional[int] = None) -> Dict:
    """Cria uma nova campanha e inicia os envios para a lista de contatos, vinculando a um projeto na API se fornecido."""
    body = {
        "name": name,
        "template_name": template_name,
        "language_code": language_code,
        "contact_ids": contact_ids,
    }
    url = f"/projects/{project_id}/campaigns" if project_id is not None else "/campaigns"
    with _client() as c:
        resp = c.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Jobs de Processamento
# ---------------------------------------------------------------------------

def _list_jobs_from_api(audio_id: Optional[int] = None, status: Optional[str] = None, skip: int = 0, limit: int = 100) -> List[Dict]:
    """Lista os jobs de processamento de áudio cadastrados na API."""
    params = {"skip": skip, "limit": limit}
    if audio_id is not None:
        params["audio_id"] = audio_id
    if status:
        params["status"] = status
    with _client() as c:
        resp = c.get("/jobs", params=params)
        resp.raise_for_status()
        return resp.json()


def get_job(job_id: int) -> Dict:
    """Retorna detalhes de um job pelo ID."""
    _require_owned_resource("whatsapp_job", job_id)
    with _client() as c:
        resp = c.get(f"/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()


def reprocess_audio(audio_id: int) -> Dict:
    """Solicita o reprocessamento de um áudio na API (DevAIce + Whisper)."""
    _require_owned_resource("whatsapp_audio", audio_id)
    with _client() as c:
        resp = c.post(f"/audios/{audio_id}/reprocess")
        resp.raise_for_status()
        return resp.json()

