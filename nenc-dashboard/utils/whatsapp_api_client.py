"""
WhatsApp API Client — Comunica com a NencProsodiaWhatsapp-API.

Funções para buscar campanhas, contatos e áudios processados,
além de converter os resultados da API nos CSVs esperados pelo Dashboard.

NOTA IMPORTANTE: Na API, os áudios são recebidos via webhook do WhatsApp
e NÃO estão vinculados a campanhas. Qualquer pessoa que envie um áudio
ao número configurado terá seu áudio registrado, independente de campanhas.
A sincronização pode ser feita por:
  - Todos os áudios (sem filtro)
  - Por telefone (filtrando por contato)
  - Por campanha (buscando os telefones da campanha e filtrando)
"""

import io
import json
import os
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Credenciais
# ---------------------------------------------------------------------------

def _get_api_url() -> str:
    """Retorna a URL da API de WhatsApp (session_state > .env > fallback)."""
    return (
        st.session_state.get("whatsapp_api_url")
        or os.getenv("WHATSAPP_API_URL", "")
    ).rstrip("/")


def _get_api_key() -> str:
    """Retorna a chave da API de WhatsApp (session_state > .env > fallback)."""
    return (
        st.session_state.get("whatsapp_api_key")
        or os.getenv("WHATSAPP_API_KEY", "")
    )


def is_configured() -> bool:
    """Verifica se as credenciais da API estão preenchidas."""
    return bool(_get_api_url()) and bool(_get_api_key())


def _client() -> httpx.Client:
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
        timeout=30.0,
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

def get_campaigns() -> List[Dict]:
    """Lista todas as campanhas disponíveis na API."""
    with _client() as c:
        resp = c.get("/campaigns", params={"limit": 500})
        resp.raise_for_status()
        return resp.json()


def get_campaign(campaign_id: int) -> Dict:
    """Retorna detalhes de uma campanha pelo ID."""
    with _client() as c:
        resp = c.get(f"/campaigns/{campaign_id}")
        resp.raise_for_status()
        return resp.json()


def get_campaign_contacts(campaign_id: int) -> List[Dict]:
    """Lista os contatos (com telefone) vinculados a uma campanha."""
    with _client() as c:
        resp = c.get(f"/campaigns/{campaign_id}/contacts")
        resp.raise_for_status()
        return resp.json()


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

def get_all_audios(phone: Optional[str] = None, limit: int = 500) -> List[Dict]:
    """
    Lista áudios da API, opcionalmente filtrando por telefone.
    Retorna lista de AudioResponse: {id, whatsapp_message_id, contact_phone,
    media_id, local_path, duration_sec, received_at}
    """
    params: Dict[str, Any] = {"limit": limit}
    if phone:
        params["phone"] = phone
    with _client() as c:
        resp = c.get("/audios", params=params)
        resp.raise_for_status()
        return resp.json()


def get_audio_status(audio_id: int) -> Dict:
    """
    Retorna o status de processamento de um áudio.
    Campos relevantes: status, has_result_json, has_transcription
    """
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
    with _client() as c:
        resp = c.get(f"/audios/{audio_id}/transcript")
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Buscador de áudios para sincronização
# ---------------------------------------------------------------------------

def fetch_audios_for_sync(
    campaign_id: Optional[int] = None,
    phones: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Busca áudios da API prontos para sincronização.

    Estratégia:
    - Se campaign_id fornecido: busca contatos da campanha → seus telefones → áudios
    - Se phones fornecido: busca áudios filtrados por esses telefones
    - Se nenhum: busca TODOS os áudios da API

    Retorna apenas áudios cujo processamento está 'done' (tem result_json).
    """
    target_phones: Optional[List[str]] = phones

    # Se tem campanha, buscar telefones dela
    if campaign_id and not target_phones:
        try:
            contacts = get_campaign_contacts(campaign_id)
            target_phones = [c.get("phone") for c in contacts if c.get("phone")]
        except Exception:
            target_phones = None  # fallback: buscar tudo

    # Buscar áudios
    all_audios: List[Dict] = []
    if target_phones:
        for phone in target_phones:
            try:
                audios = get_all_audios(phone=phone)
                all_audios.extend(audios)
            except Exception:
                pass
    else:
        all_audios = get_all_audios()

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
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------

def list_contacts(search: Optional[str] = None, skip: int = 0, limit: int = 100) -> List[Dict]:
    """Lista os contatos cadastrados na API."""
    params = {"skip": skip, "limit": limit}
    if search:
        params["search"] = search
    with _client() as c:
        resp = c.get("/contacts", params=params)
        resp.raise_for_status()
        return resp.json()


def create_contact(phone: str, name: Optional[str] = None) -> Dict:
    """Cria um novo contato na API."""
    body = {"phone": phone}
    if name:
        body["name"] = name
    with _client() as c:
        resp = c.post("/contacts", json=body)
        resp.raise_for_status()
        return resp.json()


def delete_contact(contact_id: int) -> None:
    """Exclui um contato da API pelo ID."""
    with _client() as c:
        resp = c.delete(f"/contacts/{contact_id}")
        resp.raise_for_status()


def import_contacts_csv(csv_bytes: bytes) -> Dict:
    """Importa contatos a partir de um arquivo CSV (formato com colunas phone, name)."""
    with _client() as c:
        files = {"file": ("contacts.csv", csv_bytes, "text/csv")}
        resp = c.post("/contacts/import", files=files)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Campanhas
# ---------------------------------------------------------------------------

def create_campaign(name: str, template_name: str, language_code: str, contact_ids: List[int]) -> Dict:
    """Cria uma nova campanha e inicia os envios para a lista de contatos."""
    body = {
        "name": name,
        "template_name": template_name,
        "language_code": language_code,
        "contact_ids": contact_ids,
    }
    with _client() as c:
        resp = c.post("/campaigns", json=body)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Jobs de Processamento
# ---------------------------------------------------------------------------

def list_jobs(audio_id: Optional[int] = None, status: Optional[str] = None, skip: int = 0, limit: int = 100) -> List[Dict]:
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
    with _client() as c:
        resp = c.get(f"/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()


def reprocess_audio(audio_id: int) -> Dict:
    """Solicita o reprocessamento de um áudio na API (DevAIce + Whisper)."""
    with _client() as c:
        resp = c.post(f"/audios/{audio_id}/reprocess")
        resp.raise_for_status()
        return resp.json()

