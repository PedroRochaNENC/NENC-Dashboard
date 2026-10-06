"""
Métricas de um projeto do Teste Sensorial, a partir do modelo.

- **Médias por participante**: cada participante vira um valor por condição e
  etapa (a média das janelas incluídas), para o EEG (índices do relatório e
  indicadores do pipeline) e para os periféricos;
- **Comparações por etapa**, pareadas por participante: amostra × basal (a
  etapa de referência do basal), amostra × controle e amostra × amostra, com
  Wilcoxon, efeito e p de Holm por família (medida × etapa);
- **Curvas no tempo**, média ± erro padrão entre participantes, alinhadas no
  início da etapa ou na primeira cheirada (`curves`);
- **Teste de associação**: % de "Sim", CR das respostas "Sim", Score, faixa e
  quadrante por condição e claim, com agrupamento opcional (ex.: B1+B2);
- **Síntese** por amostra e claim (cérebro, corpo e teste explícito),
  **achados** e **limitações**; recorte e comparação por perfil.

Os filtros são um dict serializável: `{"perfil": {campo: [valores]},
"comparar_por": campo}`.
"""

import itertools
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from utils import sensorial_design, sensorial_stats

_PERIPHERAL_LAYERS = {"BPM": ("fc",), "RMSSD": ("fc",), "Comfort_Score": ("fc",), "GSR_CAL_mean": ("gsr",),
                      "Emotional_Index": ("fc", "gsr")}
_COMPARISON_TYPES = {"vs_basal": "× basal", "vs_controle": "× controle", "entre_amostras": "× amostra"}
_MEANS_COLUMNS = ["participant_code", "condicao", "etapa", "medida", "valor"]


# ---------------------------------------------------------------------------
# Recorte e médias por participante
# ---------------------------------------------------------------------------

def allowed_participants(model: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> Optional[set]:
    """Participantes do recorte por perfil, ou None sem recorte."""
    profile = (filters or {}).get("perfil") or {}
    if not profile:
        return None
    table = model["participants"]
    mask = pd.Series(True, index=table.index)
    for field, values in profile.items():
        column = "perfil_" + str(field)
        wanted = {str(value) for value in (values if isinstance(values, (list, tuple, set)) else [values])}
        mask &= table[column].astype(str).isin(wanted) if column in table else False
    return set(table.loc[mask, "participant_code"].astype(str))


def _restrict(frame: pd.DataFrame, allowed: Optional[set]) -> pd.DataFrame:
    if frame.empty:
        return frame
    keep = frame["participant_code"].notna() & (frame["condicao"] != "")
    if allowed is not None:
        keep &= frame["participant_code"].astype(str).isin(allowed)
    return frame[keep]


def _means(frame: pd.DataFrame, measures: Sequence[str]) -> pd.DataFrame:
    if frame.empty or not measures:
        return pd.DataFrame(columns=_MEANS_COLUMNS)
    grouped = frame.groupby(["participant_code", "condicao", "Etapa"])[list(measures)].mean().reset_index()
    long = grouped.melt(id_vars=["participant_code", "condicao", "Etapa"], var_name="medida", value_name="valor")
    return long.rename(columns={"Etapa": "etapa"}).dropna(subset=["valor"])[_MEANS_COLUMNS]


def eeg_means(model: Dict[str, Any], allowed: Optional[set] = None) -> pd.DataFrame:
    eeg = model["eeg"]
    if eeg.empty:
        return pd.DataFrame(columns=_MEANS_COLUMNS)
    measures = list(model["indices"]) + (["PPI"] if "PPI" in eeg and "PPI" not in model["indices"] else []) \
        + list(model["pipeline_indicators"])
    return _means(_restrict(eeg[eeg["incluida"]], allowed), measures)


def peripheral_means(model: Dict[str, Any], allowed: Optional[set] = None) -> pd.DataFrame:
    peri = model["peripherals"]
    if peri.empty:
        return pd.DataFrame(columns=_MEANS_COLUMNS)
    parts = []
    for measure, layers in _PERIPHERAL_LAYERS.items():
        if measure not in peri:
            continue
        mask = pd.Series(True, index=peri.index)
        for layer in layers:
            mask &= peri["incluida_" + layer]
        parts.append(_means(_restrict(peri[mask], allowed), [measure]))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=_MEANS_COLUMNS)


def summary(means: pd.DataFrame) -> pd.DataFrame:
    """Por medida, condição e etapa: n de participantes, média, desvio, erro padrão e mediana."""
    rows = []
    for (measure, condition, stage), group in means.groupby(["medida", "condicao", "etapa"]):
        rows.append(dict({"medida": measure, "condicao": condition, "etapa": stage},
                         **sensorial_stats.describe(group["valor"])))
    return pd.DataFrame(rows, columns=["medida", "condicao", "etapa", "n", "media", "dp", "ep", "mediana"])


# ---------------------------------------------------------------------------
# Comparações pareadas
# ---------------------------------------------------------------------------

def comparisons(means: pd.DataFrame, design: Dict[str, Any], settings: Dict[str, Any]) -> pd.DataFrame:
    """Amostra × basal, × controle e × amostra, por medida e etapa, com Holm por família."""
    columns = ["medida", "etapa", "tipo", "condicao_a", "condicao_b", "etapa_b", "n", "media_a", "media_b",
               "mediana_a", "mediana_b", "diferenca_mediana", "r", "p", "metodo", "p_holm", "resultado"]
    if means.empty:
        return pd.DataFrame(columns=columns)
    min_pairs = int(settings["estatistica"].get("min_pares", sensorial_stats.MIN_PAIRS))
    alpha = float(settings["estatistica"].get("alfa", sensorial_stats.ALPHA))
    roles = sensorial_design.condition_roles(design)
    reference = design.get("referencia") or {}
    basal, reference_stage = reference.get("condicao"), reference.get("etapa")
    control = design.get("controle") or ""
    samples = [code for code, role in roles.items() if role == "amostra"]
    compared = [code for code, role in roles.items() if role in ("controle", "amostra")]
    pivot = means.pivot_table(index="participant_code", columns=["medida", "condicao", "etapa"], values="valor")

    def values(measure, condition, stage) -> Optional[pd.Series]:
        key = (measure, condition, stage)
        return pivot[key] if key in pivot.columns else None

    rows = []
    for measure in sorted(means["medida"].unique()):
        for stage in sensorial_design.analysis_stages(design):
            pairs = []
            reference_values = values(measure, basal, reference_stage) if basal else None
            for condition in compared:
                pairs.append(("vs_basal", condition, values(measure, condition, stage), basal, reference_values,
                              reference_stage))
            if control:
                control_values = values(measure, control, stage)
                for sample in samples:
                    pairs.append(("vs_controle", sample, values(measure, sample, stage), control, control_values,
                                  stage))
            for first, second in itertools.combinations(samples, 2):
                pairs.append(("entre_amostras", first, values(measure, first, stage), second,
                              values(measure, second, stage), stage))
            family = []
            for kind, condition_a, a, condition_b, b, stage_b in pairs:
                if a is None or b is None or a.dropna().empty:
                    continue
                result = sensorial_stats.paired_test(a, b, min_pairs)
                family.append(dict({"medida": measure, "etapa": stage, "tipo": kind, "condicao_a": condition_a,
                                    "condicao_b": condition_b, "etapa_b": stage_b}, **result))
            for row, adjusted in zip(family, sensorial_stats.holm([row["p"] for row in family])):
                row["p_holm"] = adjusted
                row["resultado"] = sensorial_stats.verdict(row["p"], adjusted, alpha)
            rows += family
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------------------
# Curvas no tempo
# ---------------------------------------------------------------------------

def curves(model: Dict[str, Any], measure: str, alignment: str = "etapa", filters: Optional[Dict] = None,
           bin_s: float = 0.25) -> Dict[str, Any]:
    """Curva média ± erro padrão por condição.

    `alignment="etapa"`: cada etapa no tempo dela. `"olfacao"`: a exposição e a
    etapa seguinte num eixo só, com o zero na primeira cheirada da sessão (sem
    o evento, no início da exposição — contado em `sem_cheirada`).
    """
    design = model["design"]
    eeg = model["eeg"]
    empty = {"curva": pd.DataFrame(columns=["condicao", "etapa", "t", "media", "ep", "n"]), "referencia": None,
             "alinhamento": alignment, "sem_cheirada": 0}
    if eeg.empty or measure not in eeg:
        return empty
    allowed = allowed_participants(model, filters)
    frame = _restrict(eeg[eeg["incluida"]], allowed)[["sessao_id", "participant_code", "condicao", "Etapa", "Tempo",
                                                      "primeira_olfacao_s", measure]].copy()
    stages = {s["codigo"]: s["papel"] for s in design.get("etapas") or []}
    missing_sniff = 0
    if alignment == "olfacao":
        exposure = [code for code, role in stages.items() if role == "exposicao"]
        after = [code for code, role in stages.items() if role == "pos"]
        frame = frame[frame["Etapa"].isin(exposure + after)]
        exposed = frame[frame["Etapa"].isin(exposure)]
        length = exposed.groupby("sessao_id")["Tempo"].max()
        sniff = exposed.groupby("sessao_id")["primeira_olfacao_s"].first()
        missing_sniff = int(sniff.isna().sum())
        offset = frame["sessao_id"].map(length).fillna(0).where(frame["Etapa"].isin(after), 0)
        frame["t"] = frame["Tempo"] + offset - frame["sessao_id"].map(sniff).fillna(0)
        frame["etapa"] = "/".join(exposure + after)
    else:
        frame["t"] = frame["Tempo"]
        frame["etapa"] = frame["Etapa"]
    frame["t"] = (np.floor(frame["t"] / bin_s) * bin_s).round(3)
    per_participant = frame.groupby(["condicao", "etapa", "t", "participant_code"])[measure].mean().reset_index()
    rows = []
    for (condition, stage, t), group in per_participant.groupby(["condicao", "etapa", "t"]):
        described = sensorial_stats.describe(group[measure])
        rows.append({"condicao": condition, "etapa": stage, "t": t, "media": described["media"],
                     "ep": described["ep"], "n": described["n"]})
    reference = design.get("referencia") or {}
    basal = frame if alignment == "etapa" else _restrict(eeg[eeg["incluida"]], allowed)
    basal = basal[(basal["condicao"] == reference.get("condicao")) & (basal["Etapa"] == reference.get("etapa"))]
    reference_row = None
    if not basal.empty:
        reference_row = dict(sensorial_stats.describe(basal.groupby("participant_code")[measure].mean()),
                             condicao=reference.get("condicao"), etapa=reference.get("etapa"))
    return {"curva": pd.DataFrame(rows, columns=["condicao", "etapa", "t", "media", "ep", "n"]),
            "referencia": reference_row, "alinhamento": alignment, "sem_cheirada": missing_sniff}


# ---------------------------------------------------------------------------
# Teste de associação
# ---------------------------------------------------------------------------

def _band(score: float, pct: float, bands: Dict[str, float]) -> str:
    if pct == 0 or score != score:
        return "Muito baixa"
    if score >= bands["muito_alta"]:
        return "Muito alta"
    if score > bands["alta"]:
        return "Alta"
    if score > bands["baixa"]:
        return "Baixa"
    return "Muito baixa"


def _quadrant(pct: float, cr: float, cuts: Dict[str, float]) -> str:
    adheres = pct >= cuts["pct_sim"]
    convinced = cr == cr and cr >= cuts["cr"]
    if adheres and convinced:
        return "Dominante"
    if adheres:
        return "Potencial"
    if convinced:
        return "Nicho"
    return "Sem aderência"


def association_table(trials: pd.DataFrame, settings: Dict[str, Any], groups: Optional[Dict[str, Sequence[str]]] = None
                      ) -> pd.DataFrame:
    """Por condição (ou grupo de condições) e claim: % de "Sim", CR do "Sim", Score, faixa e quadrante."""
    columns = ["condicao", "palavra", "tentativas", "participantes", "pct_sim", "cr_sim", "score", "faixa",
               "quadrante"]
    if trials.empty:
        return pd.DataFrame(columns=columns)
    frame = trials.copy()
    if groups:
        member = {condition: label for label, members in groups.items() for condition in members}
        frame["condicao"] = frame["condicao"].map(lambda c: member.get(c, c))
    bands = settings["associacao"]["faixas"]
    cuts = settings["associacao"]["quadrantes"]
    rows = []
    for (condition, word), group in frame.groupby(["condicao", "palavra"]):
        pct = float(group["sim"].mean())
        said_yes = group.loc[group["sim"], "cr"].dropna()
        cr = float(said_yes.mean()) if not said_yes.empty else math.nan
        score = pct * cr if cr == cr else (0.0 if pct == 0 else math.nan)
        rows.append({"condicao": condition, "palavra": word, "tentativas": int(len(group)),
                     "participantes": int(group["participant_code"].nunique()), "pct_sim": pct, "cr_sim": cr,
                     "score": score, "faixa": _band(score, pct, bands), "quadrante": _quadrant(pct, cr, cuts)})
    return pd.DataFrame(rows, columns=columns).sort_values(["condicao", "score"], ascending=[True, False],
                                                           ignore_index=True)


# ---------------------------------------------------------------------------
# Síntese, achados e limitações
# ---------------------------------------------------------------------------

def _support(table: pd.DataFrame, sample: str, codes: Iterable[str], exposure_stages: Sequence[str]) -> str:
    """Se as medidas ligadas ao claim confirmam o efeito esperado na amostra."""
    codes = [str(code) for code in codes if str(code).strip()]
    if not codes:
        return "sem indicador"
    if table.empty:
        return "sem dado"
    best = "não confirma"
    seen = False
    for code in codes:
        sign = -1 if code.startswith("-") else 1
        measure = code.lstrip("+-")
        rows = table[(table["medida"] == measure) & (table["condicao_a"] == sample)
                     & table["etapa"].isin(exposure_stages)]
        preferred = rows[rows["tipo"] == "vs_controle"]
        rows = preferred if not preferred.empty else rows[rows["tipo"] == "vs_basal"]
        for row in rows.itertuples():
            seen = True
            right_way = row.diferenca_mediana == row.diferenca_mediana and np.sign(row.diferenca_mediana) == sign
            if row.resultado == sensorial_stats.DIFFERENCE and right_way:
                return "confirma"
            if row.resultado == sensorial_stats.TREND and right_way:
                best = "tendência"
    return best if seen else "sem dado"


def synthesis(model: Dict[str, Any], eeg_table: pd.DataFrame, peripheral_table: pd.DataFrame,
              association: pd.DataFrame) -> pd.DataFrame:
    """Por amostra e claim: o que cérebro, corpo e teste explícito dizem, e a conclusão."""
    design = model["design"]
    samples = [c["codigo"] for c in design.get("condicoes") or [] if c["papel"] == "amostra"]
    exposure = [s["codigo"] for s in design.get("etapas") or [] if s.get("papel") in ("exposicao", "pos")]
    explicit = {(row.condicao, row.palavra): row.faixa for row in association.itertuples()} \
        if not association.empty else {}
    rows = []
    for sample in samples:
        for claim in model["claims"]:
            brain = _support(eeg_table, sample, claim.get("indicadores") or [], exposure)
            body = _support(peripheral_table, sample, claim.get("corpo") or [], exposure)
            band = explicit.get((sample, claim["palavra"]))
            said = "sem teste" if band is None else ("confirma" if band in ("Muito alta", "Alta") else "não confirma")
            physiological = "confirma" in (brain, body)
            if said == "confirma" and physiological:
                conclusion = "validado"
            elif said == "confirma" or physiological or "tendência" in (brain, body):
                conclusion = "parcial"
            else:
                conclusion = "não validado"
            rows.append({"condicao": sample, "palavra": claim["palavra"], "cerebro": brain, "corpo": body,
                         "explicito": said, "faixa": band or "", "conclusao": conclusion})
    return pd.DataFrame(rows, columns=["condicao", "palavra", "cerebro", "corpo", "explicito", "faixa", "conclusao"])


def _fmt(value: float, digits: int = 3) -> str:
    return "{:.{}g}".format(value, digits).replace(".", ",") if value == value else "—"


def findings(tables: Dict[str, pd.DataFrame], model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Diferenças (Holm) e tendências (p bruto), em frases curtas, das camadas fisiológicas."""
    names = model["index_names"]
    labels = sensorial_design.condition_labels(model["design"])
    stage_labels = {s["codigo"]: s["rotulo"] for s in model["design"].get("etapas") or []}
    items = []
    for layer, table in tables.items():
        if table.empty:
            continue
        chosen = table[table["resultado"].isin([sensorial_stats.DIFFERENCE, sensorial_stats.TREND])]
        for row in chosen.itertuples():
            direction = "maior" if row.diferenca_mediana > 0 else "menor"
            text = "{} {} {}: {} {} na {} (diferença mediana {}; r = {}; p de Holm = {})".format(
                labels.get(row.condicao_a, row.condicao_a), _COMPARISON_TYPES[row.tipo].split(" ")[0],
                labels.get(row.condicao_b, row.condicao_b), names.get(row.medida, row.medida), direction,
                stage_labels.get(row.etapa, row.etapa), _fmt(row.diferenca_mediana), _fmt(row.r, 2),
                _fmt(row.p_holm, 2))
            items.append({"camada": layer, "tipo": row.resultado, "medida": row.medida, "condicao": row.condicao_a,
                          "comparacao": row.condicao_b, "etapa": row.etapa, "p_holm": row.p_holm, "texto": text})
    items.sort(key=lambda item: (item["tipo"] != sensorial_stats.DIFFERENCE, item["p_holm"]
                                 if item["p_holm"] == item["p_holm"] else 1.0))
    return items


def limitations(model: Dict[str, Any], counts: pd.DataFrame, allowed: Optional[set]) -> List[str]:
    notes: List[str] = []
    settings = model["settings"]
    min_pairs = int(settings["estatistica"].get("min_pares", sensorial_stats.MIN_PAIRS))
    labels = sensorial_design.condition_labels(model["design"])
    for row in counts.itertuples():
        if 0 < row.eeg < min_pairs:
            notes.append("{}: só {} participante(s) com EEG; as comparações ficam descritivas.".format(
                labels.get(row.condicao, row.condicao), row.eeg))
    if model["eeg"].empty:
        notes.append("Sem EEG gravado no projeto.")
    elif not model["indices"]:
        notes.append("Sem o PSD por janela: só os indicadores do pipeline, sem os índices do relatório.")
    if model["peripherals"].empty:
        notes.append("Sem métricas dos periféricos.")
    if model["trials"].empty:
        notes.append("Sem o teste de associação.")
    notes.append("Limpeza: {}{}.".format(
        "BASE LIMPA do SPSS onde enviada; " if settings["limpeza"].get("usar_base_limpa", True) else "",
        "regra de outliers ligada" if settings["limpeza"].get("regra_spss") else "regra de outliers desligada"))
    if settings["indices"]["ppi"].get("modo", "z") == "z":
        notes.append("O PPI usa os componentes padronizados; a fórmula do SPSS dá outra escala.")
    if allowed is not None:
        notes.append("Recorte por perfil: {} participante(s).".format(len(allowed)))
    notes += [issue["message"] for issue in model["issues"] if issue["level"] == "warn"]
    return notes


def profile_comparison(model: Dict[str, Any], means: pd.DataFrame, field: str) -> pd.DataFrame:
    """Grupos de um campo de perfil comparados em cada índice, condição e etapa (entre sujeitos)."""
    from utils.jornada_metrics import compare_groups

    column = "perfil_" + str(field)
    participants = model["participants"]
    if not field or column not in participants or means.empty:
        return pd.DataFrame()
    group_of = participants.set_index("participant_code")[column].astype(str)
    data = means[means["medida"].isin(model["indices"])].copy()
    data["grupo"] = data["participant_code"].map(group_of).fillna("")
    parts = []
    for (measure, condition, stage), group in data.groupby(["medida", "condicao", "etapa"]):
        table = compare_groups(group, "valor", "grupo")
        if not table.empty:
            table.insert(0, "etapa", stage)
            table.insert(0, "condicao", condition)
            table.insert(0, "medida", measure)
            parts.append(table)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def compute_all(model: Dict[str, Any], filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Tudo o que as páginas e as exportações mostram, para um recorte."""
    filters = filters or {}
    settings = model["settings"]
    allowed = allowed_participants(model, filters)
    eeg_long = eeg_means(model, allowed)
    peripheral_long = peripheral_means(model, allowed)
    eeg_table = comparisons(eeg_long, model["design"], settings)
    peripheral_table = comparisons(peripheral_long, model["design"], settings)
    trials = _restrict(model["trials"][model["trials"]["incluida"]], allowed) if not model["trials"].empty \
        else model["trials"]
    association = association_table(trials, settings)
    pooled = association_table(trials, settings, settings["associacao"].get("agrupar")) \
        if settings["associacao"].get("agrupar") else None

    conditions = [c["codigo"] for c in model["design"].get("condicoes") or []]
    counts = pd.DataFrame({
        "condicao": conditions,
        "eeg": [int(eeg_long.loc[eeg_long["condicao"] == c, "participant_code"].nunique()) for c in conditions],
        "perifericos": [int(peripheral_long.loc[peripheral_long["condicao"] == c, "participant_code"].nunique())
                        for c in conditions],
        "associacao": [int(trials.loc[trials["condicao"] == c, "participant_code"].nunique())
                       if not trials.empty else 0 for c in conditions],
    })
    everyone = set(eeg_long["participant_code"]) | set(peripheral_long["participant_code"])
    if not trials.empty:
        everyone |= set(trials["participant_code"].dropna())
    return {
        "filtros": filters,
        "participantes": len(everyone),
        "n_por_condicao": counts,
        "eeg": {"medias": eeg_long, "resumo": summary(eeg_long), "comparacoes": eeg_table},
        "perifericos": {"medias": peripheral_long, "resumo": summary(peripheral_long),
                        "comparacoes": peripheral_table},
        "associacao": {"por_condicao": association, "agrupado": pooled},
        "sintese": synthesis(model, eeg_table, peripheral_table, association),
        "achados": findings({"eeg": eeg_table, "perifericos": peripheral_table}, model),
        "limitacoes": limitations(model, counts, allowed),
        "perfil": profile_comparison(model, eeg_long, filters.get("comparar_por") or ""),
    }
