"""
Jornada Charts — Gráficos Plotly para dados de Eye-Tracking (Jornada de Compra).

Gera:
- Barras por AOI com mean ± std entre participantes
- Heatmap participante × AOI
- Share visual por marca (barras horizontais)
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from typing import List, Optional

# ---------------------------------------------------------------------------
# Paleta consistente com charts.py
# ---------------------------------------------------------------------------
BRAND_COLORS = {
    "Always": "#636EFA",
    "Carefree": "#EF553B",
    "Intimus": "#00CC96",
    "Libresse": "#AB63FA",
    "Sempre Livre": "#FFA15A",
    "Tampax": "#19D3F3",
}


def _get_brand_color(brand: str) -> str:
    return BRAND_COLORS.get(brand, "#888888")


# ---------------------------------------------------------------------------
# Barras por AOI — média ± std entre participantes
# ---------------------------------------------------------------------------

def create_metric_by_aoi(
    df: pd.DataFrame,
    metric: str,
    aois: Optional[List[str]] = None,
    title: str = "",
) -> go.Figure:
    """
    Barras horizontais: média ± std de uma métrica por AOI.

    Se df contém coluna 'Participante', calcula mean/std entre participantes.
    Caso contrário (médias pré-calculadas), mostra valores diretos.
    """
    if df.empty or metric not in df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text=f"Sem dados para '{metric}'",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    work = df.copy()
    if aois:
        work = work[work["AOI"].isin(aois)]

    work[metric] = pd.to_numeric(work[metric], errors="coerce")

    if "Participante" in work.columns:
        stats = (
            work.groupby("AOI")[metric]
            .agg(["mean", "std", "count"])
            .reset_index()
        )
        stats["std"] = stats["std"].fillna(0)
    else:
        stats = work[["AOI", metric]].copy()
        stats = stats.rename(columns={metric: "mean"})
        stats["std"] = 0
        stats["count"] = 1

    stats = stats.sort_values("mean", ascending=True)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=stats["AOI"],
            x=stats["mean"],
            orientation="h",
            error_x=dict(type="data", array=stats["std"], visible=True),
            marker_color="#00CC96",
            hovertemplate=(
                "<b>%{y}</b><br>"
                f"{metric}: " + "%{x:.3f} ± %{error_x.array:.3f}"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=title or f"{metric} por AOI",
        xaxis_title=metric,
        yaxis_title="",
        template="plotly_dark",
        height=max(400, len(stats) * 28),
        margin=dict(l=250),
    )

    return fig


# ---------------------------------------------------------------------------
# Heatmap participante × AOI
# ---------------------------------------------------------------------------

def create_attention_heatmap(
    df: pd.DataFrame,
    metric: str,
    participants: Optional[List[str]] = None,
    aois: Optional[List[str]] = None,
    title: str = "",
) -> go.Figure:
    """
    Heatmap: linhas = participantes, colunas = AOIs, valor = métrica.
    """
    if df.empty or metric not in df.columns or "Participante" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para heatmap",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    work = df.copy()
    if participants:
        work = work[work["Participante"].isin(participants)]
    if aois:
        work = work[work["AOI"].isin(aois)]

    pivot = work.pivot_table(
        index="Participante",
        columns="AOI",
        values=metric,
        aggfunc="mean",
    )

    if pivot.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados para heatmap",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=pivot.columns.tolist(),
            y=pivot.index.tolist(),
            colorscale="Viridis",
            hovertemplate=(
                "Participante: %{y}<br>"
                "AOI: %{x}<br>"
                f"{metric}: " + "%{z:.3f}<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=title or f"Heatmap — {metric}",
        xaxis_title="AOI",
        yaxis_title="Participante",
        template="plotly_dark",
        height=max(400, len(pivot) * 40 + 100),
        margin=dict(b=150),
        xaxis=dict(tickangle=-45),
    )

    return fig


# ---------------------------------------------------------------------------
# Share visual por marca
# ---------------------------------------------------------------------------

def create_brand_share_chart(
    df: pd.DataFrame,
    title: str = "Share Visual por Marca",
) -> go.Figure:
    """
    Barras horizontais de TotalGazeDuration por marca.
    Calcula percentual do total.
    """
    metric = "Soma de TotalGazeDuration"
    if df.empty or metric not in df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="Sem dados de Visual Share",
            showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    work = df.copy()
    total = work[metric].sum()
    work["Percentual"] = (work[metric] / total * 100).round(1) if total > 0 else 0

    work = work.sort_values(metric, ascending=True)

    colors = [_get_brand_color(m) for m in work["Marca"]]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=work["Marca"],
            x=work[metric],
            orientation="h",
            marker_color=colors,
            text=work["Percentual"].apply(lambda x: f"{x:.1f}%"),
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Duração Total: %{x:.3f}s<br>"
                "Share: %{text}<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=title,
        xaxis_title="Total Gaze Duration (s)",
        yaxis_title="",
        template="plotly_dark",
        height=max(300, len(work) * 50),
        margin=dict(l=150),
    )

    return fig


# ---------------------------------------------------------------------------
# ANOVA summary table (styled)
# ---------------------------------------------------------------------------

def format_anova_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Formata tabela ANOVA para exibição: apenas linhas 'Entre Grupos' com F e Sig."""
    if df.empty or "Grupo" not in df.columns:
        return pd.DataFrame()

    entre = df[df["Grupo"] == "Entre Grupos"].copy()
    if entre.empty:
        return pd.DataFrame()

    cols_to_show = ["Métrica", "Soma dos Quadrados", "df", "Quadrado Médio", "Z", "Sig."]
    available = [c for c in cols_to_show if c in entre.columns]

    result = entre[available].copy()

    # Marcar significância
    if "Sig." in result.columns:
        result["Significante?"] = result["Sig."].apply(
            lambda x: "✅ Sim" if pd.notna(x) and x < 0.05 else "❌ Não"
        )

    return result.reset_index(drop=True)
