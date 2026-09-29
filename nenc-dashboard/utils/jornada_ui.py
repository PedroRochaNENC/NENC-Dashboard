"""
Pedaços de tela repetidos nas páginas de projeto da Jornada de Compra.
"""

import math

import streamlit as st

from utils import jornada_db

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


def fmt_seconds(value) -> str:
    """"1:21" para 81 s; vazio para NaN."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(seconds):
        return ""
    minutes, rest = divmod(int(round(seconds)), 60)
    return "{}:{:02d}".format(minutes, rest)


def fmt_number(value, digits: int = 1, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number):
        return ""
    return "{:.{d}f}{s}".format(number, d=digits, s=suffix).replace(".", ",")


def fmt_pct(value, digits: int = 0) -> str:
    """Proporção (0..1) como porcentagem pt-BR."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number):
        return ""
    return fmt_number(100 * number, digits, "%")
