"""
Página Média Geral — Médias entre participantes por Etapa.
"""

import streamlit as st

st.set_page_config(page_title="Média Geral | NENC", page_icon="👥", layout="wide")

import pandas as pd

from utils.charts import create_average_by_etapa, create_perifericos_by_etapa

INDICADORES_METRICS = [
    "engagement_score",
    "atencao",
    "WTP",
    "Memoria_log",
    "assimetria",
    "Alpha/Beta",
    "AWI_frontal",
]

PERIFERICOS_RAW = ["BPM", "RMSSD", "GSR_CAL_mean"]
PERIFERICOS_Z = ["BPM_zscore", "RMSSD_zscore", "GSR_CAL_zscore"]


def main():
    st.title("👥 Média Geral por Etapa")

    data = st.session_state.get("data", {})

    if not data or "indicadores" not in data:
        st.warning(
            "⚠️ Nenhum dado carregado. "
            "Volte à página inicial e carregue os dados."
        )
        st.stop()

    indicadores: pd.DataFrame = data["indicadores"].copy()
    perifericos: pd.DataFrame = data.get("perifericos", pd.DataFrame()).copy()

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("⚙️ Controles")

        use_zscore = st.checkbox("Usar Z-Scores (periféricos)", value=False)

        if "Codigo" in indicadores.columns:
            codigos = ["Todos"] + sorted(
                indicadores["Codigo"].dropna().unique().astype(str).tolist()
            )
            selected_codigo = st.selectbox("Filtrar por Código", codigos)
            if selected_codigo != "Todos":
                indicadores = indicadores[
                    indicadores["Codigo"].astype(str) == selected_codigo
                ]
                if (
                    not perifericos.empty
                    and "Codigo" in perifericos.columns
                ):
                    perifericos = perifericos[
                        perifericos["Codigo"].astype(str) == selected_codigo
                    ]

    # ------------------------------------------------------------------
    # Indicadores
    # ------------------------------------------------------------------
    st.subheader("Indicadores Neurais")

    available_metrics = [
        m for m in INDICADORES_METRICS if m in indicadores.columns
    ]

    if available_metrics:
        fig_ind = create_average_by_etapa(indicadores, available_metrics)
        st.plotly_chart(fig_ind, use_container_width=True)
    else:
        st.info("Nenhuma métrica de indicadores disponível.")

    # ------------------------------------------------------------------
    # Periféricos
    # ------------------------------------------------------------------
    st.subheader("Periféricos")

    if not perifericos.empty:
        fig_per = create_perifericos_by_etapa(
            perifericos, use_zscore=use_zscore
        )
        st.plotly_chart(fig_per, use_container_width=True)
    else:
        st.info("Dados de periféricos não carregados.")

    # ------------------------------------------------------------------
    # Tabelas resumo
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📋 Tabelas Resumo")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Indicadores (média ± std por Etapa)**")
        if available_metrics:
            per_part = (
                indicadores
                .groupby(["filename", "Etapa"])[available_metrics]
                .mean()
                .reset_index()
            )
            summary = (
                per_part
                .groupby("Etapa")[available_metrics]
                .agg(["mean", "std"])
                .round(4)
            )
            st.dataframe(summary, use_container_width=True)

    with col2:
        st.markdown("**Periféricos (média ± std por Etapa)**")
        if not perifericos.empty:
            per_metrics = PERIFERICOS_Z if use_zscore else PERIFERICOS_RAW
            per_available = [
                m for m in per_metrics if m in perifericos.columns
            ]
            if per_available:
                per_summary = (
                    perifericos
                    .groupby("Etapa")[per_available]
                    .agg(["mean", "std"])
                    .round(4)
                )
                st.dataframe(per_summary, use_container_width=True)


main()
