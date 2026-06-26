"""
Prosódia — Lista de Projetos.

Ponto de entrada do módulo de prosódia.
Exibe todos os projetos criados e permite criar, abrir ou excluir.
"""

import streamlit as st

from utils.prosodia_db import init_db, get_projects, delete_project

# Garantir que o banco está inicializado
init_db()

st.title("🎙️ Prosódia — Projetos")
st.markdown(
    "Organize suas análises de prosódia em **projetos** (campanhas). "
    "Cada projeto agrupa entrevistas com contexto compartilhado e "
    "base de conhecimento unificada."
)

# ------------------------------------------------------------------
# Botão novo projeto
# ------------------------------------------------------------------
col_title, col_btn = st.columns([4, 1])
with col_btn:
    if st.button("➕ Novo Projeto", type="primary", width='stretch'):
        st.session_state.pop("pros_project_id", None)
        st.switch_page("modules/prosodia/preparacao.py")

# ------------------------------------------------------------------
# Configuração da Integração WhatsApp
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# Painel de Controle WhatsApp Integration
# ------------------------------------------------------------------
with st.container(border=True):
    st.markdown("### 📱 Integração com WhatsApp API")
    
    from utils.whatsapp_api_client import is_configured, test_connection
    
    c_status, c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
    
    with c_status:
        if is_configured():
            success, _ = test_connection()
            if success:
                st.markdown(
                    """
                    <div style="display: flex; align-items: center; gap: 8px; height: 38px;">
                        <span style="height: 10px; width: 10px; background-color: #2ecc71; border-radius: 50%; display: inline-block; animation: pulse 1.5s infinite;"></span>
                        <strong style="color: #2ecc71; font-size: 14px;">API Conectada</strong>
                    </div>
                    <style>
                    @keyframes pulse {
                        0% { transform: scale(0.9); opacity: 0.7; }
                        50% { transform: scale(1.1); opacity: 1; }
                        100% { transform: scale(0.9); opacity: 0.7; }
                    }
                    </style>
                    """,
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    """
                    <div style="display: flex; align-items: center; gap: 8px; height: 38px;">
                        <span style="height: 10px; width: 10px; background-color: #e74c3c; border-radius: 50%; display: inline-block;"></span>
                        <strong style="color: #e74c3c; font-size: 14px;">API Offline</strong>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
        else:
            st.markdown(
                """
                <div style="display: flex; align-items: center; gap: 8px; height: 38px;">
                    <span style="height: 10px; width: 10px; background-color: #f1c40f; border-radius: 50%; display: inline-block;"></span>
                    <strong style="color: #f1c40f; font-size: 14px;">Não Configurada</strong>
                </div>
                """,
                unsafe_allow_html=True
            )
            
    with c_btn1:
        if st.button("👤 Contatos", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_contatos.py")
            
    with c_btn2:
        if st.button("📢 Campanhas", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_campanhas.py")
            
    with c_btn3:
        if st.button("📡 Monitor", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_monitor.py")
            
    with c_btn4:
        if st.button("⚙️ Configurar", use_container_width=True):
            st.switch_page("modules/prosodia/whatsapp_config.py")

st.divider()

# ------------------------------------------------------------------
# Lista de projetos
# ------------------------------------------------------------------
projects = get_projects()

if not projects:
    st.info(
        "Nenhum projeto criado ainda. "
        "Clique em **➕ Novo Projeto** para começar."
    )
else:
    for proj in projects:
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([4, 1, 1, 1, 1])

            with c1:
                n = proj.get("n_audios", 0)
                st.markdown(f"**{proj['name']}**")
                st.caption(
                    f"🗂️ {n} entrevista(s)  •  📅 {proj['created_at'][:10]}"
                    + (f"  •  _{proj['especialidade'][:60]}…_" if proj.get("especialidade") else "")
                )

            with c2:
                st.write("")
                if st.button("📂 Abrir", key=f"open_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/entrevistas.py")

            with c3:
                st.write("")
                if st.button("✏️ Editar", key=f"edit_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/preparacao.py")

            with c4:
                st.write("")
                if st.button("📤 Uploads", key=f"uploads_{proj['id']}", width='stretch'):
                    st.session_state["pros_project_id"] = proj["id"]
                    st.switch_page("modules/prosodia/audios.py")

            with c5:
                st.write("")
                if st.button("🗑️ Excluir", key=f"del_{proj['id']}", width='stretch'):
                    st.session_state[f"confirm_del_{proj['id']}"] = True

            # Confirmação de exclusão
            if st.session_state.get(f"confirm_del_{proj['id']}"):
                st.warning(
                    f"Tem certeza que deseja excluir **{proj['name']}**? "
                    "Todas as entrevistas e análises serão removidas permanentemente."
                )
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ Confirmar exclusão", key=f"confirm_yes_{proj['id']}", width='stretch'):
                        delete_project(proj["id"])
                        st.session_state.pop(f"confirm_del_{proj['id']}", None)
                        st.rerun()
                with cc2:
                    if st.button("❌ Cancelar", key=f"confirm_no_{proj['id']}", width='stretch'):
                        st.session_state.pop(f"confirm_del_{proj['id']}", None)
                        st.rerun()
