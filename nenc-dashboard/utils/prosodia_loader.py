"""
Prosodia Loader — Carrega e valida dados de prosódia (JSON VAD) e transcrições (CSV).

Suporta múltiplos pares de arquivos por sessão:
- JSON: campo result.vad com lista de {start, end}
- CSV: colunas SpeakerName, Timestamp, Text

O session_id é extraído do nome do arquivo:
  Prosodia-35523510_Fim.json  →  35523510_Fim
  Transcricao-35523510_Fim.csv →  35523510_Fim
"""

import json
import io
import re
import pandas as pd
from typing import Dict, List, Optional, Tuple


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _session_id_from_name(name: str) -> str:
    """
    Extrai o session_id do nome do arquivo removendo prefixos conhecidos
    e a extensão.

    Exemplos:
      Prosodia-35523510_Fim.json  →  35523510_Fim
      Transcricao-35523510_Fim.csv →  35523510_Fim
      35523510_Fim.json           →  35523510_Fim
    """
    stem = re.sub(r"\.(json|csv|xlsx)$", "", name, flags=re.IGNORECASE)
    for prefix in ("Prosodia-", "Transcricao-", "prosodia-", "transcricao-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem


def _timestamp_to_seconds(ts: str) -> float:
    """Converte 'HH:MM:SS' ou 'MM:SS' para segundos float."""
    parts = str(ts).strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _read_bytes(source) -> bytes:
    """Lê bytes de um UploadedFile ou path-like."""
    if hasattr(source, "read"):
        data = source.read()
        source.seek(0)
        return data if isinstance(data, bytes) else data.encode()
    with open(source, "rb") as f:
        return f.read()


# ------------------------------------------------------------------
# Carregamento individual de cada tipo
# ------------------------------------------------------------------

def _load_vad_from_json(source, session_id: str) -> Tuple[pd.DataFrame, List[str]]:
    """Carrega segmentos VAD de um arquivo JSON de prosódia."""
    errors: List[str] = []
    try:
        raw = _read_bytes(source)
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return pd.DataFrame(), [f"[{session_id}] Erro ao ler JSON: {e}"]

    vad_list = []
    try:
        vad_raw = obj.get("result", obj).get("vad", [])
        for seg in vad_raw:
            start = float(seg.get("start", seg.get("begin", 0)))
            end = float(seg.get("end", 0))
            vad_list.append({
                "session_id": session_id,
                "start": start,
                "end": end,
                "duration": round(end - start, 4),
            })
    except Exception as e:
        return pd.DataFrame(), [f"[{session_id}] Erro ao parsear VAD: {e}"]

    if not vad_list:
        errors.append(f"[{session_id}] Nenhum segmento VAD encontrado no JSON.")

    return pd.DataFrame(vad_list), errors


def _load_transcricao_from_csv(source, session_id: str) -> Tuple[pd.DataFrame, List[str]]:
    """Carrega transcrição de um arquivo CSV."""
    errors: List[str] = []
    try:
        raw = _read_bytes(source)
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        return pd.DataFrame(), [f"[{session_id}] Erro ao ler CSV: {e}"]

    # Normalizar nomes de colunas
    df.columns = [c.strip() for c in df.columns]

    # Verificar colunas esperadas
    expected = {"SpeakerName", "Timestamp", "Text"}
    missing = expected - set(df.columns)
    if missing:
        errors.append(
            f"[{session_id}] Colunas ausentes no CSV: {', '.join(missing)}. "
            f"Colunas encontradas: {', '.join(df.columns)}"
        )

    df["session_id"] = session_id

    # Converter Timestamp para segundos
    if "Timestamp" in df.columns:
        df["seconds"] = df["Timestamp"].apply(_timestamp_to_seconds)
    else:
        df["seconds"] = 0.0

    # Contagem de palavras por linha
    if "Text" in df.columns:
        df["word_count"] = df["Text"].fillna("").apply(lambda t: len(str(t).split()))
    else:
        df["word_count"] = 0

    return df, errors


# ------------------------------------------------------------------
# Loader principal
# ------------------------------------------------------------------

def load_prosodia_from_uploads(
    json_files: Optional[List] = None,
    csv_files: Optional[List] = None,
) -> Dict:
    """
    Carrega múltiplos pares de arquivos (JSON + CSV) e retorna pr_data.

    Parâmetros
    ----------
    json_files : lista de UploadedFile (JSONs de prosódia)
    csv_files  : lista de UploadedFile (CSVs de transcrição)

    Retorna
    -------
    {
        "sessions": list[str],
        "vad": pd.DataFrame,
        "transcricao": pd.DataFrame,
        "_errors": list[str],
    }
    """
    json_files = json_files or []
    csv_files = csv_files or []

    all_errors: List[str] = []
    vad_frames: List[pd.DataFrame] = []
    tr_frames: List[pd.DataFrame] = []

    # Índices por session_id
    json_by_sid: Dict[str, object] = {}
    csv_by_sid: Dict[str, object] = {}

    for f in json_files:
        sid = _session_id_from_name(f.name)
        json_by_sid[sid] = f

    for f in csv_files:
        sid = _session_id_from_name(f.name)
        csv_by_sid[sid] = f

    all_sids = sorted(set(json_by_sid) | set(csv_by_sid))

    for sid in all_sids:
        if sid in json_by_sid:
            vad_df, errs = _load_vad_from_json(json_by_sid[sid], sid)
            all_errors.extend(errs)
            if not vad_df.empty:
                vad_frames.append(vad_df)
        else:
            all_errors.append(f"[{sid}] JSON de prosódia não encontrado.")

        if sid in csv_by_sid:
            tr_df, errs = _load_transcricao_from_csv(csv_by_sid[sid], sid)
            all_errors.extend(errs)
            if not tr_df.empty:
                tr_frames.append(tr_df)
        else:
            all_errors.append(f"[{sid}] CSV de transcrição não encontrado.")

    vad = pd.concat(vad_frames, ignore_index=True) if vad_frames else pd.DataFrame()
    transcricao = pd.concat(tr_frames, ignore_index=True) if tr_frames else pd.DataFrame()

    return {
        "sessions": all_sids,
        "vad": vad,
        "transcricao": transcricao,
        "_errors": all_errors,
    }


# ------------------------------------------------------------------
# Helpers de consulta
# ------------------------------------------------------------------

def get_prosodia_sessions(data: Dict) -> List[str]:
    return data.get("sessions", [])


def get_prosodia_speakers(data: Dict, session_id: Optional[str] = None) -> List[str]:
    tr = data.get("transcricao", pd.DataFrame())
    if tr.empty or "SpeakerName" not in tr.columns:
        return []
    if session_id:
        tr = tr[tr["session_id"] == session_id]
    return sorted(tr["SpeakerName"].dropna().unique().tolist())


def get_prosodia_summary(data: Dict) -> Dict:
    vad = data.get("vad", pd.DataFrame())
    tr = data.get("transcricao", pd.DataFrame())
    sessions = data.get("sessions", [])

    n_segments = len(vad)
    total_speech = round(vad["duration"].sum(), 2) if not vad.empty and "duration" in vad.columns else 0
    n_messages = len(tr)
    n_speakers = tr["SpeakerName"].nunique() if not tr.empty and "SpeakerName" in tr.columns else 0

    return {
        "n_sessions": len(sessions),
        "n_segments_vad": n_segments,
        "total_speech_s": total_speech,
        "n_messages": n_messages,
        "n_speakers": n_speakers,
    }
