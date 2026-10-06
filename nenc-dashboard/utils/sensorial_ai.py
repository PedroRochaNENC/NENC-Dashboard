"""
IA da Análise Geral do Teste Sensorial: gerar, conversar e enviar à base.

O núcleo é o mesmo da Jornada (`utils/project_ai.py`); aqui ficam os prompts do
módulo e o texto do recorte. `create` e `add` são injetáveis, para testar sem a
OpenAI, e a base só entra quando a tela passa o vector store do Teste Sensorial
da organização.
"""

from typing import Callable, Dict, List, Optional

from utils import project_ai
from utils.project_ai import AI_MODELS, MODE_LABELS  # noqa: F401 (nomes que a página usa daqui)
from utils.sensorial_prompts import (
    build_chat_system_prompt,
    build_sensorial_project_user_prompt,
    build_strategic_user_prompt,
    get_sensorial_project_system_prompt,
)

MODULE_KEY = "teste_sensorial"
MODULE_LABEL = "Teste Sensorial"
KB_PREFIX = "analise_geral_ts_"


def filters_text(filters: Optional[Dict]) -> str:
    """O recorte em uma linha, para a tela, o prompt e as exportações."""
    profile = (filters or {}).get("perfil") or {}
    parts = ["{} = {}".format(field, ", ".join(str(v) for v in values)) for field, values in profile.items()]
    if (filters or {}).get("comparar_por"):
        parts.append("comparando grupos de {}".format(filters["comparar_por"]))
    return "; ".join(parts) or "todo o projeto"


def generate_analysis(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    mode: str,
    ai_model: str,
    recorte: str,
    vector_store_id: Optional[str] = None,
    create: Callable = project_ai.default_create,
) -> Dict:
    """Roda o modo pedido; devolve {"text", "citations", "search"} (ver `project_ai.run_modes`)."""
    base = build_sensorial_project_user_prompt(project, model, metrics, recorte=recorte)
    return project_ai.run_modes(
        base,
        system_prompt=lambda kind: get_sensorial_project_system_prompt(kind, model.get("index_names") or {}),
        strategic_prompt=build_strategic_user_prompt,
        mode=mode,
        ai_model=ai_model,
        module_key=MODULE_KEY,
        project_id=project.get("id"),
        vector_store_id=vector_store_id,
        create=create,
    )


def send_analysis_to_kb(project: Dict, analysis: Dict, recorte: str, vector_store_id: str, *,
                        add: Callable = project_ai.default_add) -> str:
    """Envia a análise à base marcada como análise deste projeto; devolve o id do arquivo."""
    return project_ai.send_analysis_to_kb(project, analysis, recorte, vector_store_id, module_key=MODULE_KEY,
                                          module_label=MODULE_LABEL, kb_prefix=KB_PREFIX, add=add)


def chat_answer(report_text: str, metrics: Dict, history: List[Dict], *, ai_model: str,
                project_id: Optional[int] = None, vector_store_id: Optional[str] = None,
                create: Callable = project_ai.default_create) -> str:
    """Resposta do chat sobre a análise; `history` termina na pergunta do usuário."""
    return project_ai.chat_answer(build_chat_system_prompt(report_text, metrics), history, ai_model=ai_model,
                                  module_key=MODULE_KEY, project_id=project_id, vector_store_id=vector_store_id,
                                  create=create)
