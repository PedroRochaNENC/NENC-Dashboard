"""
Tipos de projeto do NencBoost e faixas de duração esperada dos áudios.

O tipo escolhe o prompt das análises de IA e quais checagens de qualidade
rodam; a faixa de duração calibra os limiares dessas checagens. Ficam num
módulo sem dependências porque prosodia_quality e prosodia_prompts são puros e
não devem importar a camada de banco.
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


# Duração típica de cada áudio do projeto, do início ao fim da gravação. Os
# limiares de cada faixa ficam em prosodia_quality.thresholds_for_duracao.
DURACAO_ESPERADA_LABELS = {
    "ate_30s": "Até 30 s",
    "30s_1min": "30 s a 1 min",
    "1_3min": "1 a 3 min",
    "3_10min": "3 a 10 min",
    "10_30min": "10 a 30 min",
    "30_60min": "30 min a 1 h",
    "acima_1h": "Mais de 1 h",
}


def normalize_duracao_esperada(value: Optional[str]) -> Optional[str]:
    """Slug válido da faixa; None quando o projeto não tem uma.

    Projetos anteriores à faixa ficam sem ela e seguem com os limiares do tipo.
    """
    if isinstance(value, str) and value in DURACAO_ESPERADA_LABELS:
        return value
    return None
