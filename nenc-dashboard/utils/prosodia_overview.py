"""
NencBoost — recortes e gráficos da Análise Geral (6a) e da lista de Áudios (8a).

Funções puras sobre a lista de áudios de `get_audios_for_interviews` e os
dataframes que a Análise Geral já monta. Nada aqui lê o banco.

A hora de entrada usa a conversão de fuso de `prosodia_summary`: a API grava
`received_at` em UTC sem sufixo, e tratá-la como hora local adiantaria tudo
em 3h no filtro de período e na coluna Recebido.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import plotly.graph_objects as go

import utils.charts  # noqa: F401  registra o template "nenc" do Plotly
from utils import prosodia_summary
from utils.prosodia_signals import EMOCOES

POSITIVO = "#9184d9"
NEUTRO = "#595d6c"
NEGATIVO = "#c9708b"
DIVERGENTE = "#d2cefd"
VOLUME = "#423a6a"
LIMIAR = 0.2

SEM_QR = "Sem QR"
UPLOAD = "Upload direto"

_ID_NO_FIM = re.compile(r"_(\d+)$")


# ---------------------------------------------------------------------------
# Atributos de um áudio
# ---------------------------------------------------------------------------

def qr_label(audio: dict) -> str:
    """Mesma regra da tabela de Áudios: sem nome, a origem decide o rótulo."""
    name = str(audio.get("qr_code_name") or "").strip()
    if name:
        return name
    session = str(audio.get("session_id") or "")
    if session.startswith("wa_") and not session.startswith("wa_upload_"):
        return SEM_QR
    return UPLOAD


def origin(audio: dict) -> str:
    return "upload" if qr_label(audio) == UPLOAD else "whatsapp"


def api_audio_id(session_id: str) -> Optional[int]:
    if not str(session_id or "").startswith("wa_"):
        return None
    match = _ID_NO_FIM.search(str(session_id))
    return int(match.group(1)) if match else None


def masked_phone(session_id: str) -> str:
    """`wa_<telefone>_<id>` vira +55 11 ••••-0412; upload pela API não tem telefone."""
    parts = str(session_id or "").split("_")
    if len(parts) < 3 or parts[0] != "wa":
        return ""
    if parts[1] == "upload":
        return "upload pela API"
    digits = "".join(filter(str.isdigit, parts[1]))
    if len(digits) < 8:
        return ""
    prefix = "+{} {} ".format(digits[:2], digits[2:4]) if digits.startswith("55") else "+"
    return "{}••••-{}".format(prefix, digits[-4:])


def entry_time(audio: dict) -> Optional[pd.Timestamp]:
    """Hora de chegada (`received_at`), ou a da importação para o acervo antigo."""
    stamp = prosodia_summary.entry_time(audio.get("received_at"), audio.get("created_at"))
    return None if pd.isna(stamp) else stamp


def sync_text(last_sync_at, now=None) -> str:
    """'Sincronizado há 12 min', a partir de `projects.last_sync_at` (UTC)."""
    stamp = prosodia_summary.received_time(last_sync_at)
    if pd.isna(stamp):
        return "Ainda não sincronizado"
    quando = prosodia_summary.relative_time(stamp, now)
    if quando == "agora" or quando.startswith("há") or quando == "ontem":
        return "Sincronizado {}".format(quando)
    return "Sincronizado em {}".format(quando)


def sync_status_html(last_sync_at, api_project_id=None, campaign_id=None) -> str:
    """Bolinha verde quando já houve sync, o tempo e a origem (projeto API ou campanha)."""
    origem = (
        "API #{}".format(api_project_id) if api_project_id
        else "Campanha #{}".format(campaign_id) if campaign_id
        else "sem projeto API"
    )
    sincronizado = not pd.isna(prosodia_summary.received_time(last_sync_at))
    return (
        '<span style="width:7px;height:7px;border-radius:50%;background:{c};flex:none"></span>'
        "<span>{t} · {o}</span>"
    ).format(c="#7bc0a8" if sincronizado else "var(--nenc-dim)", t=sync_text(last_sync_at), o=origem)


def entry_date(audio: dict) -> Optional[date]:
    stamp = entry_time(audio)
    return stamp.date() if stamp is not None else None


def entry_text(audio: dict) -> str:
    stamp = entry_time(audio)
    return stamp.strftime("%d/%m %H:%M") if stamp is not None else ""


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def qr_options(audios: Iterable[dict]) -> List[str]:
    """Rótulos de QR presentes, do mais usado ao menos usado."""
    counts = pd.Series([qr_label(a) for a in audios]).value_counts()
    return list(counts.index)


def filter_audios(
    audios: Sequence[dict],
    qrs: Optional[Sequence[str]] = None,
    period: Optional[Tuple[date, date]] = None,
    origins: Optional[Sequence[str]] = None,
) -> List[dict]:
    selected = []
    for audio in audios:
        if qrs and qr_label(audio) not in qrs:
            continue
        if origins and origin(audio) not in origins:
            continue
        if period:
            day = entry_date(audio)
            if day is None or day < period[0] or day > period[1]:
                continue
        selected.append(audio)
    return selected


ORIGENS = {"whatsapp": "WhatsApp", "upload": "Upload"}


def filter_description(
    qrs: Optional[Sequence[str]],
    period: Optional[Tuple[date, date]],
    origins: Optional[Sequence[str]],
    bounds: Tuple[date, date],
) -> str:
    """O recorte em uma linha, para registrar na análise salva; vazio sem filtro."""
    parts = []
    if qrs:
        parts.append("QR code {}".format(", ".join(qrs)))
    if period and tuple(period) != tuple(bounds):
        parts.append("de {} a {}".format(period[0].strftime("%d/%m/%Y"), period[1].strftime("%d/%m/%Y")))
    if origins and set(origins) != set(ORIGENS):
        parts.append("origem {}".format(", ".join(ORIGENS.get(o, o) for o in origins)))
    return "; ".join(parts)


def period_bounds(audios: Iterable[dict]) -> Tuple[date, date]:
    days = [d for d in (entry_date(a) for a in audios) if d]
    today = date.today()
    return (min(days), max(days)) if days else (today, today)


def as_period(value) -> Optional[Tuple[date, date]]:
    """Normaliza o retorno de `st.date_input` com intervalo."""
    if isinstance(value, (tuple, list)):
        if len(value) == 2:
            return value[0], value[1]
        if len(value) == 1:
            return value[0], value[0]
        return None
    return (value, value) if value else None


# ---------------------------------------------------------------------------
# Recortes
# ---------------------------------------------------------------------------

def _meta(audios: Sequence[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"session_id": str(a.get("session_id") or ""), "qr": qr_label(a), "entrou": entry_time(a)} for a in audios],
        columns=["session_id", "qr", "entrou"],
    )


def _weighted(values: pd.Series, weights: pd.Series) -> Optional[float]:
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return None
    return float((values[mask] * weights[mask]).sum() / weights[mask].sum())


def _dominant_emotion(sinc_df: pd.DataFrame) -> pd.Series:
    """Emoção predominante de cada linha do Sincronizado, como rótulo."""
    present = [(col, label) for col, label in EMOCOES if col in sinc_df.columns]
    if not present or sinc_df.empty:
        return pd.Series(dtype=object)
    work = sinc_df[[col for col, _ in present]].apply(pd.to_numeric, errors="coerce")
    valid = work.dropna(how="all")
    return valid.idxmax(axis=1).map(dict(present))


def qr_summary(
    audios: Sequence[dict],
    indice_por_audio: pd.DataFrame,
    sinc_df: pd.DataFrame,
    divergences: pd.DataFrame,
) -> pd.DataFrame:
    """Uma linha por QR: áudios, índice combinado, tempo positivo, emoção e divergências."""
    meta = _meta(audios)
    if meta.empty:
        return pd.DataFrame(columns=["QR code", "Áudios", "Índice", "Positivo", "Emoção predominante", "Divergências"])
    index = indice_por_audio.rename(columns={"grupo": "session_id"}) if not indice_por_audio.empty else pd.DataFrame(
        columns=["session_id", "trechos", "indice", "positivo"])
    index = index.assign(session_id=index["session_id"].astype(str))
    frame = meta.merge(index[["session_id", "trechos", "indice", "positivo"]], on="session_id", how="left")

    emotions = pd.Series(dtype=object)
    if not sinc_df.empty and "session_id" in sinc_df.columns:
        labels = _dominant_emotion(sinc_df)
        if not labels.empty:
            by_session = sinc_df.loc[labels.index, "session_id"].astype(str)
            qr_of = dict(zip(meta["session_id"], meta["qr"]))
            emotions = labels.groupby(by_session.map(qr_of)).agg(lambda s: s.mode().iloc[0] if not s.mode().empty else "")

    diverging = pd.Series(dtype=int)
    if divergences is not None and not divergences.empty:
        qr_of = dict(zip(meta["session_id"], meta["qr"]))
        diverging = divergences["session_id"].astype(str).map(qr_of).value_counts()

    rows = []
    for qr, block in frame.groupby("qr", sort=False):
        rows.append({
            "QR code": qr,
            "Áudios": int(len(block)),
            "Índice": _weighted(block["indice"], block["trechos"]),
            "Positivo": _weighted(block["positivo"], block["trechos"]),
            "Emoção predominante": emotions.get(qr, ""),
            "Divergências": int(diverging.get(qr, 0)),
        })
    return pd.DataFrame(rows).sort_values("Áudios", ascending=False).reset_index(drop=True)


def weekly(audios: Sequence[dict], indice_por_audio: pd.DataFrame) -> pd.DataFrame:
    """Entradas e índice combinado médio por semana de chegada."""
    meta = _meta(audios).dropna(subset=["entrou"])
    if meta.empty:
        return pd.DataFrame(columns=["semana", "entradas", "indice"])
    meta["semana"] = meta["entrou"].dt.to_period("W-SUN").dt.start_time
    if not indice_por_audio.empty:
        index = indice_por_audio.rename(columns={"grupo": "session_id"})[["session_id", "trechos", "indice"]]
        meta = meta.merge(index.assign(session_id=index["session_id"].astype(str)), on="session_id", how="left")
    else:
        meta["trechos"], meta["indice"] = None, None
    rows = [
        {"semana": week, "entradas": int(len(block)), "indice": _weighted(block["indice"], block["trechos"])}
        for week, block in meta.groupby("semana")
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------

def _band_color(value: Optional[float]) -> str:
    if value is None or pd.isna(value):
        return NEUTRO
    if value >= LIMIAR:
        return POSITIVO
    if value <= -LIMIAR:
        return NEGATIVO
    return NEUTRO


def _base(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(template="nenc", height=height, margin=dict(l=40, r=16, t=12, b=36), showlegend=False,
                      plot_bgcolor="#161826", bargap=0.12)
    return fig


def fig_index_histogram(indice_por_audio: pd.DataFrame) -> go.Figure:
    edges = [round(-1 + 0.2 * i, 1) for i in range(11)]
    values = indice_por_audio["indice"].clip(-1, 1)
    counts = pd.cut(values, edges, include_lowest=True).value_counts(sort=False)
    centers = [(a + b) / 2 for a, b in zip(edges[:-1], edges[1:])]
    fig = go.Figure(go.Bar(
        x=centers, y=counts.values, width=0.18,
        marker_color=[_band_color(c) for c in centers],
        hovertemplate="%{customdata}<br>%{y} áudio(s)<extra></extra>",
        customdata=["{:+.1f} a {:+.1f}".format(a, b).replace(".", ",") for a, b in zip(edges[:-1], edges[1:])],
    ))
    fig.update_xaxes(range=[-1.05, 1.05], tickvals=[-1, -0.6, -0.2, 0.2, 0.6, 1],
                     ticktext=["−1", "−0,6", "−0,2", "+0,2", "+0,6", "+1"], showgrid=False)
    fig.update_yaxes(title_text="áudios")
    return _base(fig, 260)


def fig_text_voice(indice_por_audio: pd.DataFrame, audios: Sequence[dict]) -> go.Figure:
    qr_of = {str(a.get("session_id") or ""): qr_label(a) for a in audios}
    frame = indice_por_audio.assign(qr=indice_por_audio["grupo"].astype(str).map(qr_of).fillna(""))

    def color(row) -> str:
        texto, voz = row["texto"], row["voz"]
        if (texto >= LIMIAR and voz <= -LIMIAR) or (texto <= -LIMIAR and voz >= LIMIAR):
            return DIVERGENTE
        if texto >= LIMIAR and voz >= LIMIAR:
            return POSITIVO
        if texto <= -LIMIAR and voz <= -LIMIAR:
            return NEGATIVO
        return "#75798c"

    fig = go.Figure(go.Scatter(
        x=frame["texto"], y=frame["voz"], mode="markers",
        marker=dict(size=7, color=frame.apply(color, axis=1), opacity=0.85),
        customdata=frame[["grupo", "qr", "indice"]].values,
        hovertemplate="%{customdata[0]}<br>%{customdata[1]}<br>texto %{x:+.2f} · voz %{y:+.2f}"
                      "<br>índice %{customdata[2]:+.2f}<extra></extra>",
    ))
    for x, y, text, anchor in ((0.98, 0.98, "texto e voz positivos", "right"),
                               (0.98, -0.98, "texto positivo, voz abaixo", "right"),
                               (-0.98, 0.98, "texto negativo, voz acima", "left"),
                               (-0.98, -0.98, "texto e voz negativos", "left")):
        fig.add_annotation(x=x, y=y, text=text, showarrow=False, xanchor=anchor,
                           yanchor="top" if y > 0 else "bottom", font=dict(size=10, color="#75798c"))
    fig.add_hline(y=0, line=dict(color="#3f424d", dash="dot"))
    fig.add_vline(x=0, line=dict(color="#3f424d", dash="dot"))
    fig.update_xaxes(range=[-1, 1], title_text="texto", zeroline=False, showgrid=False)
    fig.update_yaxes(range=[-1, 1], title_text="voz", zeroline=False, showgrid=False)
    return _base(fig, 300)


def fig_qr_index(summary: pd.DataFrame) -> go.Figure:
    frame = summary.dropna(subset=["Índice"]).iloc[::-1]
    fig = go.Figure(go.Bar(
        x=frame["Índice"], y=frame["QR code"], orientation="h",
        marker_color=[_band_color(v) for v in frame["Índice"]],
        text=["{:+.2f}".format(v).replace(".", ",") for v in frame["Índice"]], textposition="outside",
        customdata=frame[["Áudios", "Positivo", "Divergências"]].values,
        hovertemplate="%{y}<br>índice %{x:+.2f}<br>%{customdata[0]} áudios · "
                      "%{customdata[1]:.0%} positivo · %{customdata[2]} divergências<extra></extra>",
    ))
    limit = max(0.5, float(frame["Índice"].abs().max() or 0) + 0.15) if not frame.empty else 0.5
    fig.update_xaxes(range=[-limit, limit], zeroline=True, zerolinecolor="#75798c", showgrid=False)
    fig.update_yaxes(showgrid=False)
    return _base(fig, 36 * max(len(frame), 3) + 60)


def fig_weekly(series: pd.DataFrame) -> go.Figure:
    labels = series["semana"].dt.strftime("%d/%m")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=series["entradas"], name="entradas", marker_color=VOLUME, yaxis="y2",
                         hovertemplate="semana de %{x}<br>%{y} entradas<extra></extra>"))
    fig.add_trace(go.Scatter(x=labels, y=series["indice"], name="índice", mode="lines+markers",
                             line=dict(color=POSITIVO, width=2), marker=dict(size=6),
                             hovertemplate="semana de %{x}<br>índice %{y:+.2f}<extra></extra>"))
    fig.update_layout(
        yaxis=dict(title="índice", zeroline=True, zerolinecolor="#3f424d", side="left"),
        yaxis2=dict(overlaying="y", side="right", showgrid=False, title="entradas",
                    range=[0, float(series["entradas"].max() or 1) * 3]),
    )
    return _base(fig, 280)


def kpi_html(label: str, value: str, note: str = "", accent: bool = False) -> str:
    return (
        '<div style="border:1px solid var(--nenc-border);border-radius:8px;padding:.8rem .95rem;'
        'display:flex;flex-direction:column;gap:.4rem">'
        '<div style="font-size:.62rem;letter-spacing:.09em;text-transform:uppercase;color:var(--nenc-faint)">{l}</div>'
        '<div style="font-size:1.65rem;font-weight:500;letter-spacing:-.02em;line-height:1;color:{c}">{v}</div>'
        '<div style="font-size:.7rem;color:var(--nenc-muted)">{n}</div></div>'
    ).format(l=label, v=value, n=note, c="var(--nenc-accent-300)" if accent else "var(--nenc-text)")


def section_html(number: str, title: str) -> str:
    return (
        '<div style="display:flex;align-items:baseline;gap:.6rem;margin:1.4rem 0 .5rem">'
        '<span style="font-size:.7rem;color:var(--nenc-accent-400);font-variant-numeric:tabular-nums">{}</span>'
        '<span style="font-size:1.05rem;font-weight:500;letter-spacing:-.01em">{}</span></div>'
    ).format(number, title)


def card_title(title: str, question: str = "") -> str:
    return (
        '<div style="display:flex;flex-direction:column;gap:.1rem;margin-bottom:.4rem">'
        '<span style="font-size:.9rem;font-weight:600">{}</span>'
        '<span style="font-size:.7rem;color:var(--nenc-faint)">{}</span></div>'
    ).format(title, question)
