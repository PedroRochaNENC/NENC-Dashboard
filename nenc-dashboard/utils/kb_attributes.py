"""
Contrato dos atributos dos documentos da base de conhecimento.

Uma organizacao tem um vector store so, e nele convivem a literatura de
referencia e o material de cada projeto — transcricao de entrevista, briefing,
analises geradas. Sem atributo nenhum para separar os dois, a analise de um
projeto alcanca a entrevista de outro projeto da mesma organizacao.

Estes atributos sao o que separa. Quem escreve na base usa `reference_document`
ou `project_document`; quem le monta o filtro com `build_kb_filter`.

Cuidado de ordem: documento sem atributo nao casa com filtro nenhum e some da
busca. Antes de ligar o filtro em producao, `scripts/backfill_kb_attributes.py`
precisa ter carimbado o acervo que ja existe.
"""

from typing import Any, Optional

ESCOPO_REFERENCIA = "referencia"
ESCOPO_PROJETO = "projeto"
ESCOPO_ANALISE = "analise"

ESCOPOS_DE_PROJETO = (ESCOPO_PROJETO, ESCOPO_ANALISE)
ESCOPOS = (ESCOPO_REFERENCIA,) + ESCOPOS_DE_PROJETO


def reference_document(modulo: str, **extras: Any) -> dict:
    """Atributos de literatura: vale para todos os projetos da organizacao."""

    attributes: dict = {"escopo": ESCOPO_REFERENCIA, "modulo": modulo}
    attributes.update(extras)
    return attributes


def project_document(
    modulo: str,
    project_id: Optional[int],
    escopo: str = ESCOPO_PROJETO,
    session_id: Optional[str] = None,
    **extras: Any,
) -> dict:
    """Atributos de material que pertence a um projeto.

    `escopo` distingue o dado bruto do projeto (`projeto`) do que a IA produziu
    a partir dele (`analise`); os dois sao filtrados pelo mesmo `project_id`.
    """

    if escopo not in ESCOPOS_DE_PROJETO:
        raise ValueError("Escopo de projeto invalido: {}.".format(escopo))

    attributes: dict = {"escopo": escopo, "modulo": modulo}
    if project_id:
        attributes["project_id"] = int(project_id)
    if session_id:
        attributes["session_id"] = str(session_id)
    attributes.update(extras)
    return attributes


def build_kb_filter(project_id: Optional[int]) -> Optional[dict]:
    """Filtro "literatura da organizacao, ou material deste projeto".

    Sem projeto aberto devolve None, e a busca segue alcancando a base inteira:
    a tela que nao sabe de qual projeto esta falando nao tem o que isolar.
    """

    if not project_id:
        return None
    return {
        "type": "or",
        "filters": [
            {"type": "eq", "key": "escopo", "value": ESCOPO_REFERENCIA},
            {"type": "eq", "key": "project_id", "value": int(project_id)},
        ],
    }


def belongs_to_project(attributes: Optional[dict], project_id: Optional[int]) -> bool:
    """Se o documento e material deste projeto — a base da limpeza.

    Literatura nunca pertence a um projeto, mesmo que carregue `project_id`:
    ela foi enviada para servir a todos, e apagar junto com um projeto tiraria
    referencia de quem nao pediu.
    """

    if not attributes or not project_id:
        return False
    if attributes.get("escopo") == ESCOPO_REFERENCIA:
        return False
    try:
        return int(attributes.get("project_id")) == int(project_id)
    except (TypeError, ValueError):
        return False
