"""
Pedaços de tela comuns às páginas de projeto dos módulos (Jornada de Compra,
Teste Sensorial).
"""

from types import ModuleType

import streamlit as st


def active_project(data_module: ModuleType, state_key: str, projects_page: str) -> dict:
    """Projeto aberto, ou para a página oferecendo o caminho de volta.

    O id vem da sessão (`state_key`); se ele apontar para um projeto que sumiu
    (ou de outra organização), a sessão é limpa para o menu voltar ao nível sem
    projeto. `data_module` precisa ter `get_project(project_id)`, que já
    respeita a organização ativa.
    """
    project_id = st.session_state.get(state_key)
    project = data_module.get_project(project_id) if project_id else None
    if not project:
        st.session_state.pop(state_key, None)
        st.warning("Nenhum projeto selecionado.")
        if st.button("Ir para Projetos", type="primary"):
            st.switch_page(projects_page)
        st.stop()
    return project
