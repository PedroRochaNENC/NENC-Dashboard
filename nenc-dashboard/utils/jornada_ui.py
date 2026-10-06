"""
Pedaços de tela repetidos nas páginas de projeto da Jornada de Compra.
"""

from utils import jornada_db, project_ui
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
    """Projeto aberto da Jornada, ou para a página oferecendo o caminho de volta."""
    return project_ui.active_project(jornada_db, "jc_project_id", "modules/jornada_compra/projetos.py")
