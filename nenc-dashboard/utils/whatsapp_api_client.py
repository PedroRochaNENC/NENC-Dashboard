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

import bisect
import io
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import urllib.parse
import httpx
import pandas as pd
from dotenv import load_dotenv

from utils import auth
from utils.prosodia_db import DEFAULT_QR_VERIFICATION_TEXT
from utils.organization_data import (
    claim_external_resource,
    list_external_resources,
    register_derived_external_resource,
    release_external_resource,
)


_APPLICATION_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_WORKSPACE_ENV_PATH = _APPLICATION_ENV_PATH.parent.parent / ".env"
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


def _require_write() -> None:
    """Guarda de escrita para as chamadas que alteram estado na API externa.

    O registro de posse (claim) roda tambem em caminhos de leitura e por isso
    fica de fora: aqui so entram as operacoes que criam, alteram ou removem.
    """

    auth.assert_module_write("prosodia")


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

    _require_write()
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

    _require_write()
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

    _require_write()
    _require_owned_resource("whatsapp_contact", contact_id)
    _delete_contact_from_api(contact_id)
    release_external_resource("whatsapp_contact", contact_id)


def import_owned_contacts_csv(
    content: bytes, phones: Iterable[Any]
) -> tuple[Dict, int, int]:
    """Import contacts and register only IDs that appeared after this import."""

    _require_write()
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
    raw = os.getenv("WHATSAPP_API_URL", "").strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1].strip()
    return raw.rstrip("/")


def _get_api_key() -> str:
    """Retorna a chave da API de WhatsApp configurada no ambiente do servidor."""
    raw = os.getenv("WHATSAPP_API_KEY", "").strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1].strip()
    return raw


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


def _client(timeout: float = 30.0, authorize_audio_files: bool = True) -> httpx.Client:
    """Cria um httpx.Client configurado com timeout e autenticação.

    `authorize_audio_files=False` so serve a quem ja conferiu a posse do audio
    antes: ver `authorize_audio_file_download`.
    """
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
        event_hooks={
            "request": [_authorize_audio_file_request] if authorize_audio_files else []
        },
    )


# ---------------------------------------------------------------------------
# Teste de conectividade
# ---------------------------------------------------------------------------

def test_connection() -> tuple[bool, str]:
    """Testa a conexão com a API e a validade da chave de autenticação. Retorna (sucesso, mensagem)."""
    try:
        with _client() as c:
            resp_health = c.get("/health")
            resp_health.raise_for_status()

            resp_auth = c.get("/campaigns", params={"limit": 1})
            if resp_auth.status_code in (401, 403):
                return False, "Servidor alcançado, mas a Chave de API (X-API-Key) foi recusada pelo servidor (HTTP 403 Forbidden)."
            resp_auth.raise_for_status()
            return True, "Conexão e autenticação OK"
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            return False, "Chave de API (X-API-Key) recusada pelo servidor (HTTP 403 Forbidden)."
        return False, f"Erro HTTP {e.response.status_code}: {e.response.text or str(e)}"
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
        if normalized_phone not in contact_ids_by_phone and not auth.is_current_user_platform_admin():
            raise auth.AuthorizationError("O telefone nao pertence a organizacao ativa.")
        params["phone"] = normalized_phone
        parent_resource_type = "whatsapp_contact"
        parent_resource_id = contact_ids_by_phone.get(normalized_phone, f"admin_{normalized_phone}")
    else:
        if auth.is_current_user_platform_admin():
            parent_resource_type = "whatsapp_api_project"
            parent_resource_id = 0
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
# Mensagens recebidas
#
# A API guarda toda mensagem de texto que trazia um codigo de projeto. Quem ve o
# que e decidido aqui: mensagem cujo codigo resolveu para projeto da organizacao
# ativa, e nada mais. As de codigo invalido nao resolveram para projeto nenhum,
# logo nao pertencem a organizacao alguma, e ficam so para o administrador da
# plataforma.
# ---------------------------------------------------------------------------

def get_inbound_messages(project_id: int, limit: int = 200) -> List[Dict]:
    """Mensagens de codigo de um projeto da API pertencente a organizacao ativa."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get("/inbound-messages", params={"project_id": project_id, "limit": limit})
        resp.raise_for_status()
        return resp.json()


def get_unmatched_inbound_messages(limit: int = 200) -> List[Dict]:
    """Mensagens cujo codigo nao resolveu para projeto algum.

    Levanta antes de sair da maquina: sem projeto nao ha como dizer de quem e a
    mensagem, entao esconder o botao na interface nao serviria de autorizacao.
    """
    if not auth.is_current_user_platform_admin():
        raise auth.AuthorizationError(
            "Mensagens de codigo invalido sao visiveis apenas para o administrador global."
        )
    with _client() as c:
        resp = c.get("/inbound-messages", params={"unmatched": "true", "limit": limit})
        resp.raise_for_status()
        return resp.json()


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
    _require_write()
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
    _require_write()
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
    _require_write()
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
    _require_write()
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
    _require_write()
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
# Mapper: JSON da API → CSVs do Dashboard
#
# O result_json do worker tem a seguinte estrutura:
# Se devaice_result é dict (caso normal):
#   { "vad": [...], "expressionLarge": {...}, "prosody": {...}, "asr": {...},
#     "whisper": { "segments": [...], "text": "...", ... },
#     "text_sentiment": { "status": ..., "segments": [...] },   # desde 09/2026
#     "segmentacao": { "cortado": true, "trecho_s": 60 } }      # só áudio longo
#
# Se devaice_result não é dict (raro):
#   { "devaice": <valor>, "whisper": {...}, ... }
#
# A linha i do VAD (DevAIce) e o segmento i do Whisper NÃO são o mesmo trecho:
# são duas segmentações independentes do mesmo áudio. O sincronizado casa cada
# linha do VAD com o segmento do Whisper pelo tempo, nunca pela posição.
# ---------------------------------------------------------------------------

# Sem sobreposição, a linha do VAD fica com o segmento mais próximo até esta
# distância (segundos); além dela, fica sem fala.
TOLERANCIA_ALINHAMENTO_S = 0.5

# Mesmo limiar da API: acima dele o áudio é analisado em trechos de ~60 s, e o
# Whisper recomeça os rótulos de locutor (A, B...) a cada trecho.
LIMIAR_AUDIO_CORTADO_S = 900.0

# |nota| abaixo disto é neutro. Vale só quando o resultado não traz o rótulo.
FAIXA_NEUTRA_SENTIMENTO = 0.2

COLUNAS_SENTIMENTO = ["sentimento_texto", "sentimento_rotulo", "sentimento_justificativa"]

# O que vai para a base de conhecimento: a fala, sem as inferências sobre ela.
COLUNAS_TRANSCRICAO_KB = ["SpeakerName", "Timestamp", "Text"]

COLUNAS_TRANSCRICAO = COLUNAS_TRANSCRICAO_KB + [
    "start_s",
    "end_s",
    "segmento_idx",
] + COLUNAS_SENTIMENTO

_COLUNAS_VAD_ACUSTICAS = [
    "start_s", "end_s", "duracao_s",
    "f0_media", "f0_variacao", "f0_min", "f0_max",
    "loudness_media", "loudness_variacao",
    "speaking_rate", "intonation_score",
    "emocao_angry", "emocao_happy", "emocao_neutral", "emocao_sad",
    "dim_arousal", "dim_dominance", "dim_valence",
]


def _numero(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _formatar_timestamp(seconds: float, casas: int = 0) -> str:
    """Segundos em HH:MM:SS (ou HH:MM:SS.ss com casas=2)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    if casas:
        return f"{hours:02d}:{minutes:02d}:{rest:0{3 + casas}.{casas}f}"
    return f"{hours:02d}:{minutes:02d}:{int(rest):02d}"


def rotulo_sentimento(score: Optional[float], faixa_neutra: float = FAIXA_NEUTRA_SENTIMENTO) -> Optional[str]:
    if score is None:
        return None
    if score >= faixa_neutra:
        return "positivo"
    if score <= -faixa_neutra:
        return "negativo"
    return "neutro"


def _devaice(result: Dict) -> Dict:
    # No caso normal, vad está na raiz do result. No caso raro, dentro de "devaice".
    if "devaice" in result and isinstance(result["devaice"], dict):
        return result["devaice"]
    return result


def _whisper(result: Dict) -> Dict:
    whisper = result.get("whisper") or {}
    return whisper if isinstance(whisper, dict) else {}


def _segmentos_whisper(result: Dict) -> List[Dict]:
    """Segmentos do Whisper com o índice original de `whisper.segments`.

    O índice é a chave que liga o segmento à nota de sentimento da API. Início
    ou fim nulo não derruba a importação: o início herda o fim do segmento
    anterior (o Whisper devolve em ordem) e o fim, o início.
    """
    segments = _whisper(result).get("segments")
    saida = []
    fim_anterior = 0.0
    for index, seg in enumerate(segments if isinstance(segments, list) else []):
        if not isinstance(seg, dict):
            continue
        start = _numero(seg.get("start"))
        end = _numero(seg.get("end"))
        if start is None:
            start = fim_anterior
        if end is None or end < start:
            end = start
        fim_anterior = end
        speaker = str(seg.get("speaker") or "").strip() or "Entrevistado"
        saida.append({
            "index": index,
            "start": start,
            "end": end,
            "speaker": speaker,
            "text": str(seg.get("text") or "").strip(),
        })
    return saida


def _sentimento_por_indice(result: Dict) -> Dict[int, Dict[str, Any]]:
    """Nota de sentimento do texto por índice de segmento do Whisper.

    Só vale o bloco `done` ou `partial` cujo `n_segments` bate com os segmentos
    do Whisper deste mesmo resultado: se a transcrição mudou, os índices seriam
    de outra.
    """
    bloco = result.get("text_sentiment")
    if not isinstance(bloco, dict) or bloco.get("status") not in ("done", "partial"):
        return {}
    segments = _whisper(result).get("segments")
    if not isinstance(segments, list) or bloco.get("n_segments") != len(segments):
        return {}
    faixa = _numero(bloco.get("faixa_neutra"))
    faixa = FAIXA_NEUTRA_SENTIMENTO if faixa is None else faixa

    notas: Dict[int, Dict[str, Any]] = {}
    for item in bloco.get("segments") or []:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = _numero(item.get("score"))
        if isinstance(index, bool) or not isinstance(index, int) or score is None:
            continue
        notas[index] = {
            "sentimento_texto": round(score, 3),
            "sentimento_rotulo": item.get("label") or rotulo_sentimento(score, faixa),
            "sentimento_justificativa": str(item.get("justificativa") or "").strip() or None,
        }
    return notas


def _audio_cortado(result: Dict, segmentos: List[Dict], vad_rows: List[Dict]) -> bool:
    """Áudio analisado em trechos pela API: rótulos de locutor por trecho.

    A marca `segmentacao` vem da API; resultados de antes dela são
    reconhecidos pela duração.
    """
    segmentacao = result.get("segmentacao")
    if isinstance(segmentacao, dict) and segmentacao.get("cortado"):
        return True
    fins = [s["end"] for s in segmentos] + [
        r["end_s"] for r in vad_rows if r.get("end_s") is not None
    ]
    return max(fins, default=0.0) > LIMIAR_AUDIO_CORTADO_S


def alinhar_vad_aos_segmentos(
    vad: List[tuple],
    segmentos: List[tuple],
    tol: float = TOLERANCIA_ALINHAMENTO_S,
) -> List[Optional[int]]:
    """Para cada intervalo do VAD, a posição do segmento que o cobre no tempo.

    `vad` e `segmentos` são listas de (início, fim) em segundos. Vence o
    segmento de maior sobreposição (empate: o de menor posição). Sem
    sobreposição, o de menor distância até `tol`; além disso, None.
    """
    ordem = sorted(range(len(segmentos)), key=lambda i: (segmentos[i][0], segmentos[i][1], i))
    inicios = [segmentos[i][0] for i in ordem]
    # Maior fim entre as posições 0..k da ordem: limita a varredura para trás.
    maior_fim = []
    acumulado = float("-inf")
    for i in ordem:
        acumulado = max(acumulado, segmentos[i][1])
        maior_fim.append(acumulado)

    resultado: List[Optional[int]] = []
    for inicio, fim in vad:
        if inicio is None or fim is None:
            resultado.append(None)
            continue
        melhor = None  # (sobreposição, -posição)
        mais_proximo = None  # (distância, posição)
        k = bisect.bisect_right(inicios, fim + tol) - 1
        while k >= 0 and maior_fim[k] >= inicio - tol:
            posicao = ordem[k]
            seg_inicio, seg_fim = segmentos[posicao]
            sobreposicao = min(fim, seg_fim) - max(inicio, seg_inicio)
            if sobreposicao > 0:
                if melhor is None or (sobreposicao, -posicao) > melhor:
                    melhor = (sobreposicao, -posicao)
            else:
                distancia = max(seg_inicio - fim, inicio - seg_fim, 0.0)
                if distancia <= tol and (mais_proximo is None or (distancia, posicao) < mais_proximo):
                    mais_proximo = (distancia, posicao)
            k -= 1
        if melhor is not None:
            resultado.append(-melhor[1])
        elif mais_proximo is not None:
            resultado.append(mais_proximo[1])
        else:
            resultado.append(None)
    return resultado


def _vad_rows(devaice: Dict) -> List[Dict]:
    """Uma linha por segmento do VAD, com as features da DevAIce do mesmo índice."""
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
        start = _numero(seg.get("start", seg.get("begin"))) or 0.0
        end = _numero(seg.get("end")) or 0.0

        # A DevAIce devolve expressão e prosódia na mesma ordem do VAD.
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

        vad_rows.append({
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
        })
    return vad_rows


def _colunas_do_segmento(seg: Optional[Dict], sentimento: Dict[int, Dict]) -> Dict[str, Any]:
    if seg is None:
        return {
            "speakers": None,
            "timestamp_inicio": None,
            "texto_transcricao": None,
            "segmento_idx": None,
            **{coluna: None for coluna in COLUNAS_SENTIMENTO},
        }
    nota = sentimento.get(seg["index"], {})
    return {
        "speakers": seg["speaker"],
        "timestamp_inicio": _formatar_timestamp(seg["start"], casas=2),
        "texto_transcricao": seg["text"],
        "segmento_idx": seg["index"],
        **{coluna: nota.get(coluna) for coluna in COLUNAS_SENTIMENTO},
    }


def map_api_result_to_sincronizado_csv(result: Dict, session_id: str) -> bytes:
    """
    Converte o JSON de resultado da API (DevAIce + Whisper) em um CSV
    no formato "Sincronizado" que o Dashboard entende nativamente.

    Uma linha por segmento do VAD: colunas de VAD (start_s, end_s, duracao_s),
    as features acústicas e a fala do segmento do Whisper que cobre aquele
    trecho no tempo (speakers, timestamp_inicio, texto_transcricao), com o
    índice do segmento (segmento_idx) e o sentimento do texto dele. Vários
    segmentos do VAD podem cair no mesmo segmento do Whisper; quem precisar
    da fala uma vez só deduplica por segmento_idx. Sem VAD, uma linha por
    segmento do Whisper, sem features.
    """
    devaice = _devaice(result)
    vad_rows = _vad_rows(devaice)
    segmentos = _segmentos_whisper(result)
    com_texto = [s for s in segmentos if s["text"]]
    sentimento = _sentimento_por_indice(result)
    cortado = _audio_cortado(result, segmentos, vad_rows)

    rows = []
    if vad_rows:
        posicoes = alinhar_vad_aos_segmentos(
            [(r["start_s"], r["end_s"]) for r in vad_rows],
            [(s["start"], s["end"]) for s in com_texto],
        )
        for vad_row, posicao in zip(vad_rows, posicoes):
            seg = com_texto[posicao] if posicao is not None else None
            rows.append({**vad_row, **_colunas_do_segmento(seg, sentimento)})
        # Whisper sem segmentos (modelo sem timestamps): o texto inteiro fica
        # na primeira linha, como sempre ficou.
        texto = str(_whisper(result).get("text") or "").strip()
        if not com_texto and texto:
            rows[0].update({"speakers": "Entrevistado", "timestamp_inicio": "00:00:00.00",
                            "texto_transcricao": texto})
    else:
        vazio = {coluna: None for coluna in _COLUNAS_VAD_ACUSTICAS}
        for seg in com_texto:
            rows.append({**vazio, **_colunas_do_segmento(seg, sentimento)})
        texto = str(_whisper(result).get("text") or "").strip()
        if not rows:
            rows.append({**vazio, **_colunas_do_segmento(None, sentimento)})
            if texto:
                rows[0].update({"speakers": "Entrevistado", "timestamp_inicio": "00:00:00.00",
                                "texto_transcricao": texto})

    for row in rows:
        row["audio_cortado"] = cortado

    df = pd.DataFrame(rows)
    df["segmento_idx"] = df["segmento_idx"].astype("Int64")

    col_order = ["start_s", "end_s", "duracao_s", "speakers", "timestamp_inicio", "texto_transcricao"]
    existing = [c for c in col_order if c in df.columns]
    extra = [c for c in df.columns if c not in col_order]
    df = df[existing + extra]

    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def _transcricao_rows(result: Dict, devaice: Dict) -> List[Dict]:
    """Uma linha por segmento do Whisper com fala; sem segmentos, o texto inteiro."""
    sentimento = _sentimento_por_indice(result)
    tr_rows = []
    for seg in _segmentos_whisper(result):
        if not seg["text"]:
            continue
        nota = sentimento.get(seg["index"], {})
        tr_rows.append({
            "SpeakerName": seg["speaker"],
            "Timestamp": _formatar_timestamp(seg["start"]),
            "Text": seg["text"],
            "start_s": round(seg["start"], 3),
            "end_s": round(seg["end"], 3),
            "segmento_idx": seg["index"],
            **{coluna: nota.get(coluna) for coluna in COLUNAS_SENTIMENTO},
        })

    # Se Whisper não tiver segments mas tiver text
    whisper = _whisper(result)
    if not tr_rows and whisper.get("text"):
        tr_rows.append({
            "SpeakerName": "Entrevistado",
            "Timestamp": "00:00:00",
            "Text": str(whisper["text"]).strip(),
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
                "Text": str(asr_text).strip(),
            })
    return tr_rows


def map_api_result_to_all_formats(result: Dict, session_id: str) -> tuple[bytes, bytes, bytes]:
    """
    Converte o JSON de resultado da API (DevAIce + Whisper) em três arquivos de bytes:
    1. prosodia_json: Estrutura JSON {"result": {"vad": [...], "expressionLarge": {...}, ...}}
    2. transcricao_csv: CSV com SpeakerName, Timestamp, Text na frente, seguidas de
       start_s, end_s, segmento_idx e do sentimento do texto de cada segmento
    3. sincronizado_csv: Estrutura CSV Sincronizado unificando ambos
    """
    devaice = _devaice(result)

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
    df_tr = pd.DataFrame(_transcricao_rows(result, devaice), columns=COLUNAS_TRANSCRICAO)
    df_tr["segmento_idx"] = df_tr["segmento_idx"].astype("Int64")

    buf_tr = io.BytesIO()
    df_tr.to_csv(buf_tr, index=False, encoding="utf-8")
    transcricao_csv_bytes = buf_tr.getvalue()

    # 3. Montar sincronizado_csv (usando a função existente)
    sincronizado_csv_bytes = map_api_result_to_sincronizado_csv(result, session_id)

    return prosodia_json_bytes, transcricao_csv_bytes, sincronizado_csv_bytes


def transcricao_para_base_conhecimento(csv_bytes: Optional[bytes]) -> Optional[bytes]:
    """O CSV de transcrição só com locutor, tempo e fala, para a base de conhecimento.

    O CSV passou a levar a nota e a justificativa de sentimento de cada trecho.
    A base de conhecimento existe para a IA citar o que foi dito; a inferência
    sobre a fala não precisa sair junto para a OpenAI (minimização de dados).
    CSV que não abre, ou sem nenhuma das três colunas, segue como veio.
    """
    if not csv_bytes:
        return csv_bytes
    try:
        df = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False)
    except Exception:
        return csv_bytes
    df.columns = [str(c).strip() for c in df.columns]
    colunas = [c for c in COLUNAS_TRANSCRICAO_KB if c in df.columns]
    if not colunas or len(colunas) == len(df.columns):
        return csv_bytes
    buf = io.BytesIO()
    df[colunas].to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def atualizar_conteudo_audios_importados(audios: Iterable[Dict]) -> Dict[str, Any]:
    """Rebaixa da API o resultado dos áudios já importados e regrava os blobs.

    Leva para os áudios antigos o que mudou sem nova análise: o casamento VAD ×
    transcrição pelo tempo e o sentimento do texto que a API calculou depois.
    Não pede reprocessamento, não chama IA e não refaz a verificação de
    qualidade: troca prosódia, transcrição e sincronizado e recalcula os
    momentos de maior ativação, que saem do sincronizado.

    Áudios que não vieram da API, ou que ainda estão em processamento ou
    falharam, ficam como estão (a sincronização normal cuida deles).
    """
    from utils.prosodia_db import save_high_activations, update_audio_content
    from utils.prosodia_loader import normalizar_sincronizado
    from utils.prosodia_signals import momentos_alta_ativacao

    resumo: Dict[str, Any] = {"atualizados": 0, "ignorados": 0, "falhas": []}
    for audio in audios:
        session_id = audio.get("session_id")
        api_audio_id = api_audio_id_from_session(session_id)
        if api_audio_id is None or audio.get("quality_status") in (
            "pending", "processing", "running", "failed"
        ):
            resumo["ignorados"] += 1
            continue
        try:
            result = get_audio_result(api_audio_id)
            if not result:
                resumo["ignorados"] += 1
                continue
            json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result, session_id)
            update_audio_content(audio["id"], json_bytes, csv_bytes, sinc_bytes)
            sinc_df = normalizar_sincronizado(pd.read_csv(io.BytesIO(sinc_bytes)), session_id)
            save_high_activations(audio["id"], momentos_alta_ativacao(sinc_df))
            resumo["atualizados"] += 1
        except Exception as exc:  # um áudio com problema não para os demais
            resumo["falhas"].append((session_id, str(exc) or type(exc).__name__))
    return resumo


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
        return _read_audio_file(c, audio_id, kind)


def authorize_audio_file_download(audio_id: int, kind: str = "wav") -> Callable[[], bytes]:
    """Confere agora a posse do áudio e devolve a função que baixa o arquivo.

    A posse é conferida pelo login guardado em st.session_state, que só existe
    na thread do script. Numa thread criada pela página, o gancho de _client
    levantava antes de a requisição sair e o download nunca acontecia. Chame
    esta função na thread do script; a função devolvida roda em qualquer thread.
    """
    _require_owned_resource("whatsapp_audio", audio_id)

    def download() -> bytes:
        # A posse ja foi conferida acima, com a sessao disponivel.
        with _client(authorize_audio_files=False) as c:
            return _read_audio_file(c, audio_id, kind)

    return download


# ---------------------------------------------------------------------------
# Exclusão do áudio na API junto com a entrevista
#
# Sem apagar na API, a próxima sincronização reimporta a entrevista excluída.
# Mas o id do áudio vem do fim do session_id, e parte do acervo foi importada de
# uma instância anterior da API, com numeração própria: o mesmo id hoje pode ser
# a gravação de outra pessoa. Só se apaga na API o que se confirma ser o mesmo
# áudio.
# ---------------------------------------------------------------------------

_API_AUDIO_ID_IN_SESSION = re.compile(r"^wa_.*_(\d+)$")

# created_at local e gravado no fuso do servidor; received_at da API, em UTC.
_IMPORT_CLOCK_TOLERANCE = timedelta(days=1)


def api_audio_id_from_session(session_id: Any) -> Optional[int]:
    """Id do áudio na API a partir do session_id da importação (wa_<tel>_<id>, wa_upload_<id>)."""
    match = _API_AUDIO_ID_IN_SESSION.match(str(session_id or ""))
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class ApiAudioDeletion:
    """O que a exclusão da entrevista faz com o áudio na API."""

    api_audio_id: Optional[int]
    delete_in_api: bool
    reason: str = ""  # por que o áudio fica na API; vazio quando é excluído


def _naive_utc(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def decide_api_audio_deletion(
    interview: Dict, api_audio: Optional[Dict], api_project_id: Any
) -> ApiAudioDeletion:
    """Decide se o áudio da API sai junto com a entrevista. Função pura.

    `interview` é a referência de `prosodia_db.get_audio_deletion_reference`;
    `api_audio`, o GET /audios/{id} (None quando a API responde 404).
    """

    session_id = str(interview.get("session_id") or "")
    api_audio_id = api_audio_id_from_session(session_id)

    def keep(reason: str) -> ApiAudioDeletion:
        return ApiAudioDeletion(api_audio_id, False, reason)

    if api_audio_id is None:
        return keep("este áudio não veio da API de WhatsApp.")
    if interview.get("other_interviews"):
        return keep("outra importação ainda usa este áudio.")
    if api_audio is None:
        return keep("o áudio já não existe na API.")

    other_recording = (
        "o áudio com este número na API é outra gravação "
        "(este foi importado de uma instância anterior da API)."
    )
    local_message = str(interview.get("whatsapp_message_id") or "").strip()
    api_message = str(api_audio.get("whatsapp_message_id") or "").strip()
    if local_message:
        if api_message != local_message:
            return keep(other_recording)
        return ApiAudioDeletion(api_audio_id, True)

    # Upload feito pela API: não há mensagem para comparar.
    if not session_id.startswith("wa_upload_"):
        return keep("não há como confirmar que o áudio na API é este mesmo.")
    if api_message or api_audio.get("source") != "upload":
        return keep(other_recording)
    if str(api_audio.get("project_id")) != str(api_project_id):
        return keep("o áudio com este número na API pertence a outro projeto.")
    imported_at = _naive_utc(interview.get("created_at"))
    received_at = _naive_utc(api_audio.get("received_at"))
    if imported_at is None or received_at is None:
        return keep("não há como confirmar que o áudio na API é este mesmo.")
    # Uma entrevista não pode ter sido importada antes de a API receber o áudio.
    if imported_at < received_at - _IMPORT_CLOCK_TOLERANCE:
        return keep(other_recording)
    return ApiAudioDeletion(api_audio_id, True)


def plan_api_audio_deletion(interview: Dict, api_project_id: Any) -> ApiAudioDeletion:
    """Consulta a API e aplica `decide_api_audio_deletion`.

    Levanta se a API não responder: sem confirmar o áudio não dá para excluir
    só a entrevista, que a próxima sincronização traria de volta.
    """

    api_audio_id = api_audio_id_from_session(interview.get("session_id"))
    if api_audio_id is None or interview.get("other_interviews"):
        return decide_api_audio_deletion(interview, None, api_project_id)
    if not is_configured():
        return ApiAudioDeletion(api_audio_id, False, "a API de WhatsApp não está configurada.")
    try:
        _require_owned_resource("whatsapp_audio", api_audio_id)
    except auth.AuthorizationError:
        return ApiAudioDeletion(
            api_audio_id, False, "o áudio na API não pertence à organização ativa."
        )
    with _client() as c:
        resp = c.get(f"/audios/{api_audio_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            api_audio = None
        else:
            resp.raise_for_status()
            api_audio = resp.json()
    return decide_api_audio_deletion(interview, api_audio, api_project_id)


def delete_api_audio(api_audio_id: int) -> None:
    """Exclui o áudio na API: registro, jobs de análise, arquivo original e WAV.

    404 conta como sucesso: o áudio já não estava lá. Qualquer outra falha
    levanta, para quem chama não seguir com a exclusão local.
    """

    _require_write()
    _require_owned_resource("whatsapp_audio", api_audio_id)
    with _client() as c:
        resp = c.delete(f"/audios/{api_audio_id}")
        if resp.status_code != httpx.codes.NOT_FOUND:
            resp.raise_for_status()
    try:
        release_external_resource("whatsapp_audio", api_audio_id)
    except auth.AuthorizationError:
        # Registro de outra organização (administrador em "Todas"): o áudio já
        # saiu da API, e um registro apontando para o nada não autoriza nada.
        pass


def _read_audio_file(c: httpx.Client, audio_id: int, kind: str) -> bytes:
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
    _require_write()
    _require_owned_resource("whatsapp_audio", audio_id)
    with _client() as c:
        resp = c.post(f"/audios/{audio_id}/reprocess")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Gestão de QR Codes & Links WhatsApp
# ---------------------------------------------------------------------------

def get_project_qr_codes(project_id: int) -> List[Dict]:
    """Lista QR codes cadastrados para um projeto na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get(f"/projects/{project_id}/qr-codes")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()


def get_project_participations(project_id: int) -> List[Dict]:
    """Lista inscrições/participantes registradas no projeto via QR code na API."""
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.get(f"/projects/{project_id}/participations")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()


def suggest_next_qr_code(project_id: int, qr_codes: List[Dict]) -> str:
    """Proximo codigo de rastreio livre, a partir do maior sufixo ja usado.

    Contar os QR codes existentes nao serve: apagar um do meio faz a sugestao
    cair sobre um codigo que ainda existe, e a API recusa com 400. Como o campo
    do formulario e desabilitado, nao havia como corrigir pela tela.
    """
    prefix = f"{project_id:02d}-"
    highest = 0
    for qr in qr_codes:
        code = str(qr.get("code") or "")
        suffix = code[len(prefix):]
        if code.startswith(prefix) and suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:02d}"


def create_project_qr_code(
    project_id: int,
    name: str,
    description: Optional[str] = None,
    code: Optional[str] = None,
    target_phone: Optional[str] = None,
    welcome_message: Optional[str] = None,
    verification_text: Optional[str] = None,
) -> Dict:
    """Cria um novo QR Code associado a um projeto na API."""
    _require_write()
    _require_owned_resource("whatsapp_api_project", project_id)
    body = {
        "name": name,
        "description": description,
        "code": code,
        "target_phone": target_phone,
        "welcome_message": welcome_message,
        "verification_text": verification_text,
    }
    body = {k: v for k, v in body.items() if v is not None}
    with _client() as c:
        resp = c.post(f"/projects/{project_id}/qr-codes", json=body)
        resp.raise_for_status()
        return resp.json()


def update_project_qr_code(
    project_id: int,
    qr_id: Any,
    name: Optional[str] = None,
    description: Optional[str] = None,
    target_phone: Optional[str] = None,
    welcome_message: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict:
    """Atualiza um QR Code existente na API (ex.: repontar para outro número de WhatsApp)."""
    _require_write()
    _require_owned_resource("whatsapp_api_project", project_id)
    body = {
        "name": name,
        "description": description,
        "target_phone": target_phone,
        "welcome_message": welcome_message,
        "status": status,
    }
    body = {k: v for k, v in body.items() if v is not None}
    with _client() as c:
        resp = c.patch(f"/projects/{project_id}/qr-codes/{qr_id}", json=body)
        resp.raise_for_status()
        return resp.json()


def delete_project_qr_code(project_id: int, qr_id: Any) -> None:
    """Exclui um QR Code do projeto na API."""
    _require_write()
    _require_owned_resource("whatsapp_api_project", project_id)
    with _client() as c:
        resp = c.delete(f"/projects/{project_id}/qr-codes/{qr_id}")
        resp.raise_for_status()


def build_whatsapp_deeplink(phone: str, verification_text: str, code: str = "") -> str:
    """Gera o link pré-preenchido do WhatsApp (https://wa.me/...) com o texto de verificação e código."""
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    text = (verification_text or DEFAULT_QR_VERIFICATION_TEXT).strip()
    if code and code.strip():
        text = f"{text}\n\n[Código: {code.strip()}]"
    encoded_text = urllib.parse.quote(text)
    return f"https://wa.me/{clean_phone}?text={encoded_text}"


def generate_qr_code_bytes(data_url: str) -> bytes:
    """Gera a imagem PNG em bytes a partir de uma URL/texto para exibição ou download."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(data_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as error:
        # Sem fallback externo de proposito: o deeplink carrega o telefone da
        # organizacao e o codigo de verificacao do participante, e enviá-lo a um
        # servico publico de terceiros vazaria esses dados.
        raise RuntimeError(
            "Nao foi possivel gerar o QR Code localmente. "
            "Verifique se as dependencias 'qrcode' e 'pillow' estao instaladas."
        ) from error


