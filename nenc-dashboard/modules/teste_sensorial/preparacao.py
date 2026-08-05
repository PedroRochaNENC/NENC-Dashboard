"""
Teste Sensorial — Preparação de Dados.

Upload / carregamento de arquivos EEG, Periféricos e PSD.
Pré-visualização e validação dos dados.
"""

import streamlit as st
from utils import auth

auth.require_module("teste_sensorial")

from pathlib import Path

from utils.data_loader import (
    load_from_folder,
    load_from_uploads,
    get_data_summary,
    get_participants,
)
from utils.organization_data import hydrate_session_state, save_session_state


hydrate_session_state("teste_sensorial", ("ts_data",))

st.title("📂 Preparação de Dados — Teste Sensorial")

# ------------------------------------------------------------------
# Upload / Carregamento
# ------------------------------------------------------------------
st.subheader("Carregar Dados")

mode = st.radio(
    "Modo de carregamento",
    ["Upload de arquivos", "Caminho da pasta"],
    index=0,
    key="ts_mode",
)

data = st.session_state.get("ts_data", {})

if mode == "Upload de arquivos":
    st.markdown("Envie os arquivos gerados pelo pipeline:")

    col1, col2, col3 = st.columns(3)

    with col1:
        ind_file = st.file_uploader(
            "indicadores (.xlsx / .csv)",
            type=["xlsx", "csv"],
            key="ts_ind",
        )
    with col2:
        per_file = st.file_uploader(
            "perifericos_metrics (.csv / .xlsx)",
            type=["xlsx", "csv"],
            key="ts_per",
        )
    with col3:
        psd_file = st.file_uploader(
            "psd_results (.xlsx / .csv) — opcional",
            type=["xlsx", "csv"],
            key="ts_psd",
        )

    if ind_file or per_file:
        data = load_from_uploads(ind_file, per_file, psd_file)
        st.session_state["ts_data"] = data
        save_session_state("teste_sensorial", ("ts_data",))

else:
    folder = st.text_input(
        "Caminho para 2.2.Dados Processados/",
        value="",
        placeholder=r"C:\...\2.2.Dados Processados",
        key="ts_folder",
    )
    if folder and Path(folder).exists():
        data = load_from_folder(folder)
        st.session_state["ts_data"] = data
        save_session_state("teste_sensorial", ("ts_data",))
    elif folder:
        st.error("Pasta não encontrada.")

# ------------------------------------------------------------------
# Avisos de validação
# ------------------------------------------------------------------
if "_errors" in data:
    for err in data["_errors"]:
        st.warning(err)

# ------------------------------------------------------------------
# Resumo dos dados carregados
# ------------------------------------------------------------------
datasets = {"indicadores", "perifericos", "psd_results"}
if data and datasets.intersection(data):
    st.divider()
    st.subheader("✅ Resumo dos Dados")

    summary = get_data_summary(data)
    participants = get_participants(data)

    cols = st.columns(4)
    cols[0].metric("Participantes", summary.get("n_participantes", 0))
    cols[1].metric("Etapas", summary.get("n_etapas", "—"))
    cols[2].metric("Linhas (indicadores)", summary.get("n_linhas_indicadores", "—"))
    cols[3].metric("Linhas (periféricos)", summary.get("n_linhas_perifericos", "—"))

    if participants:
        st.markdown("**Participantes detectados:**")
        st.write(", ".join(participants))

    # Preview das tabelas
    st.divider()
    st.subheader("📋 Pré-visualização")

    for key, label in [
        ("indicadores", "Indicadores (EEG)"),
        ("perifericos", "Periféricos"),
        ("psd_results", "PSD Results"),
    ]:
        if key in data and not data[key].empty:
            with st.expander(f"{label} — {len(data[key])} linhas, {len(data[key].columns)} colunas"):
                st.dataframe(data[key].head(50), width='stretch')

else:
    st.info("👆 Carregue os dados acima para começar.")

    st.markdown("### Formato esperado dos arquivos")

    with st.expander("indicadores.xlsx / .csv"):
        st.markdown(
            """
            | Coluna | Tipo | Descrição |
            |--------|------|-----------|
            | filename | str | ID da sessão / participante |
            | Etapa | str | Nome da condição |
            | Tempo | float | Tempo em segundos (janela 0.25 s) |
            | engagement_score | float | Score composto de engajamento |
            | atencao | float | Atenção (Beta frontal) |
            | WTP | float | Willingness to Pay |
            | … | … | Demais indicadores |
            """
        )

    with st.expander("perifericos_metrics.csv / .xlsx"):
        st.markdown(
            """
            | Coluna | Tipo | Descrição |
            |--------|------|-----------|
            | filename | str | ID da sessão / participante |
            | Etapa | str | Nome da condição |
            | Tempo | float | Duração da etapa (segundos) |
            | BPM | float | Batimentos por minuto |
            | RMSSD | float | Variabilidade cardíaca |
            | GSR_CAL_mean | float | Condutância galvânica (μS) |
            | *_zscore | float | Z-scores intra-sujeito |
            """
        )
