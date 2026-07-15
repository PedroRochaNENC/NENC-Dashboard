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
    for prefix in ("Prosodia-", "Transcricao-", "Sincronizado-", "prosodia-", "transcricao-", "sincronizado-"):
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


def _load_sincronizado_csv(source, session_id: str) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """
    Carrega um arquivo Sincronizado-<id>.csv que contém tanto dados VAD
    (start_s, end_s, duracao_s) quanto dados de transcrição
    (speakers, timestamp_inicio, texto_transcricao).

    Retorna (vad_df, transcricao_df, errors).
    """
    errors: List[str] = []
    try:
        raw = _read_bytes(source)
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame(), [f"[{session_id}] Erro ao ler Sincronizado CSV: {e}"]

    df.columns = [c.strip() for c in df.columns]
    df["session_id"] = session_id

    # --- VAD ---
    vad_df = pd.DataFrame()
    if {"start_s", "end_s"}.issubset(df.columns):
        vad_rows = df[["session_id", "start_s", "end_s"]].copy()
        vad_rows = vad_rows.rename(columns={"start_s": "start", "end_s": "end"})
        if "duracao_s" in df.columns:
            vad_rows["duration"] = df["duracao_s"].round(4)
        else:
            vad_rows["duration"] = (vad_rows["end"] - vad_rows["start"]).round(4)
        vad_df = vad_rows.reset_index(drop=True)
    else:
        errors.append(f"[{session_id}] Sincronizado: colunas start_s/end_s não encontradas — VAD ignorado.")

    # --- Transcrição ---
    tr_df = pd.DataFrame()
    col_map = {
        "speakers": "SpeakerName",
        "timestamp_inicio": "Timestamp",
        "texto_transcricao": "Text",
    }
    missing_tr = [c for c in col_map if c not in df.columns]
    if missing_tr:
        errors.append(
            f"[{session_id}] Sincronizado: colunas ausentes para transcrição: {', '.join(missing_tr)}."
        )
    else:
        tr_rows = df[["session_id"] + list(col_map.keys())].copy()
        tr_rows = tr_rows.rename(columns=col_map)
        tr_rows["seconds"] = tr_rows["Timestamp"].apply(_timestamp_to_seconds)
        tr_rows["word_count"] = tr_rows["Text"].fillna("").apply(lambda t: len(str(t).split()))
        # Preservar colunas acústicas extras quando presentes
        extra_cols = [c for c in df.columns if c not in {"session_id"} | set(col_map.keys())]
        for col in extra_cols:
            tr_rows[col] = df[col].values
        tr_df = tr_rows.reset_index(drop=True)

    return vad_df, tr_df, errors


# ------------------------------------------------------------------
# Loader principal
# ------------------------------------------------------------------

def load_prosodia_from_uploads(
    json_files: Optional[List] = None,
    csv_files: Optional[List] = None,
    sincronizado_files: Optional[List] = None,
) -> Dict:
    """
    Carrega múltiplos pares de arquivos (JSON + CSV) e retorna pr_data.

    Parâmetros
    ----------
    json_files         : lista de UploadedFile (JSONs de prosódia)
    csv_files          : lista de UploadedFile (CSVs de transcrição)
    sincronizado_files : lista de UploadedFile (CSVs Sincronizado com VAD + transcrição)

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
    sincronizado_files = sincronizado_files or []

    all_errors: List[str] = []
    vad_frames: List[pd.DataFrame] = []
    tr_frames: List[pd.DataFrame] = []

    # Índices por session_id
    json_by_sid: Dict[str, object] = {}
    csv_by_sid: Dict[str, object] = {}
    sinc_by_sid: Dict[str, object] = {}

    for f in json_files:
        sid = _session_id_from_name(f.name)
        json_by_sid[sid] = f

    for f in csv_files:
        sid = _session_id_from_name(f.name)
        csv_by_sid[sid] = f

    for f in sincronizado_files:
        sid = _session_id_from_name(f.name)
        sinc_by_sid[sid] = f

    all_sids = sorted(set(json_by_sid) | set(csv_by_sid) | set(sinc_by_sid))

    for sid in all_sids:
        # Sincronizado fornece VAD + transcrição em arquivo único
        if sid in sinc_by_sid:
            vad_df, tr_df, errs = _load_sincronizado_csv(sinc_by_sid[sid], sid)
            all_errors.extend(errs)
            if not vad_df.empty:
                vad_frames.append(vad_df)
            if not tr_df.empty:
                tr_frames.append(tr_df)
            # Arquivos separados têm prioridade (substituem o Sincronizado)
            if sid in json_by_sid:
                vad_df2, errs2 = _load_vad_from_json(json_by_sid[sid], sid)
                all_errors.extend(errs2)
                if not vad_df2.empty and vad_frames:
                    vad_frames[-1] = vad_df2
            if sid in csv_by_sid:
                tr_df2, errs2 = _load_transcricao_from_csv(csv_by_sid[sid], sid)
                all_errors.extend(errs2)
                if not tr_df2.empty and tr_frames:
                    tr_frames[-1] = tr_df2
            continue

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


def extract_topic_from_text(text: str) -> str:
    """Extrai de 1 a 3 palavras mais relevantes/compridas do texto para servir como tópico."""
    if not text or not isinstance(text, str):
        return "Geral"
    import re
    import unicodedata
    
    # Lista de stopwords comuns em português
    stopwords = {
        "a", "o", "as", "os", "de", "do", "da", "dos", "das", "e", "ou", "no", "na",
        "nos", "nas", "em", "para", "por", "com", "sem", "um", "uma", "uns", "umas",
        "que", "qual", "quais", "como", "onde", "quando", "se", "seu", "sua", "seus", "suas",
        "voce", "vocês", "voces", "ele", "ela", "eles", "elas", "isso", "isto", "aquele",
        "mas", "tambem", "mais", "muito", "entao", "aqui", "la", "sim", "nao", "pra", "pro",
        "este", "esta", "estes", "estas", "tudo", "todo", "toda", "todos", "todas", "ser",
        "ter", "ir", "com", "por", "para", "uma", "um", "do", "da", "no", "na", "ao", "aos",
        "pelo", "pela", "pelos", "pelas", "num", "numa", "neste", "nesta", "disso", "disto",
        "dele", "dela", "deles", "delas", "mim", "me", "te", "se", "nos", "vos", "lhe", "lhes",
        "meu", "minha", "meus", "minhas", "teu", "tua", "teus", "tuas", "nosso", "nossa",
        "nossos", "nossas", "vosso", "vossa", "vossos", "vossas", "qualquer", "quaisquer",
        "algum", "alguma", "alguns", "algumas", "nenhum", "nenhuma", "outro", "outra", "outros",
        "outras", "mesmo", "mesma", "mesmos", "mesmas", "proprio", "propria", "proprios", "proprias",
        "acho", "acha", "achar", "coisa", "coisas", "tipo", "ne", "ta", "entao", "gente", "queria",
        "porque", "porquê", "pois", "assim", "sobre", "quase", "estou", "esta", "estao", "esteve",
        "tinha", "tinham", "tenho", "tem", "temos", "fazer", "faz", "feito", "disse", "diz", "falar",
        "fala", "falou", "vai", "vou", "vao", "aqui", "ali", "la", "dessa", "desse", "desta", "deste",
        "muito", "pouco", "bem", "mal", "sim", "nao", "talvez"
    }
    
    text_norm = "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(ch) != "Mn"
    )
    
    words = re.findall(r"\b[a-z]{3,}\b", text_norm)
    filtered = [w for w in words if w not in stopwords]
    
    if not filtered:
        filtered = [w for w in words if len(w) >= 3]
        
    if not filtered:
        return "Geral"
        
    seen = set()
    unique = []
    for w in filtered:
        if w not in seen:
            seen.add(w)
            unique.append(w)
            if len(unique) == 3:
                break
                
    return " ".join(w.capitalize() for w in unique)
