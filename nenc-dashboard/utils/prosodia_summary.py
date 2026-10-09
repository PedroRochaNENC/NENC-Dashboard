"""
NencBoost — números do Resumo do projeto e da lista de projetos.

Leituras de metadado (sem os blobs) vindas de `prosodia_db` e funções puras
sobre dataframes. O conteúdo dos áudios é lido uma vez, por
`load_project_signals`, para as pizzas de emoção e sentimento; a página guarda
o resultado em cache.

Hora de entrada: `audios.received_at` é a hora em que a mensagem chegou à API
de WhatsApp, gravada na importação. `created_at` marca a importação no
dashboard e concentraria tudo no horário do sync, então só vale como reserva
para áudios importados antes da coluna existir.

Fusos: a API grava `received_at` em UTC (sem sufixo quando não há fuso) e o
SQLite grava `created_at` na hora local do servidor, que no container é UTC.
Tudo é levado para `NENC_TZ` (padrão America/Sao_Paulo) e comparado com o
"agora" nesse mesmo fuso; sem isso o pico por hora sairia deslocado.
"""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils import prosodia_db
from utils.prosodia_signals import (
    EMOCOES,
    detectar_divergencias,
    indice_combinado_por_grupo,
    referencia_valencia,
    sentimento_por_grupo,
    tem_sentimento_texto,
)

_LOGGER = logging.getLogger(__name__)

TOP_QR = 6

_TZ = os.environ.get("NENC_TZ", "America/Sao_Paulo")
# Fuso de `created_at` (datetime('now','localtime') do SQLite).
_SERVER_TZ = datetime.now().astimezone().tzinfo
_ENTRY_COLUMNS = ["id", "session_id", "qr_code_name", "duration_seconds",
                  "received_at", "created_at", "n_analyses"]


# ---------------------------------------------------------------------------
# Hora
# ---------------------------------------------------------------------------

def now_local() -> datetime:
    """Agora em `NENC_TZ`, sem fuso, para comparar com as entradas."""
    return pd.Timestamp.now(tz=_TZ).tz_localize(None).to_pydatetime()


def _local_time(value, naive_tz) -> pd.Timestamp:
    """Hora em `NENC_TZ`, sem fuso. `naive_tz` é o fuso de um valor sem fuso."""
    stamp = pd.to_datetime(value, errors="coerce")
    if stamp is None or pd.isna(stamp):
        return pd.NaT
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(naive_tz)
    return stamp.tz_convert(_TZ).tz_localize(None)


def received_time(value) -> pd.Timestamp:
    """`received_at` (UTC, da API) em `NENC_TZ`."""
    return _local_time(value, "UTC")


def imported_time(value) -> pd.Timestamp:
    """`created_at` (hora local do servidor) em `NENC_TZ`."""
    return _local_time(value, _SERVER_TZ)


def entry_time(received_at, created_at) -> pd.Timestamp:
    """Hora de entrada de um áudio: a chegada na API, ou a importação no acervo antigo."""
    stamp = received_time(received_at)
    return imported_time(created_at) if pd.isna(stamp) else stamp


# ---------------------------------------------------------------------------
# Leituras do banco
# ---------------------------------------------------------------------------

def project_entries(project_id: int) -> pd.DataFrame:
    """Uma linha por áudio do projeto, só com metadado.

    `entrou_em` é `received_at` quando a importação o gravou, senão
    `created_at`; `hora_real` diz qual das duas valeu.
    """
    frame = pd.DataFrame(prosodia_db.get_audio_entries(project_id), columns=_ENTRY_COLUMNS)
    received = pd.to_datetime(frame["received_at"].map(received_time))
    imported = pd.to_datetime(frame["created_at"].map(imported_time))
    frame["hora_real"] = received.notna()
    frame["entrou_em"] = received.where(received.notna(), imported)
    frame["qr_code_name"] = frame["qr_code_name"].fillna("").astype(str).str.strip()
    frame["n_analyses"] = pd.to_numeric(frame["n_analyses"], errors="coerce").fillna(0).astype(int)
    frame["duration_seconds"] = pd.to_numeric(frame["duration_seconds"], errors="coerce")
    return frame.drop(columns=["received_at", "created_at"])


def project_activity() -> Dict[int, dict]:
    """Por projeto da organização ativa: QR codes com entrada e última entrada.

    QR cadastrado e nunca escaneado não aparece aqui: a contagem vem do banco
    local, sem chamar a API para cada projeto da lista.
    """
    activity = {}
    for project_id, row in prosodia_db.get_project_activity().items():
        stamps = [stamp for stamp in (received_time(row.get("ultima_recebida")),
                                      imported_time(row.get("ultima_importada")))
                  if not pd.isna(stamp)]
        activity[project_id] = {
            "n_qr": int(row.get("n_qr") or 0),
            "ultima": max(stamps) if stamps else pd.NaT,
        }
    return activity


class _BytesFile:
    def __init__(self, data: bytes, name: str):
        self._buf = io.BytesIO(data)
        self.name = name

    def read(self):
        return self._buf.read()

    def seek(self, pos: int):
        return self._buf.seek(pos)


def load_project_signals(project_id: int) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Transcrição e Sincronizado de todos os áudios do projeto, concatenados.

    Mesma leitura de `_load_project_frames` da Análise Geral, sem o VAD, que o
    Resumo não usa. Sincronizado ilegível fica de fora e vai para o log.
    """
    from utils.prosodia_loader import load_prosodia_from_uploads, normalizar_sincronizado

    tr_parts: List[pd.DataFrame] = []
    sinc_parts: List[pd.DataFrame] = []
    for audio in prosodia_db.get_audios(project_id):
        sid = audio.get("session_id") or ""
        if audio.get("transcricao_csv"):
            parsed = load_prosodia_from_uploads(
                json_files=[],
                csv_files=[_BytesFile(audio["transcricao_csv"], "Transcricao-{}.csv".format(sid))],
                sincronizado_files=[],
            )
            tr_df = parsed.get("transcricao", pd.DataFrame())
            if not tr_df.empty:
                if "session_id" not in tr_df.columns:
                    tr_df = tr_df.assign(session_id=sid)
                tr_parts.append(tr_df)
        if audio.get("sincronizado_csv"):
            try:
                raw = pd.read_csv(io.BytesIO(audio["sincronizado_csv"]))
                if not raw.empty:
                    sinc_parts.append(normalizar_sincronizado(raw, sid))
            except Exception:
                _LOGGER.exception(
                    "Sincronizado ilegivel no audio %s do projeto %s; fica fora do Resumo.",
                    sid, project_id,
                )
    tr_all = pd.concat(tr_parts, ignore_index=True) if tr_parts else pd.DataFrame()
    sinc_all = pd.concat(sinc_parts, ignore_index=True) if sinc_parts else pd.DataFrame()
    return tr_all, sinc_all


# ---------------------------------------------------------------------------
# Coleta: entradas por QR e por hora
# ---------------------------------------------------------------------------

def _with_qr(entries: pd.DataFrame) -> pd.DataFrame:
    return entries[entries["qr_code_name"] != ""]


def kpis(entries: pd.DataFrame, now: Optional[datetime] = None) -> dict:
    now = now or now_local()
    with_qr = _with_qr(entries)
    recent = with_qr[with_qr["entrou_em"] >= now - timedelta(hours=24)]
    durations = entries["duration_seconds"].dropna()
    durations = durations[durations > 0]
    return {
        "entradas_qr": int(len(with_qr)),
        "entradas_24h": int(len(recent)),
        "analisados": int((entries["n_analyses"] > 0).sum()),
        "total": int(len(entries)),
        "duracao_media": float(durations.mean()) if not durations.empty else None,
    }


def qr_ranking(entries: pd.DataFrame, top: int = TOP_QR) -> dict:
    counts = _with_qr(entries)["qr_code_name"].value_counts()
    total = int(counts.sum())
    rest = counts.iloc[top:]
    return {
        "linhas": [
            {"nome": name, "entradas": int(n), "fatia": n / total}
            for name, n in counts.head(top).items()
        ],
        "qr_distintos": int(len(counts)),
        "outros_qr": int(len(rest)),
        "outros_entradas": int(rest.sum()),
        "sem_qr": int((entries["qr_code_name"] == "").sum()),
        "total": total,
        "maximo": int(counts.max()) if total else 0,
    }


def qr_stats(entries: pd.DataFrame) -> Dict[str, dict]:
    """Por nome de QR: entradas, última entrada e hora de pico."""
    stats: Dict[str, dict] = {}
    for name, block in _with_qr(entries).groupby("qr_code_name"):
        hours = block["entrou_em"].dropna().dt.hour
        stats[name] = {
            "entradas": int(len(block)),
            "ultima": block["entrou_em"].max(),
            "pico": int(hours.mode().iloc[0]) if not hours.empty else None,
        }
    return stats


def entries_by_hour(
    entries: pd.DataFrame, only_today: bool = False, now: Optional[datetime] = None
) -> pd.Series:
    """Entradas por QR em cada hora do dia, das 8h às 20h no mínimo."""
    now = now or now_local()
    with_qr = _with_qr(entries).dropna(subset=["entrou_em"])
    if only_today:
        with_qr = with_qr[with_qr["entrou_em"].dt.date == now.date()]
    counts = with_qr["entrou_em"].dt.hour.value_counts().sort_index()
    if counts.empty:
        return counts
    hours = range(min(8, int(counts.index.min())), max(20, int(counts.index.max())) + 1)
    return counts.reindex(hours, fill_value=0)


# ---------------------------------------------------------------------------
# Sinais: emoção na voz, sentimento do texto, divergências, índice combinado
# ---------------------------------------------------------------------------

def emotion_shares(sinc_df: pd.DataFrame) -> List[Tuple[str, float]]:
    """Fatia de segmentos por emoção predominante, maior primeiro.

    Mesma regra de `emotion_distribution_text`: em cada segmento vale a
    categoria de maior probabilidade.
    """
    present = [(col, label) for col, label in EMOCOES if col in sinc_df.columns]
    if not present:
        return []
    work = sinc_df[[col for col, _ in present]].apply(pd.to_numeric, errors="coerce").dropna(how="all")
    if work.empty:
        return []
    shares = work.idxmax(axis=1).value_counts(normalize=True)
    return sorted(((label, float(shares.get(col, 0.0))) for col, label in present), key=lambda x: -x[1])


def _sentiment_source(tr_df: pd.DataFrame, sinc_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if tem_sentimento_texto(tr_df):
        return tr_df
    if tem_sentimento_texto(sinc_df):
        return sinc_df
    return None


def _shares(row: pd.Series) -> List[Tuple[str, float]]:
    return [("Positivo", float(row["positivo"])), ("Neutro", float(row["neutro"])),
            ("Negativo", float(row["negativo"]))]


def text_sentiment(tr_df: pd.DataFrame, sinc_df: pd.DataFrame) -> Optional[dict]:
    """Média ponderada (−1 a +1) e fatias do tempo de fala por rótulo."""
    source = _sentiment_source(tr_df, sinc_df)
    if source is None:
        return None
    row = sentimento_por_grupo(source, None).iloc[0]
    return {"media": float(row["media"]), "fatias": _shares(row)}


def divergences(sinc_df: pd.DataFrame) -> Optional[Tuple[int, int]]:
    """(divergências voz × texto, áudios com divergência), ou None sem sentimento."""
    if not tem_sentimento_texto(sinc_df):
        return None
    found = detectar_divergencias(sinc_df)
    return int(len(found)), int(found["session_id"].nunique()) if not found.empty else 0


def combined_index(sinc_df: pd.DataFrame) -> Optional[dict]:
    """Índice combinado de sentimento do projeto inteiro.

    A fórmula é a de `prosodia_signals` (a mesma da Análise Geral): metade a
    nota do texto, metade a valência vocal medida contra todos os áudios do
    projeto. Fatias em proporção do tempo de fala com as duas leituras.
    """
    table = indice_combinado_por_grupo(sinc_df, referencia_valencia(sinc_df), None)
    if table.empty:
        return None
    row = table.iloc[0]
    return {"media": float(row["indice"]), "fatias": _shares(row), "trechos": int(row["trechos"])}


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------

def relative_time(stamp, now: Optional[datetime] = None) -> str:
    if stamp is None or pd.isna(stamp):
        return ""
    now = now or now_local()
    seconds = (now - stamp).total_seconds()
    days = (now.date() - stamp.date()).days
    if seconds < 60:
        return "agora"
    if seconds < 3600:
        return "há {} min".format(int(seconds // 60))
    if days == 0:
        return "há {} h".format(int(seconds // 3600))
    if days == 1:
        return "ontem"
    if days < 14:
        return "há {} dias".format(days)
    if days < 60:
        return "há {} semanas".format(days // 7)
    return stamp.strftime("%d/%m/%Y")


def duration_text(seconds: Optional[float]) -> str:
    if not seconds:
        return "—"
    total = int(round(seconds))
    return "{}:{:02d}".format(total // 60, total % 60)


def number_text(value: int) -> str:
    return "{:,}".format(int(value)).replace(",", ".")


def signed_text(value: float) -> str:
    return "{:+.2f}".format(value).replace(".", ",")


def phone_text(phone) -> str:
    digits = "".join(filter(str.isdigit, str(phone or "")))
    if digits.startswith("55") and len(digits) in (12, 13):
        return "+55 {} {}-{}".format(digits[2:4], digits[4:-4], digits[-4:])
    return "+" + digits if digits else ""
