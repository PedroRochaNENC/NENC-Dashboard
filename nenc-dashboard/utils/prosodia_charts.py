"""
Prosodia Charts — Gráficos Plotly para análise de prosódia e transcrições.

Gera:
- Timeline VAD (Gantt horizontal de segmentos de fala)
- Estatísticas por locutor (barras: mensagens e palavras)
- Timeline de mensagens (scatter por locutor ao longo do tempo)
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from typing import List, Optional

# ---------------------------------------------------------------------------
# Paleta de locutores
# ---------------------------------------------------------------------------
SPEAKER_COLORS = [
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA",
    "#FFA15A", "#19D3F3", "#FF6692", "#B6E880",
]


def _speaker_color_map(speakers: List[str]) -> dict:
    return {
        spk: SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
        for i, spk in enumerate(sorted(speakers))
    }


# ---------------------------------------------------------------------------
# 1. Timeline VAD
# ---------------------------------------------------------------------------

def create_vad_timeline(
    vad_df: pd.DataFrame,
    session_id: Optional[str] = None,
    title: str = "",
) -> go.Figure:
    """
    Gantt horizontal mostrando cada segmento de fala (VAD) como barra.

    Parâmetros
    ----------
    vad_df     : DataFrame com colunas session_id, start, end, duration
    session_id : filtrar por sessão específica; None = todas
    title      : título do gráfico
    """
    if vad_df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados VAD disponíveis",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    work = vad_df.copy()
    if session_id:
        work = work[work["session_id"] == session_id]

    if work.empty:
        fig = go.Figure()
        fig.add_annotation(
            text=f"Sem dados VAD para sessão '{session_id}'",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    sessions = work["session_id"].unique().tolist()
    color_map = {s: SPEAKER_COLORS[i % len(SPEAKER_COLORS)] for i, s in enumerate(sessions)}

    fig = go.Figure()
    for sess in sessions:
        sess_df = work[work["session_id"] == sess]
        for _, row in sess_df.iterrows():
            fig.add_trace(
                go.Bar(
                    x=[row["duration"]],
                    y=[sess],
                    base=[row["start"]],
                    orientation="h",
                    marker_color=color_map[sess],
                    opacity=0.85,
                    name=sess,
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{sess}</b><br>"
                        f"Início: {row['start']:.2f}s<br>"
                        f"Fim: {row['end']:.2f}s<br>"
                        f"Duração: {row['duration']:.2f}s"
                        "<extra></extra>"
                    ),
                )
            )

    # Adicionar uma trace por sessão apenas para a legenda
    for sess in sessions:
        fig.add_trace(
            go.Bar(
                x=[None], y=[None],
                orientation="h",
                marker_color=color_map[sess],
                name=sess,
                showlegend=True,
            )
        )

    fig.update_layout(
        title=title or "Timeline VAD — Segmentos de Fala",
        xaxis_title="Tempo (segundos)",
        yaxis_title="Sessão",
        barmode="overlay",
        template="plotly_dark",
        height=max(300, len(sessions) * 80 + 120),
        legend=dict(title="Sessão"),
    )
    return fig


# ---------------------------------------------------------------------------
# 2. Estatísticas por locutor
# ---------------------------------------------------------------------------

def create_speaker_stats(
    transcricao_df: pd.DataFrame,
    session_id: Optional[str] = None,
    title: str = "",
) -> go.Figure:
    """
    Barras agrupadas: número de mensagens e total de palavras por locutor.
    """
    if transcricao_df.empty or "SpeakerName" not in transcricao_df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados de transcrição disponíveis",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    work = transcricao_df.copy()
    if session_id:
        work = work[work["session_id"] == session_id]

    if work.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para a sessão selecionada",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    agg = (
        work.groupby("SpeakerName")
        .agg(
            mensagens=("Text", "count"),
            palavras=("word_count", "sum"),
        )
        .reset_index()
        .sort_values("palavras", ascending=False)
    )

    color_map = _speaker_color_map(agg["SpeakerName"].tolist())

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Mensagens por Locutor", "Palavras por Locutor"),
    )

    for _, row in agg.iterrows():
        color = color_map[row["SpeakerName"]]
        fig.add_trace(
            go.Bar(
                y=[row["SpeakerName"]],
                x=[row["mensagens"]],
                orientation="h",
                marker_color=color,
                name=row["SpeakerName"],
                showlegend=True,
                legendgroup=row["SpeakerName"],
                hovertemplate=f"<b>{row['SpeakerName']}</b><br>Mensagens: {row['mensagens']}<extra></extra>",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Bar(
                y=[row["SpeakerName"]],
                x=[row["palavras"]],
                orientation="h",
                marker_color=color,
                name=row["SpeakerName"],
                showlegend=False,
                legendgroup=row["SpeakerName"],
                hovertemplate=f"<b>{row['SpeakerName']}</b><br>Palavras: {row['palavras']}<extra></extra>",
            ),
            row=1, col=2,
        )

    fig.update_layout(
        title=title or "Participação por Locutor",
        template="plotly_dark",
        height=max(350, len(agg) * 50 + 150),
        barmode="group",
        legend=dict(title="Locutor"),
    )
    return fig


# ---------------------------------------------------------------------------
# 3. Timeline de mensagens
# ---------------------------------------------------------------------------

def create_message_timeline(
    transcricao_df: pd.DataFrame,
    session_id: Optional[str] = None,
    speakers: Optional[List[str]] = None,
    title: str = "",
) -> go.Figure:
    """
    Scatter: cada mensagem como ponto no tempo, colorido por locutor.
    O tamanho do ponto é proporcional ao número de palavras.
    """
    if transcricao_df.empty or "seconds" not in transcricao_df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados de transcrição disponíveis",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    work = transcricao_df.copy()
    if session_id:
        work = work[work["session_id"] == session_id]
    if speakers:
        work = work[work["SpeakerName"].isin(speakers)]

    if work.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para a seleção atual",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    all_speakers = sorted(work["SpeakerName"].dropna().unique().tolist())
    color_map = _speaker_color_map(all_speakers)

    fig = go.Figure()

    for spk in all_speakers:
        spk_df = work[work["SpeakerName"] == spk].copy()
        # Clamp word_count para tamanho de marcador razoável
        sizes = spk_df["word_count"].clip(lower=4).clip(upper=50)

        # Truncar texto longo para tooltip
        text_preview = spk_df["Text"].fillna("").apply(
            lambda t: (t[:100] + "…") if len(t) > 100 else t
        )

        fig.add_trace(
            go.Scatter(
                x=spk_df["seconds"],
                y=[spk] * len(spk_df),
                mode="markers",
                marker=dict(
                    size=sizes,
                    color=color_map[spk],
                    opacity=0.8,
                    line=dict(width=0.5, color="white"),
                ),
                name=spk,
                customdata=spk_df[["Timestamp", "word_count"]].values,
                text=text_preview,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Tempo: %{customdata[0]}<br>"
                    "Palavras: %{customdata[1]}<br>"
                    "%{text}"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=title or "Timeline de Mensagens por Locutor",
        xaxis_title="Tempo (segundos)",
        yaxis_title="Locutor",
        template="plotly_dark",
        height=max(350, len(all_speakers) * 80 + 150),
        legend=dict(title="Locutor"),
    )
    return fig
