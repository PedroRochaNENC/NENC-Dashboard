"""
Pedaços de tela repetidos nas páginas de projeto da Jornada de Compra.
"""

import streamlit as st

from utils import jornada_db
from utils.jornada_format import fmt_number, fmt_pct, fmt_seconds  # noqa: F401 - usados pelas paginas

STATUS_COLORS = {
    "incluida": "rgba(95,191,159,.18)",
    "agregado": "rgba(106,169,217,.18)",
    "excluida": "rgba(233,196,106,.18)",
    "sem_aoi": "rgba(147,151,171,.14)",
    "nao_codificada": "rgba(224,116,139,.20)",
    "ausente": "rgba(0,0,0,0)",
}
QUALITY_ICONS = {"pass": "OK", "warn": "Atenção", "fail": "Problema"}


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
