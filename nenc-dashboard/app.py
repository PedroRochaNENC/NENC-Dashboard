"""
NENC Dashboard — Visualização de dados de Neuromarketing.

Página inicial: carregamento de dados e resumo.
"""

import streamlit as st
from pathlib import Path

st.set_page_config(
    page_title="NENC Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

from utils.data_loader import (
    load_from_folder,
    load_from_uploads,
    get_data_summary,
    get_participants,
)


def main():
    st.title("🧠 NENC Dashboard")
    st.markdown(
        "**Visualização de dados de Neuromarketing** — "
        "EEG, Periféricos e Indicadores"
    )

    # ------------------------------------------------------------------
    # Sidebar — Carregamento de dados
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("📂 Carregar Dados")

        mode = st.radio(
            "Modo de carregamento",
            ["Upload de arquivos", "Caminho da pasta"],
            index=0,
        )

        data = st.session_state.get("data", {})

        if mode == "Upload de arquivos":
            st.markdown("Envie os arquivos gerados pelo pipeline:")

            ind_file = st.file_uploader(
                "indicadores (.xlsx / .csv)",
                type=["xlsx", "csv"],
                key="ind",
            )
            per_file = st.file_uploader(
                "perifericos_metrics (.csv / .xlsx)",
                type=["xlsx", "csv"],
                key="per",
            )
            psd_file = st.file_uploader(
                "psd_results (.xlsx / .csv)  —  opcional",
                type=["xlsx", "csv"],
                key="psd",
            )

            if ind_file or per_file:
                data = load_from_uploads(ind_file, per_file, psd_file)
                st.session_state["data"] = data

        else:
            folder = st.text_input(
                "Caminho para 2.2.Dados Processados/",
                value="",
                placeholder=r"C:\...\2.2.Dados Processados",
            )
            if folder and Path(folder).exists():
                data = load_from_folder(folder)
                st.session_state["data"] = data
            elif folder:
                st.error("Pasta não encontrada.")

    # ------------------------------------------------------------------
    # Avisos de validação
    # ------------------------------------------------------------------
    if "_errors" in data:
        for err in data["_errors"]:
            st.warning(err)

    # ------------------------------------------------------------------
    # Área principal — Resumo
    # ------------------------------------------------------------------
    datasets = {"indicadores", "perifericos", "psd_results"}
    if data and datasets.intersection(data):
        summary = get_data_summary(data)
        participants = get_participants(data)

        st.success("✅ Dados carregados com sucesso!")

        cols = st.columns(4)
        cols[0].metric("Participantes", summary.get("n_participantes", 0))
        cols[1].metric("Etapas", summary.get("n_etapas", "—"))
        cols[2].metric("Linhas (indicadores)", summary.get("n_linhas_indicadores", "—"))
        cols[3].metric("Linhas (periféricos)", summary.get("n_linhas_perifericos", "—"))

        if participants:
            st.markdown("**Participantes detectados:**")
            st.write(", ".join(participants))

        st.divider()
        st.markdown("### 📌 Navegação")
        st.markdown(
            """
            Use o menu lateral para acessar as páginas:
            - **📊 Timeline** — Gráficos sincronizados por participante
            - **👥 Média Geral** — Médias entre participantes por Etapa
            - **🔍 Dados Brutos** — Explorar e baixar dados tabulares
            """
        )

    else:
        st.info("👆 Carregue os dados pelo menu lateral para começar.")

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


if __name__ == "__main__":
    main()
