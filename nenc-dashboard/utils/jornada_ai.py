"""
IA da Análise Geral da Jornada de Compra: gerar, conversar e enviar à base.

A lógica fica fora da página para ser testada sem a OpenAI: `create` e `add`
são injetáveis. Só OpenAI (o fallback Groq do NencBoost ficou de fora), e a
base de conhecimento só entra quando a tela passa um vector store — a página
não passa nenhum em "Todas as organizações", onde a base resolvida seria a da
organização de quem está logado, não a do projeto.
"""

from typing import Callable, Dict, List, Optional, Sequence

from utils.excel_export import safe_slug
from utils.jornada_prompts import (
    build_chat_system_prompt,
    build_jornada_project_user_prompt,
    build_strategic_user_prompt,
    get_jornada_project_system_prompt,
)
from utils.kb_attributes import ESCOPO_ANALISE, build_kb_filter, project_document

MODULE_KEY = "jornada_compra"
AI_MODELS = ("gpt-4.1-mini", "gpt-4.1", "gpt-4o")
MODE_LABELS = {"rapida": "rápida", "aprofundada": "aprofundada"}
KB_PREFIX = "analise_geral_jc_"


def _default_create(**kwargs):
    from utils.ai_provider import create_analysis

    return create_analysis(**kwargs)


def _default_add(*args, **kwargs):
    from utils.ai_provider import add_document_to_vector_store

    return add_document_to_vector_store(*args, **kwargs)


def generate_analysis(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    mode: str,
    ai_model: str,
    recorte: str,
    quality: Optional[Dict] = None,
    interviews: Sequence[Dict] = (),
    vector_store_id: Optional[str] = None,
    create: Callable = _default_create,
) -> Dict:
    """Roda o modo pedido; devolve {"text", "citations", "search"}.

    Rápida: uma chamada. Aprofundada: leitura estatística sem a base (ela lê
    só as tabelas) e interpretação estratégica com a base, que recebe a leitura
    e o mesmo prompt-base.
    """
    tasks = (model.get("meta") or {}).get("tasks") or []
    base = build_jornada_project_user_prompt(
        project, metrics, recorte=recorte, model=model, quality=quality, interviews=interviews,
    )
    kb_filter = build_kb_filter(project.get("id"), modulo=MODULE_KEY) if vector_store_id else None
    if mode == "rapida":
        result = create(
            system_prompt=get_jornada_project_system_prompt("rapida", tasks),
            user_prompt=base,
            model=ai_model,
            vector_store_id=vector_store_id,
            kb_filter=kb_filter,
            temperature=0.4,
            max_tokens=4000,
        )
        text = result.get("text") or ""
    elif mode == "aprofundada":
        statistical = create(
            system_prompt=get_jornada_project_system_prompt("estatistica", tasks),
            user_prompt=base,
            model=ai_model,
            vector_store_id=None,
            temperature=0.2,
            max_tokens=2500,
        )
        result = create(
            system_prompt=get_jornada_project_system_prompt("estrategica", tasks),
            user_prompt=build_strategic_user_prompt(base, statistical.get("text") or ""),
            model=ai_model,
            vector_store_id=vector_store_id,
            kb_filter=kb_filter,
            temperature=0.4,
            max_tokens=4000,
        )
        text = "## Leitura estatística\n\n{}\n\n---\n\n## Interpretação estratégica\n\n{}".format(
            (statistical.get("text") or "").strip(), (result.get("text") or "").strip())
    else:
        raise ValueError("Modo de análise desconhecido: {!r}".format(mode))
    return {"text": text, "citations": result.get("citations") or [], "search": result.get("search") or {}}


def analysis_markdown(project: Dict, analysis: Dict, recorte: str) -> str:
    """A análise como documento: o que vai para a base de conhecimento."""
    lines = [
        "# Análise Geral — {}".format(project.get("name") or "Projeto"),
        "",
        "- Módulo: Jornada de Compra",
        "- Gerada em: {}".format(analysis.get("created_at") or ""),
        "- Modo: {}".format(MODE_LABELS.get(analysis.get("mode"), analysis.get("mode") or "")),
        "- Modelo: {}".format(analysis.get("model") or ""),
        "- Recorte: {}".format(recorte),
        "",
        str(analysis.get("analysis_text") or "").strip(),
    ]
    citations = analysis.get("citations") or []
    if citations:
        lines += ["", "## Referências"]
        for citation in citations:
            data = citation if isinstance(citation, dict) else {"quote": str(citation)}
            quote = str(data.get("quote") or "").strip()
            lines.append("- {}{}".format(data.get("filename") or "Documento", ": \"{}\"".format(quote) if quote else ""))
    return "\n".join(lines) + "\n"


def kb_filename(project: Dict, analysis: Dict) -> str:
    return "{}{}_{}.md".format(KB_PREFIX, safe_slug(project.get("name") or "projeto"), analysis.get("id"))


def send_analysis_to_kb(
    project: Dict,
    analysis: Dict,
    recorte: str,
    vector_store_id: str,
    *,
    add: Callable = _default_add,
) -> str:
    """Envia a análise à base marcada como análise deste projeto; devolve o id do arquivo."""
    if not vector_store_id:
        raise ValueError("A base de conhecimento da Jornada não está configurada nesta organização.")
    stored = add(
        vector_store_id,
        kb_filename(project, analysis),
        analysis_markdown(project, analysis, recorte).encode("utf-8"),
        project_document(MODULE_KEY, project.get("id"), escopo=ESCOPO_ANALISE, tipo="analise_geral",
                         analysis_id=analysis.get("id")),
        wait=False,
    )
    file_id = getattr(stored, "id", None) or (stored.get("id") if isinstance(stored, dict) else None)
    if not file_id:
        raise RuntimeError("A base não devolveu o identificador do arquivo enviado.")
    return str(file_id)


def chat_answer(
    report_text: str,
    metrics: Dict,
    history: List[Dict],
    *,
    ai_model: str,
    project_id: Optional[int] = None,
    vector_store_id: Optional[str] = None,
    create: Callable = _default_create,
) -> str:
    """Resposta do chat sobre a análise; `history` termina na pergunta do usuário."""
    conversation = "\n\n".join(
        "{}: {}".format("Usuário" if message["role"] == "user" else "Assistente", message["content"])
        for message in history
    )
    result = create(
        system_prompt=build_chat_system_prompt(report_text, metrics),
        user_prompt=conversation,
        model=ai_model,
        vector_store_id=vector_store_id,
        kb_filter=build_kb_filter(project_id, modulo=MODULE_KEY) if vector_store_id else None,
        temperature=0.3,
        max_tokens=1500,
    )
    answer = (result.get("text") or "").strip()
    citations = result.get("citations") or []
    if citations:
        answer += "\n\n**Referências da base de conhecimento:**"
        for citation in citations:
            quote = (citation.get("quote") or "").strip()
            answer += "\n- *{}*{}".format(citation.get("filename") or "Documento",
                                          ": \"{}\"".format(quote[:300]) if quote else "")
    return answer
