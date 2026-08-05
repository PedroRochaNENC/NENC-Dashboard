"""
Chart Factory — Gráficos Plotly sincronizados para o dashboard NENC.

Gera subplots com eixo X compartilhado (Tempo_global) para:
- Engagement Score
- Indicadores Neurais (multi-line)
- BPM (periféricos)
- GSR (periféricos)
- Marcadores de Etapa (vrect coloridos)
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from typing import Dict, List

# ---------------------------------------------------------------------------
# Paletas
# ---------------------------------------------------------------------------

ETAPA_COLORS = [
    "rgba(99, 110, 250, 0.12)",
    "rgba(239, 85, 59, 0.12)",
    "rgba(0, 204, 150, 0.12)",
    "rgba(171, 99, 250, 0.12)",
    "rgba(255, 161, 90, 0.12)",
    "rgba(25, 211, 243, 0.12)",
    "rgba(255, 102, 146, 0.12)",
    "rgba(182, 232, 128, 0.12)",
]

INDICATOR_COLORS = {
    "engagement_score": "#636EFA",
    "atencao": "#EF553B",
    "WTP": "#00CC96",
    "Memoria_log": "#AB63FA",
    "assimetria": "#FFA15A",
    "Alpha/Beta": "#19D3F3",
    "AWI_frontal": "#FF6692",
    "sens_asym": "#B6E880",
    "inst_sens": "#FF97FF",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_etapa_markers(
    fig: go.Figure,
    boundaries: List[Dict],
    n_rows: int,
) -> None:
    """Adiciona faixas verticais coloridas para cada Etapa em todos os subplots."""
    for i, b in enumerate(boundaries):
        color = ETAPA_COLORS[i % len(ETAPA_COLORS)]
        for row in range(1, n_rows + 1):
            fig.add_vrect(
                x0=b["inicio"],
                x1=b["fim"],
                fillcolor=color,
                layer="below",
                line_width=0,
                row=row,
                col=1,
            )
        # Label apenas no subplot de cima
        fig.add_annotation(
            x=(b["inicio"] + b["fim"]) / 2,
            y=1.0,
            yref="y domain",
            text=f"<b>{b['etapa']}</b>",
            showarrow=False,
            font=dict(size=9, color="white"),
            bgcolor="rgba(50,50,50,0.7)",
            borderpad=2,
            row=1,
            col=1,
        )


# ---------------------------------------------------------------------------
# Timeline sincronizada (individual / média)
# ---------------------------------------------------------------------------

def create_synchronized_timeline(
    merged: pd.DataFrame,
    boundaries: List[Dict],
    selected_indicators: List[str],
    use_zscore: bool = False,
    title: str = "",
) -> go.Figure:
    """
    Cria stack de subplots sincronizados com eixo X compartilhado.

    Subplots (apenas se houver dados):
      1. Engagement Score
      2. Indicadores Neurais (multi-line com legenda toggle)
      3. BPM
      4. GSR
    """
    if merged.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para exibir",
            showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
            font=dict(size=20),
        )
        return fig

    x = merged["Tempo_global"]

    # Determinar quais subplots mostrar
    has_engagement = (
        "engagement_score" in merged.columns
        and merged["engagement_score"].notna().any()
    )
    has_indicators = any(
        c in merged.columns and merged[c].notna().any()
        for c in selected_indicators
        if c != "engagement_score"
    )

    bpm_col = (
        "BPM_zscore"
        if use_zscore and "BPM_zscore" in merged.columns
        else "BPM"
    )
    gsr_col = (
        "GSR_CAL_zscore"
        if use_zscore and "GSR_CAL_zscore" in merged.columns
        else "GSR_CAL_mean"
    )
    has_bpm = bpm_col in merged.columns and merged[bpm_col].notna().any()
    has_gsr = gsr_col in merged.columns and merged[gsr_col].notna().any()

    # Montar lista de subplots
    subplot_specs: List[Dict] = []
    if has_engagement:
        subplot_specs.append({"label": "Engagement Score", "type": "engagement"})
    if has_indicators:
        subplot_specs.append({"label": "Indicadores Neurais", "type": "indicators"})
    if has_bpm:
        bpm_label = "BPM (z-score)" if use_zscore else "BPM (bpm)"
        subplot_specs.append({"label": bpm_label, "type": "bpm"})
    if has_gsr:
        gsr_label = "GSR (z-score)" if use_zscore else "GSR (μS)"
        subplot_specs.append({"label": gsr_label, "type": "gsr"})

    n_rows = len(subplot_specs)
    if n_rows == 0:
        fig = go.Figure()
        fig.add_annotation(
            text="Nenhuma métrica disponível para exibir",
            showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=[s["label"] for s in subplot_specs],
    )

    for row_idx, spec in enumerate(subplot_specs, start=1):
        if spec["type"] == "engagement":
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=merged["engagement_score"],
                    mode="lines",
                    name="Engagement",
                    line=dict(color=INDICATOR_COLORS["engagement_score"], width=2),
                    hovertemplate="Tempo: %{x:.2f}s<br>Score: %{y:.3f}<extra></extra>",
                ),
                row=row_idx,
                col=1,
            )
            fig.add_hline(
                y=0, line_dash="dot", line_color="gray",
                opacity=0.5, row=row_idx, col=1,
            )

        elif spec["type"] == "indicators":
            for ind in selected_indicators:
                if ind == "engagement_score":
                    continue
                if ind not in merged.columns or not merged[ind].notna().any():
                    continue
                color = INDICATOR_COLORS.get(ind, "#888888")
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=merged[ind],
                        mode="lines",
                        name=ind,
                        line=dict(color=color, width=1.5),
                        hovertemplate=f"{ind}: " + "%{y:.3f}<extra></extra>",
                    ),
                    row=row_idx,
                    col=1,
                )

        elif spec["type"] == "bpm":
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=merged[bpm_col],
                    mode="lines",
                    name=bpm_col,
                    line=dict(color="#EF553B", width=2),
                    hovertemplate="BPM: %{y:.1f}<extra></extra>",
                ),
                row=row_idx,
                col=1,
            )

        elif spec["type"] == "gsr":
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=merged[gsr_col],
                    mode="lines",
                    name=gsr_col,
                    line=dict(color="#00CC96", width=2),
                    hovertemplate="GSR: %{y:.4f}<extra></extra>",
                ),
                row=row_idx,
                col=1,
            )

    # Marcadores de Etapa
    _add_etapa_markers(fig, boundaries, n_rows)

    # Layout
    fig.update_layout(
        height=220 * n_rows,
        title_text=title,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        template="plotly_dark",
        margin=dict(t=80, b=40),
    )
    fig.update_xaxes(title_text="Tempo (s)", row=n_rows, col=1)

    return fig


# ---------------------------------------------------------------------------
# Média Geral — Barras por Etapa
# ---------------------------------------------------------------------------

def create_average_by_etapa(
    indicadores: pd.DataFrame,
    metrics: List[str],
) -> go.Figure:
    """
    Barras com média ± std por Etapa (entre participantes) para indicadores EEG.

    Calcula primeiro a média por participante dentro de cada Etapa,
    depois a média e desvio padrão entre participantes.
    """
    if indicadores.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    available = [m for m in metrics if m in indicadores.columns]
    if not available:
        fig = go.Figure()
        fig.add_annotation(
            text="Nenhuma métrica disponível", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    # Média por participante por etapa, depois por etapa
    per_part = indicadores.groupby(["filename", "Etapa"])[available].mean().reset_index()
    stats = per_part.groupby("Etapa")[available].agg(["mean", "std"]).reset_index()
    etapas = stats["Etapa"].values

    fig = make_subplots(
        rows=len(available),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=available,
    )

    for i, metric in enumerate(available, 1):
        means = stats[(metric, "mean")].values
        stds = stats[(metric, "std")].fillna(0).values
        color = INDICATOR_COLORS.get(metric, "#636EFA")

        fig.add_trace(
            go.Bar(
                x=[str(e) for e in etapas],
                y=means,
                error_y=dict(type="data", array=stds, visible=True),
                name=metric,
                marker_color=color,
                hovertemplate=(
                    f"{metric}<br>Média: "
                    + "%{y:.3f} ± %{error_y.array:.3f}<extra></extra>"
                ),
            ),
            row=i,
            col=1,
        )

    fig.update_layout(
        height=200 * len(available),
        showlegend=False,
        template="plotly_dark",
        title_text="Indicadores — Média por Etapa (entre participantes)",
    )

    return fig


def create_perifericos_by_etapa(
    perifericos: pd.DataFrame,
    use_zscore: bool = False,
) -> go.Figure:
    """Barras com média ± std de periféricos por Etapa."""
    if perifericos.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados de periféricos", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    if use_zscore:
        metrics = ["BPM_zscore", "RMSSD_zscore", "GSR_CAL_zscore"]
    else:
        metrics = ["BPM", "RMSSD", "GSR_CAL_mean"]

    available = [m for m in metrics if m in perifericos.columns]
    if not available:
        fig = go.Figure()
        fig.add_annotation(
            text="Métricas não encontradas", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    stats = perifericos.groupby("Etapa")[available].agg(["mean", "std"]).reset_index()
    etapas = stats["Etapa"].values
    colors = ["#EF553B", "#AB63FA", "#00CC96"]

    fig = make_subplots(
        rows=len(available),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.10,
        subplot_titles=available,
    )

    for i, metric in enumerate(available, 1):
        means = stats[(metric, "mean")].values
        stds = stats[(metric, "std")].fillna(0).values

        fig.add_trace(
            go.Bar(
                x=[str(e) for e in etapas],
                y=means,
                error_y=dict(type="data", array=stds, visible=True),
                name=metric,
                marker_color=colors[i - 1] if i <= len(colors) else "#888",
            ),
            row=i,
            col=1,
        )

    fig.update_layout(
        height=200 * len(available),
        showlegend=False,
        template="plotly_dark",
        title_text="Periféricos — Média por Etapa",
    )

    return fig
