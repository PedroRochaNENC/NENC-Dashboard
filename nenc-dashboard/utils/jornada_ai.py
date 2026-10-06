"""
IA da Análise Geral da Jornada de Compra: gerar, conversar e enviar à base.

O núcleo (modos, documento da base e chat) é comum aos módulos por projeto e
fica em `utils/project_ai.py`; aqui ficam os prompts da Jornada. `create` e
`add` são injetáveis, para testar sem a OpenAI. Só OpenAI (o fallback Groq do
NencBoost ficou de fora), e a base de conhecimento só entra quando a tela passa
um vector store — a página não passa nenhum em "Todas as organizações", onde a
base resolvida seria a da organização de quem está logado, não a do projeto.
"""

from typing import Callable, Dict, List, Optional, Sequence

from utils import project_ai
from utils.jornada_prompts import (
    build_chat_system_prompt,
    build_jornada_project_user_prompt,
    build_strategic_user_prompt,
    get_jornada_project_system_prompt,
)
from utils.project_ai import AI_MODELS, MODE_LABELS  # noqa: F401 (nomes que a página usa daqui)

MODULE_KEY = "jornada_compra"
MODULE_LABEL = "Jornada de Compra"
KB_PREFIX = "analise_geral_jc_"


def _default_create(**kwargs):
    return project_ai.default_create(**kwargs)


def _default_add(*args, **kwargs):
    return project_ai.default_add(*args, **kwargs)


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
    """Roda o modo pedido; devolve {"text", "citations", "search"} (ver `project_ai.run_modes`)."""
    tasks = (model.get("meta") or {}).get("tasks") or []
    base = build_jornada_project_user_prompt(
        project, metrics, recorte=recorte, model=model, quality=quality, interviews=interviews,
    )
    return project_ai.run_modes(
        base,
        system_prompt=lambda kind: get_jornada_project_system_prompt(kind, tasks),
        strategic_prompt=build_strategic_user_prompt,
        mode=mode,
        ai_model=ai_model,
        module_key=MODULE_KEY,
        project_id=project.get("id"),
        vector_store_id=vector_store_id,
        create=create,
    )


def analysis_markdown(project: Dict, analysis: Dict, recorte: str) -> str:
    """A análise como documento: o que vai para a base de conhecimento."""
    return project_ai.analysis_markdown(project, analysis, recorte, MODULE_LABEL)


def kb_filename(project: Dict, analysis: Dict) -> str:
    return project_ai.kb_filename(project, analysis, KB_PREFIX)


def send_analysis_to_kb(
    project: Dict,
    analysis: Dict,
    recorte: str,
    vector_store_id: str,
    *,
    add: Callable = _default_add,
) -> str:
    """Envia a análise à base marcada como análise deste projeto; devolve o id do arquivo."""
    return project_ai.send_analysis_to_kb(project, analysis, recorte, vector_store_id, module_key=MODULE_KEY,
                                          module_label=MODULE_LABEL, kb_prefix=KB_PREFIX, add=add)


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
    return project_ai.chat_answer(build_chat_system_prompt(report_text, metrics), history, ai_model=ai_model,
                                  module_key=MODULE_KEY, project_id=project_id, vector_store_id=vector_store_id,
                                  create=create)
