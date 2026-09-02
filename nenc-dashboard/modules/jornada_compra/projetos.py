"""
Jornada de Compra — Lista de Projetos.

Ponto de entrada do módulo de Jornada de Compra.
Exibe todos os projetos criados e permite criar, abrir, editar ou excluir.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")

from utils import jornada_db

jornada_db.init_db()

ui.inject_theme()
ui.breadcrumb("Jornada de Compra", "Projetos")
page_title(
    "folders",
    "Projetos",
    "Cada projeto agrupa datasets, entrevistas e análises.",
)
st.markdown(
    "Organize seus estudos de eye-tracking e jornada do consumidor em **projetos**. "
    "Cada projeto agrupa métricas de atenção, share visual por marca, entrevistas "
    "do PDV e base de conhecimento unificada."
)

# ------------------------------------------------------------------
# Botão Novo Projeto
# ------------------------------------------------------------------
col_title, col_btn = st.columns([4, 1])
with col_btn:
    if st.button("Novo Projeto", type="primary", use_container_width=True):
        st.session_state.pop("jc_project_id", None)
        st.switch_page("modules/jornada_compra/preparacao.py")

# ------------------------------------------------------------------
# Migração de Dados Legados (se existirem na sessão)
# ------------------------------------------------------------------
legacy_data = st.session_state.get("jc_data")
if legacy_data and not st.session_state.get("jc_project_id"):
    with st.expander("Dados não salvos detectados no Session State", expanded=True):
        st.warning(
            "Encontramos dados de eye-tracking carregados na sessão atual. "
            "Você pode salvá-los como um novo Projeto para não perder seu progresso."
        )
        mig_name = st.text_input("Nome do Projeto para migração", value="Projeto Jornada de Compra (Importado)", key="jc_mig_name")
        if st.button("Salvar como Novo Projeto"):
            pid = jornada_db.create_project(name=mig_name)
            jornada_db.save_dataset(
                pid,
                tabelas=legacy_data.get("tabelas"),
                por_marca=legacy_data.get("por_marca"),
                medias=legacy_data.get("medias"),
                visual_share=legacy_data.get("visual_share"),
            )
            st.session_state["jc_project_id"] = pid
            st.success(f"Projeto '{mig_name}' criado com sucesso!")
            st.rerun()

st.divider()

# ------------------------------------------------------------------
# Lista de Projetos
# ------------------------------------------------------------------
projects = jornada_db.get_projects()

if not projects:
    st.info("Nenhum projeto cadastrado ainda. Clique em **Novo Projeto** para começar.")
else:
    active_project_id = st.session_state.get("jc_project_id")

    for proj in projects:
        p_id = proj["id"]
        is_active = (active_project_id == p_id)
        
        with st.container(border=True):
            c_header, c_actions = st.columns([3, 1])
            
            with c_header:
                active_badge = "**[ATIVO]** " if is_active else ""
                st.markdown(f"### {active_badge}{proj['name']}")
                meta_parts = []
                if proj.get("categoria"):
                    meta_parts.append(f"**Categoria:** {proj['categoria']}")
                meta_parts.append(f"**Datasets:** {proj['total_datasets']}")
                meta_parts.append(f"**Entrevistas:** {proj['total_interviews']}")
                meta_parts.append(f"**Análises IA:** {proj['total_analyses']}")
                meta_parts.append(f"**Atualizado em:** {proj['updated_at']}")
                st.caption(" | ".join(meta_parts))
                
                if proj.get("marcas"):
                    st.markdown(f"**Marcas no Estudo:** `{proj['marcas']}`")

            with c_actions:
                if not is_active:
                    if st.button("Abrir Projeto", key=f"open_jc_{p_id}", use_container_width=True, type="primary"):
                        st.session_state["jc_project_id"] = p_id
                        # Limpar cache local de sessão para recarregar do banco
                        st.session_state.pop("jc_data", None)
                        st.switch_page("modules/jornada_compra/preparacao.py")
                else:
                    st.success("Projeto Selecionado")
                    if st.button("Ir para Dados", key=f"goto_jc_{p_id}", use_container_width=True):
                        st.switch_page("modules/jornada_compra/preparacao.py")

                with st.popover("Opções", use_container_width=True):
                    if st.button("Editar Briefing", key=f"edit_jc_{p_id}", use_container_width=True):
                        st.session_state["jc_project_id"] = p_id
                        st.switch_page("modules/jornada_compra/preparacao.py")
                    
                    if st.button("Excluir Projeto", key=f"del_jc_{p_id}", use_container_width=True):
                        st.session_state[f"confirm_del_jc_{p_id}"] = True

                    if st.session_state.get(f"confirm_del_jc_{p_id}"):
                        st.error("Tem certeza que deseja excluir?")
                        if st.button("Confirmar Exclusão", key=f"conf_del_jc_{p_id}", type="primary"):
                            jornada_db.delete_project(p_id)
                            if st.session_state.get("jc_project_id") == p_id:
                                st.session_state.pop("jc_project_id", None)
                                st.session_state.pop("jc_data", None)
                            st.success("Projeto excluído.")
                            st.rerun()
