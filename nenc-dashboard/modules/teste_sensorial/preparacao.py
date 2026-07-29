"""
Teste Sensorial — Preparação de Dados.

Upload / carregamento de arquivos EEG, Periféricos e PSD + Briefing do Projeto.
"""

import streamlit as st
from utils import auth

auth.require_module("teste_sensorial")

import pandas as pd
from pathlib import Path

from utils import teste_sensorial_db
from utils.data_loader import (
    load_from_folder,
    load_from_uploads,
    get_data_summary,
    get_participants,
)

teste_sensorial_db.init_db()

st.title("📋 Dados do Projeto — Teste Sensorial")

# ------------------------------------------------------------------
# Verificação de Projeto Ativo
# ------------------------------------------------------------------
project_id = st.session_state.get("ts_project_id")

if not project_id:
    st.warning("⚠️ Nenhum projeto sensorial está selecionado no momento.")
    c_btn1, c_btn2 = st.columns([1, 2])
    with c_btn1:
        if st.button("🧪 Ver Lista de Projetos", type="primary", use_container_width=True):
            st.switch_page("modules/teste_sensorial/projetos.py")
    st.divider()

    with st.expander("➕ Criar Novo Projeto Rápido", expanded=True):
        quick_name = st.text_input("Nome do Projeto", placeholder="Ex: Teste Sensorial Fragrância A vs B")
        quick_prod = st.text_input("Produto / Estímulo Avaliado", placeholder="Ex: Chocolate 70% Cacau")
        if st.button("Criar e Continuar", type="primary"):
            if quick_name.strip():
                new_id = teste_sensorial_db.create_project(name=quick_name, produto_estimulo=quick_prod)
                st.session_state["ts_project_id"] = new_id
                st.success(f"Projeto '{quick_name}' criado!")
                st.rerun()
            else:
                st.error("Digite o nome do projeto.")
    st.stop()

# Carregar projeto do DB
project = teste_sensorial_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    if st.button("Voltar aos Projetos"):
        st.session_state.pop("ts_project_id", None)
        st.switch_page("modules/teste_sensorial/projetos.py")
    st.stop()

st.info(f"📂 **Projeto Ativo:** `{project['name']}` | **Produto/Estímulo:** `{project.get('produto_estimulo') or 'Não especificado'}`")

# ------------------------------------------------------------------
# Briefing do Projeto
# ------------------------------------------------------------------
with st.expander("📝 Briefing & Contexto do Estudo Sensorial", expanded=True):
    with st.form("ts_briefing_form"):
        c1, c2 = st.columns(2)
        with c1:
            name_val = st.text_input("Nome do Projeto", value=project["name"])
            prod_val = st.text_input("Produto / Estímulo Avaliado", value=project.get("produto_estimulo") or "")
        with c2:
            questions_val = st.text_area("Perguntas de Pesquisa / Hipóteses", value=project.get("questions") or "", height=110)

        historico_val = st.text_area(
            "Histórico do Produto / Contexto do Teste",
            value=project.get("historico") or "",
            placeholder="Descreva o produto, formulação ou embalagem avaliada...",
            height=90,
        )
        problemas_val = st.text_area(
            "Objetivos Centrais do Estudo",
            value=project.get("problemas") or "",
            placeholder="Ex: 1) Medir o engajamento emocional durante o consumo. 2) Comparar valência da fragrância...",
            height=90,
        )

        if st.form_submit_button("💾 Salvar Briefing do Projeto", type="primary"):
            teste_sensorial_db.update_project(
                project_id,
                name=name_val,
                produto_estimulo=prod_val,
                historico=historico_val,
                problemas=problemas_val,
                questions=questions_val,
            )
            st.success("Briefing atualizado com sucesso!")
            st.rerun()

# ------------------------------------------------------------------
# Carregar dados já salvos no DB
# ------------------------------------------------------------------
data = st.session_state.get("ts_data")
if not data:
    data = teste_sensorial_db.get_dataset(project_id)
    st.session_state["ts_data"] = data

# ------------------------------------------------------------------
# Upload / Carregamento
# ------------------------------------------------------------------
st.divider()
st.subheader("📤 Upload ou Carregamento de Dados Sensoriais")

mode = st.radio(
    "Modo de carregamento",
    ["Upload de arquivos", "Caminho da pasta local"],
    index=0,
    key="ts_mode",
)

if mode == "Upload de arquivos":
    st.markdown("Envie os arquivos de dados EEG, Periféricos e PSD:")

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
            "psd_results (.xlsx / .csv)",
            type=["xlsx", "csv"],
            key="ts_psd",
        )

    if ind_file or per_file or psd_file:
        data = load_from_uploads(ind_file, per_file, psd_file)
        st.session_state["ts_data"] = data
        teste_sensorial_db.save_dataset(
            project_id,
            indicadores=data.get("indicadores"),
            perifericos=data.get("perifericos"),
            psd_results=data.get("psd_results"),
        )
        st.success("✅ Arquivos sensoriais salvos no banco de dados do projeto com sucesso!")

else:
    folder = st.text_input(
        "Caminho para pasta de Dados Processados",
        value="",
        placeholder=r"C:\...\2.2.Dados Processados",
        key="ts_folder",
    )
    if folder and Path(folder).exists():
        data = load_from_folder(folder)
        st.session_state["ts_data"] = data
        teste_sensorial_db.save_dataset(
            project_id,
            indicadores=data.get("indicadores"),
            perifericos=data.get("perifericos"),
            psd_results=data.get("psd_results"),
        )
        st.success("✅ Dados da pasta carregados e salvos no banco de dados do projeto!")

# ------------------------------------------------------------------
# Resumo dos dados carregados
# ------------------------------------------------------------------
datasets = {"indicadores", "perifericos", "psd_results"}
if data and datasets.intersection(data):
    st.divider()
    st.subheader("✅ Resumo dos Dados Carregados")

    summary = get_data_summary(data)
    participants = get_participants(data)

    cols = st.columns(4)
    cols[0].metric("Participantes", len(participants))
    cols[1].metric("Indicadores EEG", "Sim" if "indicadores" in data else "Não")
    cols[2].metric("Periféricos (GSR/HR)", "Sim" if "perifericos" in data else "Não")
    cols[3].metric("PSD Spectrogram", "Sim" if "psd_results" in data else "Não")

    if participants:
        with st.expander(f"Participantes ({len(participants)})"):
            st.write(", ".join(participants))

    for key in ("indicadores", "perifericos", "psd_results"):
        if key in data and not data[key].empty:
            df = data[key]
            with st.expander(f"📋 Visualizar `{key}` ({len(df)} linhas)"):
                st.dataframe(df.head(50), use_container_width=True)

# Navegação
st.divider()
col_nav1, col_nav2 = st.columns(2)
with col_nav1:
    if st.button("⬅️ Voltar aos Projetos", use_container_width=True):
        st.switch_page("modules/teste_sensorial/projetos.py")
with col_nav2:
    if st.button("Avançar para Timeline ➡️", use_container_width=True, type="primary"):
        st.switch_page("modules/teste_sensorial/timeline.py")
