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

# # ------------------------------------------------------------------
# # Configuração da Integração WhatsApp
# # ------------------------------------------------------------------
# with st.expander("🔗 Configuração da Integração WhatsApp API", expanded=False):
#     import os
#     from pathlib import Path
#     from dotenv import load_dotenv, set_key

#     _env_path = Path(__file__).resolve().parent.parent / ".env"
#     load_dotenv(_env_path)

#     wa_col1, wa_col2 = st.columns(2)
#     with wa_col1:
#         wa_url = st.text_input(
#             "URL da API WhatsApp",
#             value=st.session_state.get("whatsapp_api_url", os.getenv("WHATSAPP_API_URL", "")),
#             placeholder="http://localhost:8000",
#             key="wa_cfg_url",
#         )
#     with wa_col2:
#         wa_key = st.text_input(
#             "Chave da API WhatsApp",
#             value=st.session_state.get("whatsapp_api_key", os.getenv("WHATSAPP_API_KEY", "")),
#             type="password",
#             key="wa_cfg_key",
#         )

#     if st.button("💾 Salvar Configuração WhatsApp", key="wa_cfg_save"):
#         st.session_state["whatsapp_api_url"] = wa_url.strip()
#         st.session_state["whatsapp_api_key"] = wa_key.strip()
#         # Persistir no .env
#         try:
#             set_key(str(_env_path), "WHATSAPP_API_URL", wa_url.strip())
#             set_key(str(_env_path), "WHATSAPP_API_KEY", wa_key.strip())
#             os.environ["WHATSAPP_API_URL"] = wa_url.strip()
#             os.environ["WHATSAPP_API_KEY"] = wa_key.strip()
#             st.success("✅ Configuração salva com sucesso!")
#         except Exception as e:
#             st.error(f"Erro ao salvar .env: {e}")

# st.divider()

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
