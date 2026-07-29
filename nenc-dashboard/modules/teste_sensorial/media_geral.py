"""
Teste Sensorial — Média Geral dos Indicadores por Etapa e Participante.
"""

import streamlit as st
from utils import auth

auth.require_module("teste_sensorial")

import pandas as pd

from utils import teste_sensorial_db
from utils.data_loader import get_participants, get_etapas

teste_sensorial_db.init_db()

st.title("👥 Média Geral — Teste Sensorial")

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

# Carregar dados
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
participants = get_participants(data)
etapas = get_etapas(data)

st.subheader("📊 Médias de Indicadores por Etapa")

metric_cols = [c for c in indicadores.columns if c not in ("participante", "tempo", "etapa") and pd.api.types.is_numeric_dtype(indicadores[c])]

if "etapa" in indicadores.columns and metric_cols:
    etapa_summary = indicadores.groupby("etapa")[metric_cols].mean().round(4)
    st.dataframe(etapa_summary, use_container_width=True)

    st.divider()
    st.subheader("📈 Comparativo por Participante")
    selected_metric = st.selectbox("Métrica", metric_cols)
    if selected_metric:
        pivot_df = indicadores.pivot_table(index="participante", columns="etapa", values=selected_metric, aggfunc="mean").round(4)
        st.dataframe(pivot_df, use_container_width=True)
