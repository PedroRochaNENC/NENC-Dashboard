"""
Teste Sensorial — Timeline sincronizada por participante ou média geral.
"""

import streamlit as st
from utils import auth

auth.require_module("teste_sensorial")

import pandas as pd

from utils import teste_sensorial_db
from utils.data_loader import get_participants, get_etapas
from utils.resampler import (
    build_unified_timeline,
    compute_participant_average,
    get_etapa_boundaries,
)
from utils.charts import create_synchronized_timeline, INDICATOR_COLORS

teste_sensorial_db.init_db()

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

DEFAULT_ON = {"atencao", "WTP", "assimetria"}

st.title("📊 Timeline Sincronizada — Teste Sensorial")

project_id = st.session_state.get("ts_project_id")

if not project_id:
    st.warning("⚠️ Nenhum projeto está selecionado.")
    if st.button("🧪 Ir para Projetos", type="primary"):
        st.switch_page("modules/teste_sensorial/projetos.py")
    st.stop()

project = teste_sensorial_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    st.stop()

st.caption(f"Projeto Ativo: **{project['name']}**")

# Carregar dados do projeto
data = st.session_state.get("ts_data")
if not data or "indicadores" not in data or data["indicadores"].empty:
    data = teste_sensorial_db.get_dataset(project_id)
    st.session_state["ts_data"] = data

if not data or "indicadores" not in data or data["indicadores"].empty:
    st.warning("⚠️ Nenhum dado de indicadores EEG carregado para este projeto.")
    if st.button("📋 Ir para Dados do Projeto", type="primary"):
        st.switch_page("modules/teste_sensorial/preparacao.py")
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

    if view_mode == "Individual":
        selected_participant = st.selectbox("Participante", participants)
    else:
        selected_participant = None

    st.subheader("Indicadores")
    selected_indicators = []
    for ind in AVAILABLE_INDICATORS:
        if ind in indicadores.columns:
            default_val = ind in DEFAULT_ON
            if st.checkbox(ind, value=default_val, key=f"ts_check_{ind}"):
                selected_indicators.append(ind)

    perif_indicators = []
    if not perifericos.empty:
        st.subheader("Periféricos")
        possible_perif = [c for c in perifericos.columns if c not in ("participante", "tempo", "etapa")]
        for p_col in possible_perif:
            if st.checkbox(p_col, value=False, key=f"ts_check_perif_{p_col}"):
                perif_indicators.append(p_col)

# ------------------------------------------------------------------
# Construção do gráfico
# ------------------------------------------------------------------
if not selected_indicators and not perif_indicators:
    st.info("Selecione pelo menos um indicador na barra lateral para visualizar o gráfico.")
else:
    if view_mode == "Individual" and selected_participant:
        part_ind = indicadores[indicadores["participante"] == selected_participant]
        part_per = (
            perifericos[perifericos["participante"] == selected_participant]
            if not perifericos.empty and "participante" in perifericos.columns
            else pd.DataFrame()
        )
        timeline_df = build_unified_timeline(part_ind, part_per)
        title = f"Timeline — {selected_participant}"
    else:
        timeline_df = compute_participant_average(indicadores, perifericos)
        title = "Timeline — Média Geral de Todos os Participantes"

    boundaries = get_etapa_boundaries(timeline_df)

    all_selected = selected_indicators + perif_indicators
    fig = create_synchronized_timeline(timeline_df, selected_indicators=all_selected, etapa_boundaries=boundaries, title=title)
    st.plotly_chart(fig, use_container_width=True)
