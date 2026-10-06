"""
IA da Análise Geral dos módulos por projeto: o núcleo comum.

Cada módulo monta os prompts dele (`utils/jornada_prompts.py`,
`utils/sensorial_prompts.py`); aqui ficam os modos, o documento que vai para a
base de conhecimento e o chat. `create` e `add` são injetáveis, para testar
sem a OpenAI. A base só entra quando a tela passa um vector store — a página
não passa nenhum em "Todas as organizações", onde a base resolvida seria a da
organização de quem está logado, não a do projeto.
"""

from typing import Callable, Dict, List, Optional

from utils.excel_export import safe_slug
from utils.kb_attributes import ESCOPO_ANALISE, build_kb_filter, project_document

AI_MODELS = ("gpt-4.1-mini", "gpt-4.1", "gpt-4o")
MODE_LABELS = {"rapida": "rápida", "aprofundada": "aprofundada"}


def default_create(**kwargs):
    from utils.ai_provider import create_analysis

    return create_analysis(**kwargs)


def default_add(*args, **kwargs):
    from utils.ai_provider import add_document_to_vector_store

    return add_document_to_vector_store(*args, **kwargs)


def run_modes(
    base_prompt: str,
    *,
    system_prompt: Callable[[str], str],
    strategic_prompt: Callable[[str, str], str],
    mode: str,
    ai_model: str,
    module_key: str,
    project_id: Optional[int],
    vector_store_id: Optional[str],
    create: Callable,
) -> Dict:
    """Roda o modo pedido; devolve {"text", "citations", "search"}.

    Rápida: uma chamada. Aprofundada: leitura estatística sem a base (ela lê
    só as tabelas) e interpretação estratégica com a base, que recebe a leitura
    e o mesmo prompt-base. `system_prompt(modo)` dá o prompt de sistema de
    "rapida", "estatistica" e "estrategica".
    """
    kb_filter = build_kb_filter(project_id, modulo=module_key) if vector_store_id else None
    if mode == "rapida":
        result = create(system_prompt=system_prompt("rapida"), user_prompt=base_prompt, model=ai_model,
                        vector_store_id=vector_store_id, kb_filter=kb_filter, temperature=0.4, max_tokens=4000)
        text = result.get("text") or ""
    elif mode == "aprofundada":
        statistical = create(system_prompt=system_prompt("estatistica"), user_prompt=base_prompt, model=ai_model,
                             vector_store_id=None, temperature=0.2, max_tokens=2500)
        result = create(system_prompt=system_prompt("estrategica"),
                        user_prompt=strategic_prompt(base_prompt, statistical.get("text") or ""), model=ai_model,
                        vector_store_id=vector_store_id, kb_filter=kb_filter, temperature=0.4, max_tokens=4000)
        text = "## Leitura estatística\n\n{}\n\n---\n\n## Interpretação estratégica\n\n{}".format(
            (statistical.get("text") or "").strip(), (result.get("text") or "").strip())
    else:
        raise ValueError("Modo de análise desconhecido: {!r}".format(mode))
    return {"text": text, "citations": result.get("citations") or [], "search": result.get("search") or {}}


def analysis_markdown(project: Dict, analysis: Dict, recorte: str, module_label: str) -> str:
    """A análise como documento: o que vai para a base de conhecimento."""
    lines = [
        "# Análise Geral — {}".format(project.get("name") or "Projeto"),
        "",
        "- Módulo: {}".format(module_label),
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


def kb_filename(project: Dict, analysis: Dict, prefix: str) -> str:
    return "{}{}_{}.md".format(prefix, safe_slug(project.get("name") or "projeto"), analysis.get("id"))


def send_analysis_to_kb(
    project: Dict,
    analysis: Dict,
    recorte: str,
    vector_store_id: str,
    *,
    module_key: str,
    module_label: str,
    kb_prefix: str,
    add: Callable,
) -> str:
    """Envia a análise à base marcada como análise deste projeto; devolve o id do arquivo."""
    if not vector_store_id:
        raise ValueError("A base de conhecimento do módulo {} não está configurada nesta organização.".format(
            module_label))
    stored = add(
        vector_store_id,
        kb_filename(project, analysis, kb_prefix),
        analysis_markdown(project, analysis, recorte, module_label).encode("utf-8"),
        project_document(module_key, project.get("id"), escopo=ESCOPO_ANALISE, tipo="analise_geral",
                         analysis_id=analysis.get("id")),
        wait=False,
    )
    file_id = getattr(stored, "id", None) or (stored.get("id") if isinstance(stored, dict) else None)
    if not file_id:
        raise RuntimeError("A base não devolveu o identificador do arquivo enviado.")
    return str(file_id)


def chat_answer(
    system_prompt: str,
    history: List[Dict],
    *,
    ai_model: str,
    module_key: str,
    project_id: Optional[int] = None,
    vector_store_id: Optional[str] = None,
    create: Callable,
) -> str:
    """Resposta do chat sobre a análise; `history` termina na pergunta do usuário."""
    conversation = "\n\n".join(
        "{}: {}".format("Usuário" if message["role"] == "user" else "Assistente", message["content"])
        for message in history
    )
    result = create(
        system_prompt=system_prompt,
        user_prompt=conversation,
        model=ai_model,
        vector_store_id=vector_store_id,
        kb_filter=build_kb_filter(project_id, modulo=module_key) if vector_store_id else None,
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
