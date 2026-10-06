"""
Desenho de um estudo do Teste Sensorial: condições, etapas, índices, claims
e os padrões da análise.

Os padrões vêm em camadas: o que está no código (`DEFAULT_SETTINGS`) e, por
cima, o que o projeto guardou em `settings_json`. O desenho em si (quais
sessões são o basal, o controle e cada amostra; quais etapas entram) é
deduzido dos dados quando o projeto não diz nada: a amostra da sessão vira a
condição, sessão sem amostra num experimento "basal" é o basal, e "N" ou um
experimento "neutro" é o controle. Quem revisa pode corrigir pelo projeto.
"""

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from utils.jornada_taxonomy import fold, normalize_label

CONDITION_ROLES = ("basal", "controle", "amostra", "ignorar")
STAGE_ROLES = ("referencia", "exposicao", "pos", "ignorar")
BASAL = "Basal"

DEFAULT_SETTINGS: Dict[str, Any] = {
    # Vazio: deduzido dos dados. Preenchido: condicoes, mapa, etapas, referencia, controle.
    "desenho": {},
    "indices": {
        "nomes": {},  # código -> nome de negócio do projeto
        "ppi": {"modo": "z", "pesos": {"FAI": 0.4, "ATTENTION_IDX_MID": 0.3, "MEMORY_IDX": 0.3}},
    },
    "limpeza": {
        # A BASE LIMPA do SPSS, quando enviada, decide as janelas e tentativas de cada camada.
        "usar_base_limpa": True,
        # Regra de outliers da sintaxe do SPSS (ln, z, Mahalanobis), desligada por padrão.
        "regra_spss": False,
        "z": 3.29,
        "p_mahalanobis": 0.001,
        "min_marcas": 5,
    },
    "perifericos": {
        # Sem BASE LIMPA dos periféricos, janela com fluxo corrompido ou sem pulso não entra.
        "exigir_qualidade": True,
    },
    "associacao": {
        "agrupar": {},  # rótulo -> condições somadas, ex.: {"B": ["B1", "B2"]}
        "faixas": {"muito_alta": 0.2, "alta": 0.0, "baixa": -0.1},
    },
    # Claim -> indicadores que o confirmam: [{"palavra", "indicadores", "corpo"}].
    "claims": [],
    "estatistica": {"min_pares": 5},
}

# Índices do relatório, calculados do PSD por janela (utils/sensorial_indices.py).
INDEX_CATALOG = (
    {"codigo": "FAI", "nome": "Valência emocional",
     "descricao": "Assimetria frontal de alfa, log10(F4) − log10(F3): positivo indica aproximação e atratividade."},
    {"codigo": "TEMP_ASYM", "nome": "Memória olfativa",
     "descricao": "Assimetria temporal de alfa, log10(T4) − log10(T3): processamento límbico e memória olfativa."},
    {"codigo": "ALPHA_THETA", "nome": "Relaxamento",
     "descricao": "Razão alfa/teta em C3, C4, P3 e P4: relaxamento e cognição passiva."},
    {"codigo": "BETA_GAMMA", "nome": "Processamento posterior",
     "descricao": "Razão beta/gama em P3, P4 e T3 a T6: engajamento cognitivo posterior."},
    {"codigo": "ATTENTION_IDX", "nome": "Atenção",
     "descricao": "Média de beta e gama sobre a de alfa e teta em F3, F4, P3 e P4: foco."},
    {"codigo": "ATTENTION_IDX_MID", "nome": "Atenção integrada",
     "descricao": "O índice de atenção com Fz e Cz da linha média."},
    {"codigo": "MEMORY_IDX", "nome": "Memória e associação",
     "descricao": "Média de teta e gama em P3, P4, T5 e T6 (potência absoluta)."},
    {"codigo": "AROUSAL_FRONT", "nome": "Energia e prontidão",
     "descricao": "Razão beta/alfa em Fp1, Fp2, F3, F4 e Fz: ativação cortical anterior."},
    {"codigo": "MIDLINE_AROUSAL", "nome": "Alerta e foco",
     "descricao": "Razão beta/alfa em Fz e Cz."},
    {"codigo": "PPI", "nome": "Índice preditivo geral",
     "descricao": "0,4 × valência + 0,3 × atenção integrada + 0,3 × memória, com os componentes padronizados "
                  "(ou como na sintaxe do SPSS, se o projeto pedir)."},
)
INDEX_CODES = tuple(item["codigo"] for item in INDEX_CATALOG)

PERIPHERAL_CATALOG = (
    {"codigo": "BPM", "nome": "Frequência cardíaca", "camada": "fc"},
    {"codigo": "RMSSD", "nome": "Variabilidade cardíaca", "camada": "fc"},
    {"codigo": "GSR_CAL_mean", "nome": "Condutância da pele", "camada": "gsr"},
    {"codigo": "Emotional_Index", "nome": "Índice emocional", "camada": "gsr"},
    {"codigo": "Comfort_Score", "nome": "Conforto", "camada": "fc"},
)
PERIPHERAL_CODES = tuple(item["codigo"] for item in PERIPHERAL_CATALOG)

# Nomes legíveis para as colunas mais usadas dos indicadores do pipeline.
PIPELINE_LABELS = {
    "atencao": "Atenção (pipeline)",
    "WTP": "Disposição a pagar (pipeline)",
    "engagement_score": "Engajamento (pipeline)",
    "AWI_frontal": "Assimetria frontal AWI (pipeline)",
    "Memoria": "Memória (pipeline)",
}

_STAGE_LABELS = {"basal": "Basal", "olfacao": "Olfação", "posolfacao": "Pós-olfação"}
_SAMPLE_CODE = re.compile(r"^[A-Za-z]+\d*")


# ---------------------------------------------------------------------------
# Padrões em camadas
# ---------------------------------------------------------------------------

def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict) and key not in ("desenho", "agrupar"):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def resolve_settings(project_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Os padrões do código com o que o projeto guardou por cima."""
    return _merge(DEFAULT_SETTINGS, project_settings or {})


def index_names(settings: Dict[str, Any]) -> Dict[str, str]:
    """Código -> nome de negócio (o do projeto, ou o do catálogo)."""
    names = {item["codigo"]: item["nome"] for item in INDEX_CATALOG}
    names.update({item["codigo"]: item["nome"] for item in PERIPHERAL_CATALOG})
    names.update(PIPELINE_LABELS)
    names.update({str(k): str(v) for k, v in (settings.get("indices", {}).get("nomes") or {}).items() if v})
    return names


# ---------------------------------------------------------------------------
# Condições e etapas
# ---------------------------------------------------------------------------

def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return normalize_label(value)


def sample_code(amostra: object) -> str:
    """`B1-IAT e Prosodia` vira `B1`; o código da amostra é o começo do rótulo."""
    text = _text(amostra)
    match = _SAMPLE_CODE.match(text)
    return match.group() if match else text


def condition_key(experimento: object, amostra: object) -> str:
    return "{}|{}".format(_text(experimento), _text(amostra))


def _role_for(experimento: str, code: str) -> str:
    if not code:
        if "basal" in fold(experimento):
            return "basal"
        return "controle" if "neutro" in fold(experimento) else "ignorar"
    if code.upper() == "N" or "neutro" in fold(experimento):
        return "controle"
    return "amostra"


def _condition_label(code: str, role: str) -> str:
    if role == "basal":
        return BASAL
    if role == "controle":
        return "Neutro" if code.upper() == "N" else code
    return "Amostra {}".format(code)


def stage_label(code: str) -> str:
    return _STAGE_LABELS.get(fold(code).replace(" ", ""), code)


def _stage_role(code: str) -> str:
    key = fold(code).replace(" ", "")
    if key.startswith("basal"):
        return "referencia"
    if key.startswith("pos"):
        return "pos"
    return "exposicao"


def _manifest_stages(manifests: Sequence[Dict[str, Any]]) -> List[str]:
    stages: List[str] = []
    for manifest in manifests:
        standard = (((manifest.get("configuracao") or {}).get("PROJETO") or {}).get("etapas") or {}).get(
            "padronizar") or {}
        for target in standard.values():
            if isinstance(target, str) and target not in stages:
                stages.append(target)
    return stages


def deduce_design(sessions: pd.DataFrame, stages: Iterable[str],
                  manifests: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Desenho proposto a partir das sessões (`experimento`, `amostra`) e das etapas vistas nos dados."""
    seen = sessions[["experimento", "amostra"]].drop_duplicates() if not sessions.empty else pd.DataFrame(
        columns=["experimento", "amostra"])
    conditions: Dict[str, Dict[str, str]] = {}
    mapping: Dict[str, str] = {}
    unnamed_controls: List[str] = []  # experimento "neutro" sem amostra: vai para o controle que houver
    for experimento, amostra in seen.itertuples(index=False):
        code = sample_code(amostra)
        role = _role_for(_text(experimento), code)
        if role == "ignorar":
            mapping[condition_key(experimento, amostra)] = ""
            continue
        if role == "controle" and not code:
            unnamed_controls.append(condition_key(experimento, amostra))
            continue
        code = code or BASAL
        mapping[condition_key(experimento, amostra)] = code
        current = conditions.get(code)
        if current is None or current["papel"] == "amostra" and role != "amostra":
            conditions[code] = {"codigo": code, "rotulo": _condition_label(code, role), "papel": role}
    if unnamed_controls:
        control = next((c["codigo"] for c in conditions.values() if c["papel"] == "controle"), "N")
        conditions.setdefault(control, {"codigo": control, "rotulo": _condition_label(control, "controle"),
                                        "papel": "controle"})
        mapping.update({key: control for key in unnamed_controls})
    order = {"basal": 0, "controle": 1, "amostra": 2}
    ordered = sorted(conditions.values(), key=lambda item: (order[item["papel"]], item["codigo"]))

    present = [str(stage) for stage in stages if str(stage).strip()]
    chosen = [stage for stage in _manifest_stages(manifests) if stage in present]
    if not chosen:
        chosen = [stage for stage in present
                  if re.match(r"^(basal|olfa|posolfa|pos olfa)", fold(stage).replace(">", " "))]
    chosen.sort(key=lambda stage: ("referencia", "exposicao", "pos").index(_stage_role(stage)))
    stage_rows = [{"codigo": stage, "rotulo": stage_label(stage), "papel": _stage_role(stage)} for stage in chosen]
    basal = next((c["codigo"] for c in ordered if c["papel"] == "basal"), "")
    control = next((c["codigo"] for c in ordered if c["papel"] == "controle"), "")
    reference_stage = next((s["codigo"] for s in stage_rows if s["papel"] == "referencia"), "")
    return {
        "condicoes": ordered,
        "mapa": mapping,
        "etapas": stage_rows,
        "referencia": {"condicao": basal, "etapa": reference_stage},
        "controle": control,
    }


def resolve_design(settings: Dict[str, Any], deduced: Dict[str, Any]) -> Dict[str, Any]:
    """O desenho do projeto por cima do deduzido: o que a pessoa escreveu vale."""
    chosen = settings.get("desenho") or {}
    design = copy.deepcopy(deduced)
    for key in ("condicoes", "etapas", "referencia", "controle"):
        if chosen.get(key):
            design[key] = copy.deepcopy(chosen[key])
    design["mapa"] = dict(deduced.get("mapa") or {}, **(chosen.get("mapa") or {}))
    return design


def condition_for(experimento: object, amostra: object, design: Dict[str, Any]) -> str:
    """Código da condição de uma sessão, ou "" quando ela fica fora da análise."""
    mapping = design.get("mapa") or {}
    key = condition_key(experimento, amostra)
    if key in mapping:
        return mapping[key] or ""
    code = sample_code(amostra)
    known = {c["codigo"] for c in design.get("condicoes") or []}
    return code if code in known else ""


def condition_roles(design: Dict[str, Any]) -> Dict[str, str]:
    return {c["codigo"]: c["papel"] for c in design.get("condicoes") or []}


def condition_labels(design: Dict[str, Any]) -> Dict[str, str]:
    return {c["codigo"]: c["rotulo"] for c in design.get("condicoes") or []}


def analysis_stages(design: Dict[str, Any]) -> List[str]:
    return [s["codigo"] for s in design.get("etapas") or [] if s.get("papel") != "ignorar"]


def default_claims(words: Iterable[str]) -> List[Dict[str, Any]]:
    """Um claim por palavra do teste de associação, ainda sem indicadores ligados."""
    seen = sorted({normalize_label(word) for word in words if normalize_label(word)})
    return [{"palavra": word, "indicadores": [], "corpo": []} for word in seen]


def resolve_claims(settings: Dict[str, Any], words: Iterable[str]) -> List[Dict[str, Any]]:
    """Os claims do projeto e, para as palavras que ele não citou, um claim vazio."""
    claims = [dict(item) for item in settings.get("claims") or [] if item.get("palavra")]
    named = {fold(item["palavra"]) for item in claims}
    claims += [item for item in default_claims(words) if fold(item["palavra"]) not in named]
    return claims
