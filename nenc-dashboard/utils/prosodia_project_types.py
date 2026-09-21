"""
Tipos de projeto do NencBoost.

O tipo escolhe o prompt das análises de IA e os limiares das checagens de
qualidade. Fica num módulo sem dependências porque prosodia_quality e
prosodia_prompts são puros e não devem importar a camada de banco.
"""

from typing import Optional

ENTREVISTA_QUALITATIVA = "entrevista_qualitativa"
PESQUISA_OPINIAO = "pesquisa_opiniao"

PROJECT_TYPE_LABELS = {
    ENTREVISTA_QUALITATIVA: "Entrevista Qualitativa",
    PESQUISA_OPINIAO: "Pesquisa de Opinião (Feedback)",
}

# Todo projeto anterior à coluna tipo_projeto foi tratado como entrevista.
DEFAULT_PROJECT_TYPE = ENTREVISTA_QUALITATIVA


def normalize_project_type(value: Optional[str]) -> str:
    """Slug válido do tipo; vazio ou desconhecido vira o padrão."""
    if isinstance(value, str) and value in PROJECT_TYPE_LABELS:
        return value
    return DEFAULT_PROJECT_TYPE
