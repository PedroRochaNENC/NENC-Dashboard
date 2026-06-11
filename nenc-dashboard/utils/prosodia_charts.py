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


# ---------------------------------------------------------------------------
# 4. Features acústicas por segmento (acoustic timeline)
# ---------------------------------------------------------------------------

# Rótulos amigáveis para colunas acústicas
_ACOUSTIC_LABELS = {
    "f0_media": "F0 média (Hz)",
    "f0_variacao": "F0 variação",
    "f0_min": "F0 mín (Hz)",
    "f0_max": "F0 máx (Hz)",
    "loudness_media": "Loudness média",
    "loudness_variacao": "Loudness variação",
    "speaking_rate": "Taxa de fala",
    "intonation_score": "Score entonação",
    "emocao_angry": "Emoção: Raiva",
    "emocao_happy": "Emoção: Alegria",
    "emocao_neutral": "Emoção: Neutro",
    "emocao_sad": "Emoção: Tristeza",
    "dim_arousal": "Arousal",
    "dim_dominance": "Dominância",
    "dim_valence": "Valência",
}

_ACOUSTIC_COLORS = [
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
]


def create_acoustic_timeline(
    df: pd.DataFrame,
    session_id: Optional[str] = None,
    indicators: Optional[List[str]] = None,
    title: str = "",
) -> go.Figure:
    """
    Gráfico de linhas/steps com features acústicas por segmento VAD.

    Parâmetros
    ----------
    df         : DataFrame com colunas start_s (ou start), end_s (ou end) e indicadores acústicos
    session_id : filtrar por sessão; None = usar todos
    indicators : lista de colunas a exibir; None = todas disponíveis
    title      : título do gráfico
    """
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados acústicos disponíveis",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    work = df.copy()
    if session_id and "session_id" in work.columns:
        work = work[work["session_id"] == session_id]

    # Determinar coluna de tempo (x)
    if "start_s" in work.columns:
        x_col = "start_s"
    elif "start" in work.columns:
        x_col = "start"
    else:
        x_col = work.columns[0]  # fallback: primeira coluna

    # Indicadores disponíveis
    all_acoustic = list(_ACOUSTIC_LABELS.keys())
    available = [c for c in (indicators or all_acoustic) if c in work.columns]

    if not available:
        fig = go.Figure()
        fig.add_annotation(
            text="Nenhuma feature acústica disponível no DataFrame.",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    fig = go.Figure()

    for i, col in enumerate(available):
        color = _ACOUSTIC_COLORS[i % len(_ACOUSTIC_COLORS)]
        label = _ACOUSTIC_LABELS.get(col, col)
        y_vals = work[col].fillna(0).tolist()
        x_vals = work[x_col].tolist()

        # Step para F0 e loudness; line suave para emoções/VAD
        line_shape = "hv" if col.startswith(("f0", "loudness")) else "spline"

        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines",
                name=label,
                line=dict(color=color, width=1.8, shape=line_shape),
                hovertemplate=f"<b>{label}</b><br>Tempo: %{{x:.1f}}s<br>Valor: %{{y:.3f}}<extra></extra>",
            )
        )

    fig.update_layout(
        title=title or "Features Acústicas por Segmento",
        xaxis_title="Tempo (segundos)",
        yaxis_title="Valor",
        template="plotly_dark",
        height=420,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
    )
    return fig


# ---------------------------------------------------------------------------
# 5. Marcadores de transcrição — turnos por locutor
# ---------------------------------------------------------------------------

def create_transcription_markers(
    transcricao_df: pd.DataFrame,
    session_id: Optional[str] = None,
    speakers: Optional[List[str]] = None,
    title: str = "",
) -> go.Figure:
    """
    Scatter de turnos de fala: eixo X = tempo, eixo Y = locutor.
    Cada ponto representa um turno; o hover exibe o texto transcrito.
    Tamanho do marcador proporcional ao número de palavras.

    Parâmetros
    ----------
    transcricao_df : DataFrame com colunas session_id, SpeakerName, seconds, word_count, Text
    session_id     : filtrar por sessão; None = usar todos
    speakers       : lista de locutores a exibir; None = todos
    title          : título do gráfico
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
    if session_id and "session_id" in work.columns:
        work = work[work["session_id"] == session_id]
    if speakers:
        work = work[work["SpeakerName"].isin(speakers)]

    if work.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem turnos para os locutores selecionados",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark")
        return fig

    all_spks = sorted(work["SpeakerName"].dropna().unique().tolist())
    color_map = _speaker_color_map(all_spks)

    fig = go.Figure()

    for spk in all_spks:
        s_df = work[work["SpeakerName"] == spk].copy()

        # Tamanho do ponto: word_count clampado
        sizes = s_df["word_count"].clip(lower=6, upper=60) if "word_count" in s_df.columns else 12

        # Texto para tooltip (truncar em 120 chars)
        text_vals = (
            s_df["Text"].fillna("").apply(
                lambda t: (t[:120] + "…") if len(t) > 120 else t
            )
            if "Text" in s_df.columns
            else pd.Series([""] * len(s_df))
        )

        ts_vals = s_df["Timestamp"].tolist() if "Timestamp" in s_df.columns else s_df["seconds"].tolist()

        fig.add_trace(
            go.Scatter(
                x=s_df["seconds"],
                y=[spk] * len(s_df),
                mode="markers+lines",
                marker=dict(
                    size=sizes,
                    color=color_map[spk],
                    opacity=0.85,
                    line=dict(width=0.8, color="white"),
                    symbol="circle",
                ),
                line=dict(color=color_map[spk], width=0.5, dash="dot"),
                name=spk,
                text=text_vals,
                customdata=list(zip(ts_vals, sizes)),
                hovertemplate=(
                    f"<b>{spk}</b><br>"
                    "Tempo: %{customdata[0]}<br>"
                    "Palavras: %{customdata[1]}<br>"
                    "<i>%{text}</i>"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=title or "Turnos por Locutor — Marcadores de Transcrição",
        xaxis_title="Tempo (segundos)",
        yaxis_title="Locutor",
        template="plotly_dark",
        height=max(350, len(all_spks) * 100 + 150),
        hovermode="closest",
        legend=dict(title="Locutor"),
    )
    return fig

