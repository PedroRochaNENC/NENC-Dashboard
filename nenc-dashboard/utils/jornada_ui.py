"""
Pedaços de tela repetidos nas páginas de projeto da Jornada de Compra.
"""

import streamlit as st

from utils import jornada_db


def active_project() -> dict:
    """Projeto aberto, ou para a página oferecendo o caminho de volta.

    O id vem da sessão; se ele apontar para um projeto que sumiu (ou de outra
    organização), a sessão é limpa para o menu voltar ao nível sem projeto.
    """
    project_id = st.session_state.get("jc_project_id")
    project = jornada_db.get_project(project_id) if project_id else None
    if not project:
        st.session_state.pop("jc_project_id", None)
        st.warning("Nenhum projeto selecionado.")
        if st.button("Ir para Projetos", type="primary"):
            st.switch_page("modules/jornada_compra/projetos.py")
        st.stop()
    return project
