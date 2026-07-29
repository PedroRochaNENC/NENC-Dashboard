"""
Teste Sensorial — Lista de Projetos.

Ponto de entrada do módulo de Teste Sensorial.
Exibe todos os projetos criados e permite criar, abrir, editar ou excluir.
"""

import streamlit as st
from utils import auth

user = auth.require_module("teste_sensorial")

from utils import teste_sensorial_db

teste_sensorial_db.init_db()

st.title("🧪 Teste Sensorial — Projetos")
st.markdown(
    "Organize suas pesquisas de neuromarketing e testes sensoriais em **projetos**. "
    "Cada projeto agrupa métricas de EEG (atenção, valência, assimetria), dados periféricos "
    "(GSR, frequência cardíaca), PSD e base de conhecimento do produto."
)

# ------------------------------------------------------------------
# Botão Novo Projeto
# ------------------------------------------------------------------
col_title, col_btn = st.columns([4, 1])
with col_btn:
    if st.button("➕ Novo Projeto", type="primary", use_container_width=True):
        st.session_state.pop("ts_project_id", None)
        st.switch_page("modules/teste_sensorial/preparacao.py")

# ------------------------------------------------------------------
# Migração de Dados Legados
# ------------------------------------------------------------------
legacy_data = st.session_state.get("ts_data")
if legacy_data and not st.session_state.get("ts_project_id"):
    with st.expander("📦 Dados de EEG/Periféricos não salvos no Session State", expanded=True):
        st.warning(
            "Encontramos dados sensoriais carregados na sessão atual. "
            "Você pode salvá-los como um novo Projeto no banco de dados."
        )
        mig_name = st.text_input("Nome do Projeto Sensorial", value="Projeto Sensorial (Importado)", key="ts_mig_name")
        if st.button("💾 Salvar como Novo Projeto"):
            pid = teste_sensorial_db.create_project(name=mig_name)
            teste_sensorial_db.save_dataset(
                pid,
                indicadores=legacy_data.get("indicadores"),
                perifericos=legacy_data.get("perifericos"),
                psd_results=legacy_data.get("psd_results"),
            )
            st.session_state["ts_project_id"] = pid
            st.success(f"Projeto '{mig_name}' criado com sucesso!")
            st.rerun()

st.divider()

# ------------------------------------------------------------------
# Lista de Projetos
# ------------------------------------------------------------------
projects = teste_sensorial_db.get_projects()

if not projects:
    st.info("Nenhum projeto de Teste Sensorial cadastrado ainda. Clique em **➕ Novo Projeto** para começar.")
else:
    active_project_id = st.session_state.get("ts_project_id")

    for proj in projects:
        p_id = proj["id"]
        is_active = (active_project_id == p_id)
        
        with st.container(border=True):
            c_header, c_actions = st.columns([3, 1])
            
            with c_header:
                active_badge = "🟢 **[ATIVO]** " if is_active else ""
                st.markdown(f"### {active_badge}{proj['name']}")
                meta_parts = []
                if proj.get("produto_estimulo"):
                    meta_parts.append(f"🧪 **Produto/Estímulo:** {proj['produto_estimulo']}")
                meta_parts.append(f"📊 **Datasets:** {proj['total_datasets']}")
                meta_parts.append(f"🧠 **Análises IA:** {proj['total_analyses']}")
                meta_parts.append(f"📅 **Atualizado em:** {proj['updated_at']}")
                st.caption(" | ".join(meta_parts))

            with c_actions:
                if not is_active:
                    if st.button("📂 Abrir Projeto", key=f"open_ts_{p_id}", use_container_width=True, type="primary"):
                        st.session_state["ts_project_id"] = p_id
                        st.session_state.pop("ts_data", None)
                        st.switch_page("modules/teste_sensorial/preparacao.py")
                else:
                    st.success("Projeto Selecionado")
                    if st.button("📋 Ir para Dados", key=f"goto_ts_{p_id}", use_container_width=True):
                        st.switch_page("modules/teste_sensorial/preparacao.py")

                with st.popover("⚙️ Opções", use_container_width=True):
                    if st.button("✏️ Editar Briefing", key=f"edit_ts_{p_id}", use_container_width=True):
                        st.session_state["ts_project_id"] = p_id
                        st.switch_page("modules/teste_sensorial/preparacao.py")
                    
                    if st.button("🗑️ Excluir Projeto", key=f"del_ts_{p_id}", use_container_width=True):
                        st.session_state[f"confirm_del_ts_{p_id}"] = True

                    if st.session_state.get(f"confirm_del_ts_{p_id}"):
                        st.error("Tem certeza que deseja excluir?")
                        if st.button("🔥 Confirmar Exclusão", key=f"conf_del_ts_{p_id}", type="primary"):
                            teste_sensorial_db.delete_project(p_id)
                            if st.session_state.get("ts_project_id") == p_id:
                                st.session_state.pop("ts_project_id", None)
                                st.session_state.pop("ts_data", None)
                            st.success("Projeto excluído.")
                            st.rerun()
