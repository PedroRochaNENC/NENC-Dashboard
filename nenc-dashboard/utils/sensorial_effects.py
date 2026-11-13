"""
Painel de efeitos do Teste Sensorial: as amostras dos projetos de uma organização lado a lado.

Para o P&D relacionar bases e fragrâncias aos efeitos no cérebro: em cada índice do catálogo
(`sensorial_design.INDEX_CODES`), o efeito de cada amostra contra o controle do próprio projeto, em
cada etapa de exposição. O efeito é o da comparação pareada que a Análise Geral já faz
(`sensorial_metrics.comparisons`, tipo `vs_controle`): a diferença média com o IC 95%.

A escala que se compara entre estudos é o **dz pareado**: a diferença média dividida pelo desvio das
diferenças por participante. Não tem unidade, então vale entre índices de escalas muito diferentes (a
memória perto de 1e-12, as razões perto de 1) e entre estudos com amostras de tamanhos diferentes. O IC
do dz é o IC t da diferença dividido pelo mesmo desvio: exclui o zero quando o da diferença exclui, mas
não conta a incerteza do desvio (é aproximado). A diferença bruta, na unidade do índice, fica como opção.

Só faz sentido porque todo projeto passa pela mesma limpeza e pelo mesmo critério (fase 1); o que ainda
pode variar por projeto (teste, alfa, modo do PPI, BASE LIMPA, regra de outliers) vai em `config`, e
`comparability` aponta a diferença.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from utils import sensorial_design, sensorial_metrics

EFFECT_COLUMNS = ["condicao", "amostra", "controle", "etapa", "etapa_rotulo", "medida", "n", "diferenca_media",
                  "ic_inf", "ic_sup", "dp_diferenca", "dz", "dz_inf", "dz_sup", "p", "resultado"]
PANEL_COLUMNS = ["projeto_id", "projeto", "base", "categoria", "claims_alvo"] + EFFECT_COLUMNS
SCALES = {"dz": "dz pareado (padronizado)", "bruta": "Diferença média bruta"}
# Etapas em que a amostra age; a de referência fica fora (é antes da exposição).
EFFECT_STAGE_ROLES = ("exposicao", "pos")
# O que muda a conta e precisa ser igual entre os projetos comparados.
_CONFIG_LABELS = {"teste": "teste estatístico", "alfa": "alfa", "ppi": "modo do PPI", "base_limpa": "BASE LIMPA",
                  "regra_spss": "regra de outliers"}


def paired_sd(mean: float, upper: float, n: int) -> float:
    """Desvio das diferenças pareadas, de volta da meia largura do IC 95% t (`sensorial_stats.paired_test`)."""
    from scipy.stats import t

    if n is None or n < 2 or any(value != value for value in (mean, upper)):
        return math.nan
    half_width = float(upper) - float(mean)
    return half_width * math.sqrt(n) / float(t.ppf(0.975, n - 1))


def project_config(settings: Dict[str, Any]) -> Dict[str, Any]:
    """O que da configuração do projeto muda a conta do efeito."""
    stats = settings.get("estatistica") or {}
    cleaning = settings.get("limpeza") or {}
    return {"teste": str(stats.get("teste") or "wilcoxon"), "alfa": float(stats.get("alfa", 0.05)),
            "ppi": str(((settings.get("indices") or {}).get("ppi") or {}).get("modo") or "spss"),
            "base_limpa": bool(cleaning.get("usar_base_limpa", True)),
            "regra_spss": bool(cleaning.get("regra_spss", True))}


def project_effects(model: Dict[str, Any]) -> Dict[str, Any]:
    """Efeito de cada amostra contra o controle, por índice e etapa de exposição.

    Devolve `{"efeitos": DataFrame (EFFECT_COLUMNS), "motivo": str, "config": dict}`; `motivo` diz por que
    o projeto fica fora do painel, vazio quando entra.
    """
    design = model["design"]
    result = {"efeitos": pd.DataFrame(columns=EFFECT_COLUMNS), "motivo": "",
              "config": project_config(model["settings"])}
    control = design.get("controle") or ""
    roles = sensorial_design.condition_roles(design)
    if not control or roles.get(control) != "controle":
        result["motivo"] = "sem controle no Desenho"
        return result
    if not [code for code, role in roles.items() if role == "amostra"]:
        result["motivo"] = "sem amostra no Desenho"
        return result
    if model["eeg"].empty:
        result["motivo"] = "sem EEG gravado"
        return result
    if not model["indices"]:
        result["motivo"] = "sem o PSD por janela (os índices do relatório)"
        return result
    stages = {s["codigo"]: s.get("rotulo") or s["codigo"] for s in design.get("etapas") or []
              if s.get("papel") in EFFECT_STAGE_ROLES}
    table = sensorial_metrics.comparisons(sensorial_metrics.eeg_means(model), design, model["settings"])
    if not table.empty:
        table = table[(table["tipo"] == "vs_controle") & table["medida"].isin(sensorial_design.INDEX_CODES)
                      & table["etapa"].isin(stages)]
    if table.empty:
        result["motivo"] = "nenhuma amostra com EEG pareado com o controle nas etapas de exposição"
        return result
    labels = sensorial_design.condition_labels(design)
    rows = []
    for row in table.itertuples():
        deviation = paired_sd(row.diferenca_media, row.ic_sup, row.n)
        usable = deviation == deviation and deviation > 0
        rows.append({
            "condicao": row.condicao_a, "amostra": labels.get(row.condicao_a, row.condicao_a),
            "controle": labels.get(row.condicao_b, row.condicao_b), "etapa": row.etapa,
            "etapa_rotulo": stages[row.etapa], "medida": row.medida, "n": int(row.n),
            "diferenca_media": row.diferenca_media, "ic_inf": row.ic_inf, "ic_sup": row.ic_sup,
            "dp_diferenca": deviation,
            "dz": row.diferenca_media / deviation if usable else math.nan,
            "dz_inf": row.ic_inf / deviation if usable else math.nan,
            "dz_sup": row.ic_sup / deviation if usable else math.nan,
            "p": row.p, "resultado": row.resultado})
    result["efeitos"] = pd.DataFrame(rows, columns=EFFECT_COLUMNS)
    return result


def panel_frame(entries: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    """As linhas de todos os projetos com a ficha de cada amostra.

    Cada entrada é `{"projeto": dict do projeto, "efeitos": DataFrame, "fichas": {codigo: ficha}}`. Sem
    categoria na ficha, vale a do projeto.
    """
    parts = []
    for entry in entries:
        effects = entry["efeitos"]
        if effects is None or effects.empty:
            continue
        project = entry["projeto"]
        sheets = entry.get("fichas") or {}
        frame = effects.copy()
        frame.insert(0, "projeto_id", int(project["id"]))
        frame.insert(1, "projeto", str(project.get("name") or ""))
        frame.insert(2, "base", [(sheets.get(code) or {}).get("base", "") for code in frame["condicao"]])
        frame.insert(3, "categoria", [(sheets.get(code) or {}).get("categoria") or str(project.get("categoria") or "")
                                      for code in frame["condicao"]])
        frame.insert(4, "claims_alvo", [list((sheets.get(code) or {}).get("claims_alvo") or [])
                                        for code in frame["condicao"]])
        parts.append(frame)
    if not parts:
        return pd.DataFrame(columns=PANEL_COLUMNS)
    return pd.concat(parts, ignore_index=True)[PANEL_COLUMNS]


def filter_options(frame: pd.DataFrame) -> Dict[str, List[str]]:
    """Valores dos filtros: bases, categorias, claims-alvo e etapas presentes."""
    if frame.empty:
        return {"bases": [], "categorias": [], "claims": [], "etapas": []}
    claims = sorted({claim for claims in frame["claims_alvo"] for claim in claims}, key=str.casefold)
    return {"bases": sorted({b for b in frame["base"] if b}, key=str.casefold),
            "categorias": sorted({c for c in frame["categoria"] if c}, key=str.casefold),
            "claims": claims,
            "etapas": list(dict.fromkeys(frame["etapa_rotulo"]))}


def apply_filters(frame: pd.DataFrame, bases: Sequence[str] = (), categorias: Sequence[str] = (),
                  claims: Sequence[str] = (), etapas: Sequence[str] = (), medidas: Sequence[str] = ()
                  ) -> pd.DataFrame:
    """Recorte do painel; filtro vazio não recorta. Claim-alvo: a amostra tem pelo menos um dos escolhidos."""
    if frame.empty:
        return frame
    mask = pd.Series(True, index=frame.index)
    if bases:
        mask &= frame["base"].isin(list(bases))
    if categorias:
        mask &= frame["categoria"].isin(list(categorias))
    if claims:
        wanted = {claim.casefold() for claim in claims}
        mask &= frame["claims_alvo"].map(lambda items: any(item.casefold() in wanted for item in items))
    if etapas:
        mask &= frame["etapa_rotulo"].isin(list(etapas))
    if medidas:
        mask &= frame["medida"].isin(list(medidas))
    return frame[mask]


def comparability(configs: Dict[str, Dict[str, Any]]) -> List[str]:
    """O que difere na configuração dos projetos comparados (nome do projeto -> `project_config`)."""
    notes = []
    for key, label in _CONFIG_LABELS.items():
        values: Dict[Any, List[str]] = {}
        for project, config in configs.items():
            values.setdefault(config.get(key), []).append(project)
        if len(values) > 1:
            notes.append("{} difere: {}.".format(label[0].upper() + label[1:], "; ".join(
                "{} em {}".format(_config_value(key, value), ", ".join(names)) for value, names in values.items())))
    return notes


def _config_value(key: str, value: Any) -> str:
    if key == "teste":
        return {"wilcoxon": "Wilcoxon", "t_pareado": "t pareado"}.get(value, str(value))
    if key == "ppi":
        return {"spss": "como no SPSS", "z": "padronizado"}.get(value, str(value))
    if key == "alfa":
        return str(value).replace(".", ",")
    return "sim" if value else "não"


def scale_columns(scale: str) -> Dict[str, str]:
    """Colunas do efeito e do IC na escala escolhida."""
    if scale == "bruta":
        return {"efeito": "diferenca_media", "inf": "ic_inf", "sup": "ic_sup"}
    return {"efeito": "dz", "inf": "dz_inf", "sup": "dz_sup"}


def catalog_names() -> Dict[str, str]:
    """Nome de cada índice no relatório (o do catálogo: o painel junta projetos que podem ter renomeado)."""
    return {item["codigo"]: item["nome"] for item in sensorial_design.INDEX_CATALOG}


def selected_projects(projects: Sequence[Dict[str, Any]], organization_id: Optional[int]) -> List[Dict[str, Any]]:
    """Os projetos de uma organização só: o painel nunca mistura clientes."""
    if not organization_id:
        return []
    return [project for project in projects if int(project.get("organization_id") or 0) == int(organization_id)]
