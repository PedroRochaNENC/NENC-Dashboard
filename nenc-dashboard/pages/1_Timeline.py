"""
Página Timeline — Gráficos sincronizados por participante ou média geral.
"""

import streamlit as st

st.set_page_config(page_title="Timeline | NENC", page_icon="📊", layout="wide")

import pandas as pd

from utils.data_loader import get_participants, get_etapas
from utils.resampler import (
    build_unified_timeline,
    compute_participant_average,
    get_etapa_boundaries,
)
from utils.charts import create_synchronized_timeline, INDICATOR_COLORS

AVAILABLE_INDICATORS = [
    "atencao",
    "WTP",
    "Memoria_log",
    "assimetria",
    "Alpha/Beta",
    "AWI_frontal",
    "sens_asym",
    "inst_sens",
]

# Indicadores ligados por padrão
DEFAULT_ON = {"atencao", "WTP", "assimetria"}


def main():
    st.title("📊 Timeline Sincronizada")

    data = st.session_state.get("data", {})

    if not data or "indicadores" not in data:
        st.warning(
            "⚠️ Nenhum dado carregado. "
            "Volte à página inicial e carregue os dados."
        )
        st.stop()

    indicadores: pd.DataFrame = data["indicadores"]
    perifericos: pd.DataFrame = data.get("perifericos", pd.DataFrame())

    participants = get_participants(data)

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("⚙️ Controles")

        view_mode = st.radio("Visualização", ["Individual", "Média geral"])

        selected_participant = None
        if view_mode == "Individual":
            if not participants:
                st.warning("Nenhum participante encontrado.")
                st.stop()
            selected_participant = st.selectbox(
                "Participante", participants, index=0
            )

        st.divider()

        # Seleção de etapas
        st.subheader("Etapas")
        all_etapas = get_etapas(data, filename=selected_participant)
        if all_etapas:
            selected_etapas = st.multiselect(
                "Etapas para análise",
                options=all_etapas,
                default=all_etapas,
                key="etapas_timeline",
            )
        else:
            selected_etapas = []

        st.divider()

        # Seleção de indicadores
        st.subheader("Indicadores")
        selected_indicators: list = []
        for ind in AVAILABLE_INDICATORS:
            if ind in indicadores.columns:
                checked = st.checkbox(
                    ind,
                    value=(ind in DEFAULT_ON),
                    key=f"ind_{ind}",
                )
                if checked:
                    selected_indicators.append(ind)

        st.divider()

        use_zscore = st.checkbox("Usar Z-Scores (periféricos)", value=False)

    # ------------------------------------------------------------------
    # Filtrar etapas selecionadas
    # ------------------------------------------------------------------
    if selected_etapas:
        indicadores = indicadores[indicadores["Etapa"].isin(selected_etapas)]
        if not perifericos.empty and "Etapa" in perifericos.columns:
            perifericos = perifericos[perifericos["Etapa"].isin(selected_etapas)]
    else:
        st.warning("Selecione pelo menos uma Etapa.")
        st.stop()

    # ------------------------------------------------------------------
    # Build timeline
    # ------------------------------------------------------------------
    if view_mode == "Individual" and selected_participant:
        merged = build_unified_timeline(
            indicadores, perifericos, filename=selected_participant
        )
        title = f"Timeline — {selected_participant}"
    else:
        avg_ind, avg_per = compute_participant_average(indicadores, perifericos)
        merged = build_unified_timeline(
            avg_ind, avg_per, filename="Média Geral"
        )
        title = "Timeline — Média Geral"

    if merged.empty:
        st.warning("Sem dados para o participante/modo selecionado.")
        st.stop()

    boundaries = get_etapa_boundaries(merged)

    # ------------------------------------------------------------------
    # Render chart
    # ------------------------------------------------------------------
    fig = create_synchronized_timeline(
        merged=merged,
        boundaries=boundaries,
        selected_indicators=selected_indicators,
        use_zscore=use_zscore,
        title=title,
    )

    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # Resumo estatístico
    # ------------------------------------------------------------------
    with st.expander("📋 Resumo estatístico por Etapa"):
        skip = {
            "filename", "Etapa", "Codigo", "Tempo",
            "Tempo_global", "Etapa_inicio", "Etapa_fim",
        }
        metric_cols = [c for c in merged.columns if c not in skip]
        if metric_cols:
            summary = (
                merged
                .groupby("Etapa")[metric_cols]
                .agg(["mean", "std"])
                .round(4)
            )
            st.dataframe(summary, use_container_width=True)


# Streamlit multipage: executar ao importar
main()
