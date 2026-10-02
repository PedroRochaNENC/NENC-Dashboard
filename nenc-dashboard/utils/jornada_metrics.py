"""
Métricas da Jornada de Compra.

Funções puras sobre o modelo (`utils/jornada_model.py`). Entram só as
gravações `incluida`; as não codificadas e as excluídas nunca aparecem em
denominador nenhum. A unidade de análise é a CÉLULA — tarefa × loja — porque
cada loja tem a sua gôndola; métricas por marca também saem por tarefa com
todas as lojas juntas, já que a marca existe em todas.

Definições (R = gravações incluídas da célula; D = tempo; S = fração da
gravação, sem unidade; K = tipos de AOI da marca, produto por padrão):

- share (principal): média, por gravação com olhar em K, de S(r,b) / S(r);
  share ponderada: Σ D(r,b) / Σ D(r) — sempre rotulada, ela dá mais peso a
  quem ficou mais tempo diante da gôndola;
- alcance: gravações que olharam b / |R| (quem não olhou nenhum produto conta);
- funil: notou → examinou (D(r,b) ≥ limiar) → retornou (alguma AOI da marca
  com 2+ visitas; por AOI, porque partes vizinhas somariam visitas falsas);
- TTFF: mediana e quartis entre quem olhou; relativo à primeira marca vista
  (na jornada livre o absoluto inclui a caminhada até a categoria);
- primeira marca notada: pela ordem do TTFF bruto, que vale sem unidade;
  empates dividem o crédito;
- índice de presença: share / (peso da marca na gôndola / peso total), com o
  peso manual do catálogo ou 1 por AOI (rotulado como aproximação).

Com menos de 5 gravações por grupo, as comparações ficam descritivas: o teste
de permutação só roda com amostra que o sustente.
"""

import itertools
import math
from collections import Counter
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from utils.jornada_ingest import TASK_LABELS
from utils.jornada_taxonomy import DEFAULT_ELEMENT_LABELS, element_label, fold

MIN_N_TEST = 5
ALL_STORES = "*"
VARIABLE_LABELS = {"channel": "Canal", "task": "Tarefa", "store": "Loja", "profile": "Perfil"}


# ---------------------------------------------------------------------------
# Estatistica
# ---------------------------------------------------------------------------

def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """δ de Cliff: P(a > b) − P(a < b), de −1 a 1."""

    a = np.asarray([x for x in a if x == x], dtype=float)
    b = np.asarray([x for x in b if x == x], dtype=float)
    if not a.size or not b.size:
        return math.nan
    greater = (a[:, None] > b[None, :]).sum()
    less = (a[:, None] < b[None, :]).sum()
    return float((greater - less) / (a.size * b.size))


def permutation_p_value(
    a: Sequence[float], b: Sequence[float], n_perm: int = 20000, seed: int = 0
) -> tuple:
    """p bicaudal da diferença de médias. Exato enquanto couber em `n_perm`."""

    a = np.asarray([x for x in a if x == x], dtype=float)
    b = np.asarray([x for x in b if x == x], dtype=float)
    if not a.size or not b.size:
        return math.nan, ""
    pooled = np.concatenate([a, b])
    observed = abs(a.mean() - b.mean())
    tolerance = 1e-12
    total = math.comb(pooled.size, a.size)
    if total <= n_perm:
        hits = 0
        indices = range(pooled.size)
        pooled_sum = pooled.sum()
        for combo in itertools.combinations(indices, a.size):
            group_sum = pooled[list(combo)].sum()
            diff = abs(group_sum / a.size - (pooled_sum - group_sum) / b.size)
            hits += diff >= observed - tolerance
        return hits / total, "permutação exata"
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        shuffled = rng.permutation(pooled)
        diff = abs(shuffled[: a.size].mean() - shuffled[a.size:].mean())
        hits += diff >= observed - tolerance
    return (hits + 1) / (n_perm + 1), "permutação Monte Carlo"


def compare_groups(
    values: pd.DataFrame,
    value_col: str,
    group_col: str,
    *,
    min_n_test: int = MIN_N_TEST,
    n_perm: int = 20000,
    seed: int = 0,
) -> pd.DataFrame:
    """Comparação par a par entre os grupos, com n, efeito e p quando cabe."""

    columns = ["group_a", "group_b", "n_a", "n_b", "mean_a", "mean_b", "median_a", "median_b",
               "diff", "cliffs_delta", "p_value", "method"]
    data = values[[value_col, group_col]].dropna()
    groups = [g for g in sorted(data[group_col].unique()) if g != ""]
    rows = []
    for first, second in itertools.combinations(groups, 2):
        a = data.loc[data[group_col] == first, value_col].to_numpy(dtype=float)
        b = data.loc[data[group_col] == second, value_col].to_numpy(dtype=float)
        if min(a.size, b.size) >= min_n_test:
            p_value, method = permutation_p_value(a, b, n_perm=n_perm, seed=seed)
        else:
            p_value, method = math.nan, "descritivo"
        rows.append({
            "group_a": first, "group_b": second, "n_a": int(a.size), "n_b": int(b.size),
            "mean_a": float(a.mean()) if a.size else math.nan,
            "mean_b": float(b.mean()) if b.size else math.nan,
            "median_a": float(np.median(a)) if a.size else math.nan,
            "median_b": float(np.median(b)) if b.size else math.nan,
            "diff": float(a.mean() - b.mean()) if a.size and b.size else math.nan,
            "cliffs_delta": cliffs_delta(a, b),
            "p_value": p_value,
            "method": method,
        })
    return pd.DataFrame(rows, columns=columns)


def design_confounds(recordings: pd.DataFrame, variables=("channel", "task", "store", "profile")) -> List[Dict]:
    """Pares de variáveis que andam juntos um-para-um: comparar um é comparar o outro."""

    found = []
    if recordings.empty:
        return found
    for first, second in itertools.combinations(variables, 2):
        if first not in recordings or second not in recordings:
            continue
        pairs = recordings[[first, second]].dropna()
        pairs = pairs[(pairs[first] != "") & (pairs[second] != "")].drop_duplicates()
        if pairs[first].nunique() < 2 or pairs[second].nunique() < 2:
            continue
        one_to_one = (pairs.groupby(first)[second].nunique().max() == 1
                      and pairs.groupby(second)[first].nunique().max() == 1)
        if one_to_one:
            mapping = ", ".join(
                "{} = {}".format(
                    TASK_LABELS.get(row[first], row[first]), TASK_LABELS.get(row[second], row[second])
                )
                for _, row in pairs.sort_values(first).iterrows()
            )
            found.append({
                "variables": (first, second),
                "labels": (VARIABLE_LABELS.get(first, first), VARIABLE_LABELS.get(second, second)),
                "mapping": mapping,
            })
    return found


# ---------------------------------------------------------------------------
# Filtros e celulas
# ---------------------------------------------------------------------------

def cell_label(task: str, store_label: str) -> str:
    task_text = TASK_LABELS.get(task, task or "—")
    if store_label in (ALL_STORES, "", None):
        return "{} · todas as lojas".format(task_text)
    return "{} · {}".format(task_text, store_label)


def apply_filters(model: Dict, filters: Optional[Dict] = None) -> Dict:
    """Recorte do modelo: gravações incluídas e o olhar/agregados delas."""

    filters = filters or {}
    recordings = model["recordings"]
    gaze = model["gaze"]
    pooled = model["pooled"]
    included = recordings[recordings["status"] == "incluida"] if not recordings.empty else recordings
    for key, column in (("stores", "store"), ("tasks", "task"), ("profiles", "profile"),
                        ("channels", "channel")):
        chosen = filters.get(key)
        # Projeto só com registro de campo não tem gravações (nem colunas) para recortar.
        if chosen and column in included.columns:
            included = included[included[column].isin(chosen)]
    keys = set(included["recording_key"]) if not included.empty else set()
    if not gaze.empty:
        gaze = gaze[gaze["recording_key"].isin(keys) & gaze["include"].fillna(True).astype(bool)]
    if not pooled.empty:
        keep = pooled["include"].fillna(True).astype(bool) | pooled["is_outside"].astype(bool)
        pooled = pooled[pooled["used"].astype(bool) & keep]
        if filters.get("tasks"):
            pooled = pooled[pooled["task"].isin(filters["tasks"])]
        if filters.get("profiles"):
            pooled = pooled[pooled["profile"].isin(filters["profiles"]) | (pooled["group"] == "TODOS")]
    return {"recordings": included, "gaze": gaze, "pooled": pooled}


def _cells(recordings: pd.DataFrame) -> List[Dict]:
    """Células tarefa × loja e, por tarefa com mais de uma loja, a célula de todas."""

    cells = []
    if recordings.empty:
        return cells
    for (task, store), rows in recordings.groupby(["task", "store"]):
        cells.append({"task": task, "store": store, "store_label": rows["store_label"].iloc[0],
                      "keys": set(rows["recording_key"])})
    for task, rows in recordings.groupby("task"):
        if rows["store"].nunique() > 1:
            cells.append({"task": task, "store": ALL_STORES, "store_label": ALL_STORES,
                          "keys": set(rows["recording_key"])})
    for cell in cells:
        cell["cell"] = cell_label(cell["task"], cell["store_label"])
        cell["n"] = len(cell["keys"])
    return cells


# ---------------------------------------------------------------------------
# Por gravacao
# ---------------------------------------------------------------------------

def per_recording_brand(gaze: pd.DataFrame, recordings: pd.DataFrame, kinds=("produto",)) -> pd.DataFrame:
    """Uma linha por gravação × marca, com zeros para marca não olhada."""

    columns = ["recording_key", "brand", "share", "dwell_s", "looked", "ttff_s", "ttff_raw",
               "max_visits", "visits", "rel_ttff_s", "first_credit"]
    if gaze.empty or recordings.empty:
        return pd.DataFrame(columns=columns)
    rows = gaze[gaze["kind"].isin(kinds) & (gaze["brand"] != "")]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    grouped = rows.groupby(["recording_key", "brand"])
    frame = pd.DataFrame({
        "share_raw": grouped["share_of_recording"].sum(min_count=1),
        "dwell_s": grouped["dwell_s"].sum(min_count=1),
        "looked": grouped["looked"].any(),
        "max_visits": grouped["visits"].max(),
        "visits": grouped["visits"].sum(),
    }).reset_index()
    looked_rows = rows[rows["looked"]]
    ttff = looked_rows.groupby(["recording_key", "brand"]).agg(
        ttff_s=("ttff_s", "min"), ttff_raw=("ttff_raw", "min")
    ).reset_index()
    frame = frame.merge(ttff, on=["recording_key", "brand"], how="left")

    # Zeros explicitos: toda marca da celula em toda gravacao da celula.
    brands_by_store = rows.groupby("store")["brand"].unique().to_dict()
    store_of = dict(zip(recordings["recording_key"], recordings["store"]))
    complete = []
    for key in recordings["recording_key"]:
        for brand in brands_by_store.get(store_of.get(key), []):
            complete.append((key, brand))
    grid = pd.DataFrame(complete, columns=["recording_key", "brand"])
    frame = grid.merge(frame, on=["recording_key", "brand"], how="left")
    frame["looked"] = frame["looked"].fillna(False).astype(bool)
    frame["share_raw"] = frame["share_raw"].fillna(0.0)
    frame["max_visits"] = frame["max_visits"].fillna(0.0)
    frame["visits"] = frame["visits"].fillna(0.0)
    # Tempo zero onde a gravacao tem segundos e a marca nao foi olhada.
    has_seconds = rows.groupby("recording_key")["dwell_s"].apply(lambda s: s.notna().any())
    missing = frame["dwell_s"].isna() & frame["recording_key"].map(has_seconds).fillna(False).astype(bool)
    frame.loc[missing, "dwell_s"] = 0.0

    totals = frame.groupby("recording_key")["share_raw"].transform("sum")
    frame["share"] = np.where(totals > 0, frame["share_raw"] / totals.where(totals > 0, 1), np.nan)
    first = frame[frame["looked"] & frame["ttff_s"].notna()].groupby("recording_key")["ttff_s"].transform("min")
    frame["rel_ttff_s"] = np.nan
    frame.loc[first.index, "rel_ttff_s"] = frame.loc[first.index, "ttff_s"] - first

    # Primeira marca notada pelo TTFF bruto (mesma unidade dentro da gravacao).
    frame["first_credit"] = 0.0
    looked_frame = frame[frame["looked"] & frame["ttff_raw"].notna()]
    for key, group in looked_frame.groupby("recording_key"):
        minimum = group["ttff_raw"].min()
        winners = group.index[np.isclose(group["ttff_raw"], minimum, atol=1e-9)]
        frame.loc[winners, "first_credit"] = 1.0 / len(winners)
    return frame[columns]


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------

def brand_table(
    gaze: pd.DataFrame,
    recordings: pd.DataFrame,
    catalog: pd.DataFrame,
    *,
    kinds=("produto",),
    examined_threshold_s: float = 1.0,
    focus_brand: str = "",
) -> pd.DataFrame:
    per = per_recording_brand(gaze, recordings, kinds)
    columns = ["task", "store", "store_label", "cell", "brand", "is_focus", "n", "n_pos",
               "share_mean", "share_weighted", "reach", "examined", "examined_n",
               "examined_given_noticed", "revisit", "ttff_median", "ttff_q1", "ttff_q3",
               "ttff_n", "rel_ttff_median", "first_noticed", "first_noticed_n", "dwell_mean_s",
               "visits_mean", "presence", "presence_index", "presence_source"]
    if per.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for cell in _cells(recordings):
        sub = per[per["recording_key"].isin(cell["keys"])]
        if sub.empty:
            continue
        positive_keys = set(sub.loc[sub["share"].notna(), "recording_key"])
        any_look = set(sub.loc[sub["looked"] & sub["ttff_raw"].notna(), "recording_key"])
        seconds_keys = set(sub.loc[sub["dwell_s"].notna(), "recording_key"])
        weights = _presence(catalog, cell["store"], recordings, cell["keys"], kinds)
        total_weight = sum(weights["weights"].values())
        for brand, rows_b in sub.groupby("brand"):
            looked = rows_b[rows_b["looked"]]
            with_seconds = rows_b[rows_b["recording_key"].isin(seconds_keys)]
            examined_hits = with_seconds[with_seconds["dwell_s"] >= examined_threshold_s]
            dwell_total = sub.loc[sub["recording_key"].isin(seconds_keys), "dwell_s"].sum()
            ttff_values = looked["ttff_s"].dropna()
            share_mean = rows_b.loc[rows_b["recording_key"].isin(positive_keys), "share"].mean()
            presence = weights["weights"].get(brand, 0.0) / total_weight if total_weight else math.nan
            rows.append({
                "task": cell["task"], "store": cell["store"], "store_label": cell["store_label"],
                "cell": cell["cell"], "brand": brand,
                "is_focus": bool(focus_brand) and fold(brand) == fold(focus_brand),
                "n": cell["n"], "n_pos": len(positive_keys),
                "share_mean": share_mean,
                "share_weighted": (with_seconds["dwell_s"].sum() / dwell_total
                                   if dwell_total and len(seconds_keys) == cell["n"] else math.nan),
                "reach": looked["recording_key"].nunique() / cell["n"] if cell["n"] else math.nan,
                "examined": (examined_hits["recording_key"].nunique() / len(seconds_keys)
                             if seconds_keys else math.nan),
                "examined_n": len(seconds_keys),
                "examined_given_noticed": (examined_hits["recording_key"].nunique()
                                           / looked["recording_key"].nunique()
                                           if len(looked) and seconds_keys else math.nan),
                "revisit": (rows_b["max_visits"] >= 2).sum() / cell["n"] if cell["n"] else math.nan,
                "ttff_median": ttff_values.median() if len(ttff_values) else math.nan,
                "ttff_q1": ttff_values.quantile(0.25) if len(ttff_values) else math.nan,
                "ttff_q3": ttff_values.quantile(0.75) if len(ttff_values) else math.nan,
                "ttff_n": int(len(ttff_values)),
                "rel_ttff_median": looked["rel_ttff_s"].median() if len(looked) else math.nan,
                "first_noticed": rows_b["first_credit"].sum() / len(any_look) if any_look else math.nan,
                "first_noticed_n": len(any_look),
                "dwell_mean_s": rows_b["dwell_s"].mean() if rows_b["dwell_s"].notna().any() else math.nan,
                "visits_mean": rows_b["visits"].mean(),
                "presence": presence,
                "presence_index": share_mean / presence if presence and presence == presence else math.nan,
                "presence_source": weights["source"],
            })
    return pd.DataFrame(rows, columns=columns)


def _presence(catalog: pd.DataFrame, store: str, recordings: pd.DataFrame, keys, kinds) -> Dict:
    """Peso de cada marca na gôndola da célula (AOIs do catálogo)."""

    if catalog.empty:
        return {"weights": {}, "source": ""}
    stores = (set(recordings.loc[recordings["recording_key"].isin(keys), "store"])
              if store == ALL_STORES else {store})
    rows = catalog[catalog["store"].isin(stores) & catalog["kind"].isin(kinds)
                   & catalog["include"].astype(bool) & (catalog["brand"] != "")]
    if rows.empty:
        return {"weights": {}, "source": ""}
    manual = rows["shelf_weight"].notna().any()
    weight = rows["shelf_weight"].fillna(1.0) if manual else pd.Series(1.0, index=rows.index)
    weights = weight.groupby(rows["brand"]).sum().to_dict()
    return {"weights": weights, "source": "peso informado" if manual else "nº de AOIs (aproximação)"}


def sku_table(gaze: pd.DataFrame, recordings: pd.DataFrame) -> pd.DataFrame:
    columns = ["task", "store", "store_label", "cell", "product", "brand", "is_focus", "n",
               "share_mean", "reach", "dwell_mean_s", "ttff_median", "visits_mean"]
    products = gaze[(gaze["kind"] == "produto") & (gaze["product"] != "")] if not gaze.empty else gaze
    if products.empty:
        return pd.DataFrame(columns=columns)
    per = products.groupby(["recording_key", "store", "product", "brand"]).agg(
        share_raw=("share_of_recording", "sum"),
        dwell_s=("dwell_s", lambda s: s.sum(min_count=1)),
        looked=("looked", "any"),
        ttff_s=("ttff_s", "min"),
        visits=("visits", "sum"),
        is_focus=("is_focus", "any"),
    ).reset_index()
    per["share"] = per["share_raw"] / per.groupby("recording_key")["share_raw"].transform("sum").replace(0, np.nan)
    rows = []
    for cell in _cells(recordings):
        if cell["store"] == ALL_STORES:
            continue
        sub = per[per["recording_key"].isin(cell["keys"])]
        for (product, brand), rows_p in sub.groupby(["product", "brand"]):
            looked = rows_p[rows_p["looked"]]
            rows.append({
                "task": cell["task"], "store": cell["store"], "store_label": cell["store_label"],
                "cell": cell["cell"], "product": product, "brand": brand,
                "is_focus": bool(rows_p["is_focus"].any()), "n": cell["n"],
                "share_mean": rows_p["share"].mean(),
                "reach": looked["recording_key"].nunique() / cell["n"],
                "dwell_mean_s": rows_p["dwell_s"].fillna(0).mean() if rows_p["dwell_s"].notna().any() else math.nan,
                "ttff_median": looked["ttff_s"].median() if len(looked) else math.nan,
                "visits_mean": rows_p["visits"].sum() / cell["n"],
            })
    return pd.DataFrame(rows, columns=columns)


def price_table(gaze: pd.DataFrame, recordings: pd.DataFrame) -> pd.DataFrame:
    """Atenção às etiquetas de preço, onde estão mapeadas."""

    columns = ["task", "store", "store_label", "cell", "product", "brand", "n", "reach",
               "dwell_mean_s", "ttff_median", "price_fraction"]
    if gaze.empty or not (gaze["kind"] == "preco").any():
        return pd.DataFrame(columns=columns)
    rows = []
    for cell in _cells(recordings):
        if cell["store"] == ALL_STORES:
            continue
        sub = gaze[gaze["recording_key"].isin(cell["keys"])]
        prices = sub[sub["kind"] == "preco"]
        if prices.empty:
            continue
        for (product, brand), rows_p in prices.groupby(["product", "brand"]):
            per = rows_p.groupby("recording_key").agg(
                share=("share_of_recording", "sum"), dwell_s=("dwell_s", lambda s: s.sum(min_count=1)),
                looked=("looked", "any"), ttff_s=("ttff_s", "min"),
            )
            related = sub[(sub["kind"] == "produto") & (sub["product"] == product)].groupby(
                "recording_key")["share_of_recording"].sum()
            denominator = per["share"].add(related.reindex(per.index).fillna(0))
            fraction = (per["share"] / denominator.where(denominator > 0)).dropna()
            looked = per[per["looked"]]
            rows.append({
                "task": cell["task"], "store": cell["store"], "store_label": cell["store_label"],
                "cell": cell["cell"], "product": product, "brand": brand, "n": cell["n"],
                "reach": len(looked) / cell["n"],
                "dwell_mean_s": per["dwell_s"].fillna(0).sum() / cell["n"] if per["dwell_s"].notna().any() else math.nan,
                "ttff_median": looked["ttff_s"].median() if len(looked) else math.nan,
                "price_fraction": fraction.mean() if len(fraction) else math.nan,
            })
    return pd.DataFrame(rows, columns=columns)


def _attribute_presence(catalog: Optional[pd.DataFrame], column: str, stores) -> Dict[str, float]:
    """Fração das AOIs de produto (ou do peso informado) de cada valor do atributo."""

    if catalog is None or catalog.empty or column not in catalog:
        return {}
    rows = catalog[catalog["store"].isin(stores) & (catalog["kind"] == "produto")
                   & catalog["include"].astype(bool) & (catalog[column].fillna("") != "")]
    if rows.empty:
        return {}
    manual = rows["shelf_weight"].notna().any()
    weight = rows["shelf_weight"].fillna(1.0) if manual else pd.Series(1.0, index=rows.index)
    totals = weight.groupby(rows[column]).sum()
    return (totals / totals.sum()).to_dict() if totals.sum() else {}


def attribute_table(gaze: pd.DataFrame, recordings: pd.DataFrame, dimensions: Sequence[str],
                    catalog: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Fração da atenção por valor de atributo, entre as AOIs em que ele existe.

    A presença de cada valor na gôndola vem do catálogo, como a das marcas: um
    valor com mais produtos expostos tende a levar mais atenção, e o índice
    (share ÷ presença) separa atração de espaço ocupado.
    """

    columns = ["task", "store", "store_label", "cell", "dimension", "value", "n", "n_defined",
               "share_mean", "reach", "presence", "presence_index"]
    rows = []
    products = gaze[gaze["kind"] == "produto"] if not gaze.empty else gaze
    for dimension in dimensions:
        column = "attr_{}".format(dimension)
        if products.empty or column not in products:
            continue
        defined = products[products[column].fillna("") != ""]
        if defined.empty:
            continue
        for cell in _cells(recordings):
            sub = defined[defined["recording_key"].isin(cell["keys"])]
            if sub.empty or sub[column].nunique() < 2:
                continue
            per = sub.groupby(["recording_key", column]).agg(
                share=("share_of_recording", "sum"), looked=("looked", "any")).reset_index()
            totals = per.groupby("recording_key")["share"].transform("sum")
            per["fraction"] = per["share"] / totals.where(totals > 0)
            counted = per[totals > 0]
            n_defined = counted["recording_key"].nunique()
            stores = (set(recordings.loc[recordings["recording_key"].isin(cell["keys"]), "store"])
                      if cell["store"] == ALL_STORES else {cell["store"]})
            presence = _attribute_presence(catalog, column, stores)
            for value, rows_v in per.groupby(column):
                share = rows_v.loc[rows_v["recording_key"].isin(counted["recording_key"]), "fraction"].mean()
                weight = presence.get(value, math.nan)
                rows.append({
                    "task": cell["task"], "store": cell["store"], "store_label": cell["store_label"],
                    "cell": cell["cell"], "dimension": dimension, "value": value, "n": cell["n"],
                    "n_defined": n_defined,
                    "share_mean": share,
                    "reach": rows_v.loc[rows_v["looked"], "recording_key"].nunique() / cell["n"],
                    "presence": weight,
                    "presence_index": share / weight if weight and weight == weight else math.nan,
                })
    return pd.DataFrame(rows, columns=columns)


def _decision_time_lookup(participants: pd.DataFrame, times: Optional[pd.DataFrame]) -> Dict:
    """Tempo até a decisão por participante × tarefa: o de campo primeiro, depois o da planilha.

    Sem a tabela de tempos (modelo antigo), cai no tempo do participante, sem tarefa.
    """
    if times is None:
        tempo = dict(zip(participants["participant"], participants["tempo_decisao_s"])) if not participants.empty else {}
        return {"by_participant": tempo}
    lookup: Dict = {}
    for source in ("planilha", "campo"):  # campo por último: sobrescreve
        rows = times[times["source"] == source] if not times.empty else times
        for row in rows.to_dict("records"):
            if row["task"]:
                lookup[(row["participant"], row["task"])] = row["seconds"]
    return {"by_task": lookup}


def recording_summary(gaze: pd.DataFrame, recordings: pd.DataFrame, participants: pd.DataFrame,
                      focus_brand: str = "", times: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Por gravação: tempo na categoria, marcas vistas, share da marca foco, tempo até a decisão.

    O tempo até a decisão é o da mesma tarefa da gravação (ver `jornada_model._build_times`):
    um tempo de compra da estimulada nunca aparece numa gravação da jornada livre.
    """

    columns = ["recording_key", "participant", "task", "store", "store_label", "channel", "profile",
               "category_share", "category_dwell_s", "brands_looked", "visits_total",
               "focus_share", "focus_ttff_s", "tempo_decisao_s"]
    if recordings.empty:
        return pd.DataFrame(columns=columns)
    shelf = gaze[gaze["kind"].isin(("produto", "preco"))] if not gaze.empty else gaze
    per = per_recording_brand(gaze, recordings) if not gaze.empty else pd.DataFrame()
    lookup = _decision_time_lookup(participants, times)
    rows = []
    for record in recordings.to_dict("records"):
        key = record["recording_key"]
        own = shelf[shelf["recording_key"] == key] if not shelf.empty else shelf
        brands = per[(per["recording_key"] == key)] if not per.empty else per
        focus = brands[brands["brand"].map(fold) == fold(focus_brand)] if focus_brand and not brands.empty else pd.DataFrame()
        rows.append({
            "recording_key": key, "participant": record["participant"], "task": record["task"],
            "store": record["store"], "store_label": record["store_label"],
            "channel": record.get("channel", ""), "profile": record.get("profile", ""),
            "category_share": own["share_of_recording"].sum() if not own.empty else math.nan,
            "category_dwell_s": own["dwell_s"].sum(min_count=1) if not own.empty else math.nan,
            "brands_looked": int(brands["looked"].sum()) if not brands.empty else 0,
            "visits_total": own["visits"].sum() if not own.empty else 0,
            "focus_share": focus["share"].iloc[0] if not focus.empty else math.nan,
            "focus_ttff_s": focus["ttff_s"].iloc[0] if not focus.empty else math.nan,
            "tempo_decisao_s": (lookup["by_task"].get((record["participant"], record["task"]), math.nan)
                                if "by_task" in lookup
                                else lookup["by_participant"].get(record["participant"], math.nan)),
        })
    return pd.DataFrame(rows, columns=columns)


TIME_SOURCE_LABELS = {"campo": "tempo de compra (campo)", "planilha": "Tempo da planilha"}


def decision_table(times: pd.DataFrame) -> pd.DataFrame:
    """Tempo até a decisão por tarefa e fonte, e dentro delas por loja, canal e perfil (descritivo).

    Usa a tabela de tempos do modelo, que não depende de gravação codificada:
    a estimulada das lojas sem AOI entra pelo tempo de compra do registro de campo.
    """

    columns = ["task", "task_label", "source", "source_label", "group_type", "group", "n", "median_s",
               "q1_s", "q3_s", "min_s", "max_s"]
    data = times.dropna(subset=["seconds"]) if times is not None and not times.empty else pd.DataFrame()
    if data.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for (task, source), task_rows in data.groupby(["task", "source"], sort=False):
        task_rows = task_rows.drop_duplicates("participant")
        for group_type, column in (("loja", "store_label"), ("canal", "channel"), ("perfil", "profile")):
            for group, values in task_rows.groupby(column):
                if not group:
                    continue
                seconds = values["seconds"]
                rows.append({"task": task, "task_label": TASK_LABELS.get(task, task or "sem tarefa"),
                             "source": source, "source_label": TIME_SOURCE_LABELS.get(source, source),
                             "group_type": group_type, "group": group, "n": int(len(seconds)),
                             "median_s": seconds.median(), "q1_s": seconds.quantile(0.25),
                             "q3_s": seconds.quantile(0.75), "min_s": seconds.min(), "max_s": seconds.max()})
    frame = pd.DataFrame(rows, columns=columns)
    order = {"estimulada": 0, "livre": 1, "embalagens": 2}
    frame["_task"] = frame["task"].map(lambda t: order.get(t, 9))
    frame["_source"] = frame["source"].map(lambda s: 0 if s == "campo" else 1)
    return frame.sort_values(["_task", "_source"], kind="stable").drop(columns=["_task", "_source"]).reset_index(drop=True)


def decision_by_store(decision: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por tarefa × loja para gráficos: o tempo de compra de campo onde existe, senão o da planilha."""
    if decision is None or decision.empty:
        return pd.DataFrame(columns=list(getattr(decision, "columns", [])) + ["label"])
    stores = decision[decision["group_type"] == "loja"]
    with_field = set(stores.loc[stores["source"] == "campo", "task"])
    keep = stores[(stores["source"] == "campo") | ~stores["task"].isin(with_field)]
    return keep.assign(label=keep["task_label"] + " · " + keep["group"])


def time_kpi(metrics: Dict):
    """Número-chave de tempo: mediana do tempo de compra de campo; sem ele, a do Tempo da planilha."""
    times = metrics.get("times")
    if times is not None and not times.empty:
        field = times.loc[times["source"] == "campo", "seconds"].dropna()
        if len(field):
            return "Tempo de compra (mediana)", float(field.median())
        sheet = times["seconds"].dropna()
        if len(sheet):
            return "Tempo até a decisão (mediana)", float(sheet.median())
    summary = metrics.get("recording_summary")
    values = (summary["tempo_decisao_s"].dropna()
              if summary is not None and "tempo_decisao_s" in summary else pd.Series(dtype=float))
    return "Tempo até a decisão (mediana)", float(values.median()) if len(values) else math.nan


# ---------------------------------------------------------------------------
# Escolha (registro de campo)
# ---------------------------------------------------------------------------

def _with_choice(choices: pd.DataFrame) -> pd.DataFrame:
    if choices is None or choices.empty:
        return pd.DataFrame()
    return choices[choices["chosen_brands"].map(lambda brands: len(brands) > 0)]


def choice_table(choices: pd.DataFrame, focus_brand: str = "") -> pd.DataFrame:
    """Participantes que escolheram cada marca, por tarefa e por loja, canal e perfil.

    Quem escolheu duas marcas conta para as duas; a fração é sobre quem tem
    escolha registrada no grupo. Marcas escolhidas em qualquer grupo da tarefa
    aparecem em todos (com zero), para a comparação entre grupos ser direta.
    """
    columns = ["task", "task_label", "group_type", "group", "brand", "is_focus", "n", "chose_n", "share"]
    data = _with_choice(choices)
    if data.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for task, task_rows in data.groupby("task", sort=False):
        totals = Counter(brand for brands in task_rows["chosen_brands"] for brand in set(brands))
        brands = [brand for brand, _ in sorted(totals.items(), key=lambda item: (-item[1], item[0]))]
        groups = [("total", "todas as lojas", task_rows)]
        for group_type, column in (("loja", "store_label"), ("canal", "channel"), ("perfil", "profile")):
            groups += [(group_type, group, subset) for group, subset in task_rows.groupby(column) if group]
        for group_type, group, subset in groups:
            n = int(len(subset))
            counts = Counter(brand for chosen in subset["chosen_brands"] for brand in set(chosen))
            for brand in brands:
                rows.append({"task": task, "task_label": TASK_LABELS.get(task, task), "group_type": group_type,
                             "group": group, "brand": brand,
                             "is_focus": bool(focus_brand) and fold(brand) == fold(focus_brand),
                             "n": n, "chose_n": int(counts.get(brand, 0)),
                             "share": counts.get(brand, 0) / n if n else math.nan})
    return pd.DataFrame(rows, columns=columns)


def variant_table(choices: pd.DataFrame, dimensions: Optional[Dict[str, Sequence[str]]] = None) -> pd.DataFrame:
    """Entre quem escolheu cada marca, quantos levaram cada valor de atributo (ex.: Noturno)."""
    columns = ["task", "task_label", "brand", "dimension", "value", "n_brand", "chose_n", "share"]
    data = _with_choice(choices)
    if data.empty or not dimensions:
        return pd.DataFrame(columns=columns)
    rows = []
    for task, task_rows in data.groupby("task", sort=False):
        brands = sorted({brand for chosen in task_rows["chosen_brands"] for brand in chosen})
        for brand in brands:
            choosers = task_rows[task_rows["chosen_brands"].map(lambda chosen: brand in chosen)]
            for dimension, values in dimensions.items():
                for value in values:
                    hits = int(choosers["chosen_values"].map(lambda found: value in (found or {}).get(dimension, [])).sum())
                    if not hits:
                        continue
                    rows.append({"task": task, "task_label": TASK_LABELS.get(task, task), "brand": brand,
                                 "dimension": dimension, "value": value, "n_brand": int(len(choosers)),
                                 "chose_n": hits, "share": hits / len(choosers)})
    return pd.DataFrame(rows, columns=columns)


def attention_to_choice(choices: pd.DataFrame, per_recording: pd.DataFrame,
                        examined_threshold_s: float = 1.0) -> pd.DataFrame:
    """Para quem tem olhar na tarefa da escolha: a marca escolhida foi notada, examinada, a 1ª, a mais vista?"""
    columns = ["participant", "task", "task_label", "store_label", "chosen_brand", "looked", "examined",
               "first_noticed", "top_share", "share", "share_rank", "n_brands"]
    data = _with_choice(choices)
    if data.empty or per_recording is None or per_recording.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for record in data[data["has_gaze"] & (data["chosen_brands"].map(len) == 1)].to_dict("records"):
        own = per_recording[per_recording["recording_key"] == record["recording_key"]]
        if own.empty:
            continue
        brand = record["chosen_brands"][0]
        mine = own[own["brand"].map(fold) == fold(brand)]
        ranked = own.sort_values("share", ascending=False).reset_index(drop=True)
        position = ranked.index[ranked["brand"].map(fold) == fold(brand)]
        looked = bool(mine["looked"].any()) if not mine.empty else False
        dwell = mine["dwell_s"].iloc[0] if not mine.empty else math.nan
        share = mine["share"].iloc[0] if not mine.empty else math.nan
        rows.append({
            "participant": record["participant"], "task": record["task"], "task_label": record["task_label"],
            "store_label": record["store_label"], "chosen_brand": brand, "looked": looked,
            "examined": bool(dwell == dwell and dwell >= examined_threshold_s),
            "first_noticed": bool(not mine.empty and mine["first_credit"].iloc[0] > 0),
            "top_share": bool(len(position) and position[0] == 0 and share == share and share > 0),
            "share": share,
            "share_rank": int(position[0]) + 1 if len(position) else math.nan,
            "n_brands": int(len(own)),
        })
    return pd.DataFrame(rows, columns=columns)


def consideration_table(choices: pd.DataFrame) -> pd.DataFrame:
    """Tamanho do conjunto considerado e embalagens citadas, por tarefa e loja."""
    columns = ["task", "task_label", "store_label", "n", "considered_mean", "single_brand_n", "packs"]
    data = choices[choices["considered_text"] != ""] if choices is not None and not choices.empty else pd.DataFrame()
    if data.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    groups = [(task, "todas as lojas", rows_t) for task, rows_t in data.groupby("task", sort=False)]
    groups += [(task, store, rows_s) for (task, store), rows_s in data.groupby(["task", "store_label"], sort=False)]
    for task, store, subset in groups:
        packs = Counter(size for sizes in subset["packs"] for size in sizes)
        rows.append({
            "task": task, "task_label": TASK_LABELS.get(task, task), "store_label": store, "n": int(len(subset)),
            "considered_mean": subset["considered_count"].mean(),
            "single_brand_n": int((subset["considered_count"] == 1).sum()),
            "packs": " · ".join("{} un.: {}".format(size, count) for size, count in sorted(packs.items())),
        })
    return pd.DataFrame(rows, columns=columns)


def packaging_tables(pooled: pd.DataFrame, element_labels: Optional[Dict] = None) -> Dict[str, pd.DataFrame]:
    """Elementos de embalagem por perfil, a partir dos agregados."""

    empty = {"elements": pd.DataFrame(), "brands": pd.DataFrame(), "coverage": pd.DataFrame()}
    if pooled.empty:
        return empty
    data = pooled[pooled["task"] == "embalagens"] if (pooled["task"] == "embalagens").any() else pooled
    if data.empty:
        return empty
    groups = [g for g in data["group"].unique() if g != "TODOS"]
    # Todos = soma dos perfis; o TODOS exportado entra so se nao houver perfis.
    sources = data[data["group"].isin(groups)] if groups else data
    combined = _combine_groups(sources)
    frames = [sources, combined] if groups else [sources]
    work = pd.concat(frames, ignore_index=True)

    elements_rows, brand_rows, coverage_rows = [], [], []
    for (group, profile), rows_g in work.groupby(["group", "profile"], sort=False):
        n_group = rows_g["n_group"].max()
        outside = rows_g[rows_g["is_outside"]]
        coverage_rows.append({
            "group": group, "profile": profile, "n_group": n_group,
            "aoi_coverage": 1 - outside["share_of_pool_time"].sum() if not outside.empty else math.nan,
        })
        elements = rows_g[(rows_g["kind"] == "embalagem") & (rows_g["brand"] != "")]
        if elements.empty:
            continue
        brand_totals = elements.groupby("brand")["dwell_sum_s"].sum()
        grand = brand_totals.sum()
        for brand, total in brand_totals.items():
            marca = elements[(elements["brand"] == brand) & (elements["element"] == "MARCA")]
            brand_rows.append({
                "group": group, "profile": profile, "brand": brand, "n_group": n_group,
                "dwell_sum_s": total,
                "packaging_share": total / grand if grand else math.nan,
                "dwell_per_participant_s": total / n_group if n_group else math.nan,
                "logo_reach": min(1.0, marca["lookers"].sum() / n_group) if n_group and not marca.empty else math.nan,
            })
        for _, row in elements.iterrows():
            brand_total = brand_totals.get(row["brand"], 0)
            lookers = row["lookers"]
            elements_rows.append({
                "group": group, "profile": profile, "brand": row["brand"],
                "element": row["element"],
                "element_label": element_label(row["element"], element_labels),
                "n_group": n_group, "lookers": lookers,
                "reach": min(1.0, lookers / n_group) if n_group else math.nan,
                "dwell_sum_s": row["dwell_sum_s"],
                "dwell_per_participant_s": row["dwell_sum_s"] / n_group if n_group else math.nan,
                "dwell_per_looker_s": row["dwell_sum_s"] / lookers if lookers else math.nan,
                "visits_per_looker": row["visits_sum"] / lookers if lookers else math.nan,
                "ttff_mean_s": row["ttff_mean_lookers_s"],
                "element_share": row["dwell_sum_s"] / brand_total if brand_total else math.nan,
            })
    return {
        "elements": pd.DataFrame(elements_rows),
        "brands": pd.DataFrame(brand_rows),
        "coverage": pd.DataFrame(coverage_rows),
    }


def _combine_groups(rows: pd.DataFrame) -> pd.DataFrame:
    """Soma dos grupos: tempos e contagens somam; TTFF é média ponderada por quem olhou."""

    if rows.empty:
        return rows
    sizes = rows.groupby("group")["n_group"].max()
    combined = []
    for _, part in rows.groupby("aoi_key", sort=False):
        first = part.iloc[0].to_dict()
        lookers = part["lookers"].fillna(0)
        weighted = (part["ttff_mean_lookers_s"] * lookers).sum()
        first.update(
            group="Todos",
            profile="Todos os perfis",
            n_group=int(sizes.sum()),
            n_group_source="soma dos grupos",
            dwell_sum_s=part["dwell_sum_s"].sum(min_count=1),
            visits_sum=part["visits_sum"].sum(),
            lookers=lookers.sum(),
            ttff_mean_lookers_s=weighted / lookers.sum() if lookers.sum() else math.nan,
            max_visit_s=part["max_visit_s"].max(),
        )
        combined.append(first)
    frame = pd.DataFrame(combined)
    # A fracao "fora das AOIs" do conjunto: tempo fora / tempo total dos grupos.
    if not frame.empty and frame["is_outside"].any():
        total_time = (rows["dwell_sum_s"] / rows["share_of_pool_time"].replace(0, np.nan)).groupby(rows["group"]).max().sum()
        outside_index = frame.index[frame["is_outside"]]
        frame.loc[outside_index, "share_of_pool_time"] = frame.loc[outside_index, "dwell_sum_s"] / total_time if total_time else math.nan
    return frame


# ---------------------------------------------------------------------------
# Achados e limitacoes
# ---------------------------------------------------------------------------

def _pct(value: float) -> str:
    return "{:.0f}%".format(100 * value) if value == value else "—"


def _num(value: float, digits: int = 1) -> str:
    """Número em pt-BR, sem casas quando é inteiro."""
    if value != value:
        return "—"
    if abs(value - round(value)) < 1e-9:
        return "{:.0f}".format(value)
    return "{:.{d}f}".format(value, d=digits).replace(".", ",")


def generate_findings(metrics: Dict, focus_brand: str = "") -> List[Dict]:
    """Frases determinísticas, cada uma com valor, n e célula."""

    findings: List[Dict] = []

    def add(section: str, text: str, cell: str, n: int, value: float = math.nan) -> None:
        findings.append({"section": section, "text": text, "cell": cell, "n": int(n),
                         "value": value, "strength": "descritivo" if n < MIN_N_TEST else "amostra ≥ 5"})

    brands = metrics.get("brand", pd.DataFrame())
    if not brands.empty:
        for cell, rows in brands.groupby("cell", sort=False):
            ranked = rows.dropna(subset=["share_mean"]).sort_values("share_mean", ascending=False)
            n = int(rows["n"].iloc[0])
            if len(ranked) >= 2:
                top, second = ranked.iloc[0], ranked.iloc[1]
                margin = top["share_mean"] - second["share_mean"]
                if margin >= 0.10:
                    text = "{} lidera a atenção entre as marcas em {}: {} da atenção, contra {} de {} (n={}).".format(
                        top["brand"], cell, _pct(top["share_mean"]), _pct(second["share_mean"]), second["brand"], n)
                elif margin >= 0.05:
                    text = "{} fica à frente em {} ({} contra {} de {}, n={}).".format(
                        top["brand"], cell, _pct(top["share_mean"]), _pct(second["share_mean"]), second["brand"], n)
                else:
                    text = "Empate técnico em {}: {} ({}) e {} ({}), n={}.".format(
                        cell, top["brand"], _pct(top["share_mean"]), second["brand"], _pct(second["share_mean"]), n)
                add("gondola", text, cell, n, top["share_mean"])
            focus = rows[rows["is_focus"]]
            if not focus.empty:
                row = focus.iloc[0]
                missed = int(round(n * (1 - row["reach"]))) if row["reach"] == row["reach"] else 0
                if missed > 0:
                    add("gondola", "{} de {} participantes não olharam {} em {}.".format(
                        missed, n, row["brand"], cell), cell, n, row["reach"])
                if row["examined"] == row["examined"]:
                    add("gondola", "{}: notada por {}, examinada por {} e revisitada por {} em {}.".format(
                        row["brand"], _pct(row["reach"]), _pct(row["examined"]), _pct(row["revisit"]), cell),
                        cell, n, row["examined"])
            first = rows.dropna(subset=["first_noticed"]).sort_values("first_noticed", ascending=False)
            if not first.empty:
                row = first.iloc[0]
                n_any = int(row["first_noticed_n"])
                count = row["first_noticed"] * n_any
                if n_any and row["first_noticed"] >= 2 / 3:
                    # Empate divide o credito: 1,5 de 2 e diferente de 2 de 2.
                    add("gondola", "{} foi a primeira marca notada por {} de {} em {}.".format(
                        row["brand"], _num(count), n_any, cell), cell, n_any, row["first_noticed"])
            if rows["store"].iloc[0] == ALL_STORES:
                continue
            for _, row in rows.iterrows():
                index = row["presence_index"]
                if index != index or row["presence"] != row["presence"]:
                    continue
                notable = index >= 1.2 or index <= 0.8
                if not notable or (not row["is_focus"] and index < 1.2):
                    continue
                direction = "acima" if index >= 1.2 else "abaixo"
                add("gondola", "{} recebe {}× a atenção esperada pela presença na gôndola em {} "
                    "({} do esperado; presença por {}).".format(
                        row["brand"], _num(index), cell, direction, row["presence_source"]),
                    cell, n, index)

    prices = metrics.get("price", pd.DataFrame())
    if not prices.empty:
        for _, row in prices.iterrows():
            add("preco", "A etiqueta de preço de {} foi vista por {} em {}.".format(
                row["product"], _pct(row["reach"]), row["cell"]), row["cell"], row["n"], row["reach"])

    attributes = metrics.get("attributes", pd.DataFrame())
    if not attributes.empty:
        for (cell, dimension), rows in attributes.groupby(["cell", "dimension"], sort=False):
            if rows["store"].iloc[0] == ALL_STORES:
                continue
            ranked = rows.dropna(subset=["share_mean"]).sort_values("share_mean", ascending=False)
            if not ranked.empty:
                top = ranked.iloc[0]
                presence = top.get("presence", math.nan)
                index = top.get("presence_index", math.nan)
                if presence == presence and presence and index == index:
                    if index >= 1.2:
                        reading = "acima do espaço que ocupa"
                    elif index <= 0.8:
                        reading = "abaixo do espaço que ocupa"
                    else:
                        reading = "proporcional ao espaço que ocupa"
                    add("navegacao", "{} concentra {} da atenção entre os produtos com o atributo {} em {} e "
                        "ocupa {} das AOIs desses produtos: atenção {} (índice {}; n={}).".format(
                            top["value"], _pct(top["share_mean"]), dimension, cell, _pct(presence), reading,
                            _num(index, 2), int(top["n_defined"])),
                        cell, int(top["n_defined"]), top["share_mean"])
                else:
                    add("navegacao", "{} concentra {} da atenção entre os produtos com o atributo {} em {} "
                        "(n={}).".format(top["value"], _pct(top["share_mean"]), dimension, cell,
                                         int(top["n_defined"])),
                        cell, int(top["n_defined"]), top["share_mean"])

    packaging = metrics.get("packaging", {}).get("elements", pd.DataFrame())
    if isinstance(packaging, pd.DataFrame) and not packaging.empty:
        overall = packaging[packaging["group"] == "Todos"]
        if overall.empty:
            overall = packaging
        for (group, brand), rows in overall.groupby(["profile", "brand"], sort=False):
            top = rows.sort_values("element_share", ascending=False).iloc[0]
            add("embalagem", "Na embalagem de {}, {} concentra {} do olhar ({}; n={}).".format(
                brand, top["element_label"], _pct(top["element_share"]), group, int(top["n_group"])),
                group, int(top["n_group"]), top["element_share"])
        if focus_brand:
            logos = packaging[(packaging["brand"].map(fold) == fold(focus_brand))
                              & (packaging["element"] == "MARCA")]
            for _, row in logos.iterrows():
                add("embalagem", "O logo de {} foi visto por {} ({} de {}) em {}.".format(
                    row["brand"], _pct(row["reach"]), int(row["lookers"]), int(row["n_group"]),
                    row["profile"]), row["profile"], int(row["n_group"]), row["reach"])

    decision = metrics.get("decision", pd.DataFrame())
    if not decision.empty:
        stores = decision[decision["group_type"] == "loja"]
        with_field = set(stores.loc[stores["source"] == "campo", "task"])
        for _, row in stores.iterrows():
            # O tempo da planilha só vira achado onde não há tempo de compra de campo.
            if row["source"] == "planilha" and row["task"] in with_field:
                continue
            what = "Tempo de compra" if row["source"] == "campo" else "Tempo (planilha)"
            add("decisao", "{} na {} · {}: mediana de {} s (de {} a {} s, n={}).".format(
                what, row["task_label"], row["group"], _num(row["median_s"]), _num(row["min_s"]),
                _num(row["max_s"]), int(row["n"])),
                "{} · {}".format(row["task_label"], row["group"]), int(row["n"]), row["median_s"])

    choice = metrics.get("choice", pd.DataFrame())
    if not choice.empty:
        for (task, store), rows in choice[choice["group_type"] == "loja"].groupby(["task", "group"], sort=False):
            ranked = rows[rows["chose_n"] > 0].sort_values(["chose_n", "brand"], ascending=[False, True])
            if ranked.empty:
                continue
            top, n = ranked.iloc[0], int(rows["n"].iloc[0])
            others = ", ".join("{} {}".format(r["brand"], r["chose_n"]) for _, r in ranked.iloc[1:].iterrows())
            add("escolha", "Na {} · {}, {} de {} {} {}{}.".format(
                TASK_LABELS.get(task, task), store, int(top["chose_n"]), n,
                "escolheu" if int(top["chose_n"]) == 1 else "escolheram", top["brand"],
                " ({})".format(others) if others else ""),
                "{} · {}".format(TASK_LABELS.get(task, task), store), n, top["share"])
        if focus_brand:
            channels = choice[(choice["group_type"] == "canal") & choice["is_focus"]]
            for task, rows in channels.groupby("task", sort=False):
                if len(rows) < 2:
                    continue
                parts = ["{} de {} no canal {}".format(int(r["chose_n"]), int(r["n"]), r["group"])
                         for _, r in rows.iterrows()]
                add("escolha", "Na {}, {} foi escolhida por {} — mesma tarefa, canais diferentes.".format(
                    TASK_LABELS.get(task, task), rows["brand"].iloc[0], " e ".join(parts)),
                    TASK_LABELS.get(task, task), int(rows["n"].sum()), math.nan)

    attention = metrics.get("attention_choice", pd.DataFrame())
    if not attention.empty:
        n = int(len(attention))
        add("escolha", "Entre quem tem olhar na tarefa da compra (n={}), a marca escolhida foi a mais vista "
            "por {}, a primeira notada por {} e não foi olhada por {}.".format(
                n, int(attention["top_share"].sum()), int(attention["first_noticed"].sum()),
                int((~attention["looked"]).sum())),
            "olhar × escolha", n, attention["top_share"].mean())
    return findings


def limitations(model: Dict, metrics: Dict) -> List[str]:
    notes: List[str] = []
    recordings = model["recordings"]
    if not recordings.empty:
        uncoded = recordings[recordings["status"] == "nao_codificada"]
        if len(uncoded):
            notes.append(
                "{} gravação(ões) sem codificação de AOI ({}) ficaram fora da análise; os n "
                "efetivos consideram só as gravações codificadas.".format(
                    len(uncoded), ", ".join(sorted(uncoded["participant"] + " · " + uncoded["task_label"])))
            )
        excluded = recordings[recordings["status"] == "excluida"]
        if len(excluded):
            notes.append("{} gravação(ões) excluída(s) manualmente da análise.".format(len(excluded)))
        low_hz = recordings[(recordings["hz"] < 20) & recordings["has_frames"]]
        if len(low_hz):
            notes.append(
                "Taxa de amostragem baixa em {}: fixações curtas podem ter se perdido.".format(
                    ", ".join(sorted(set(low_hz["participant"])))))
        converted = recordings[recordings["unit"] == "amostras"]
        if len(converted):
            notes.append(
                "Exports em amostras (quadros) foram convertidos para segundos pelos quadros de "
                "cada gravação; o TTFF usa o timestamp do quadro.")
    for confound in metrics.get("confounds", []):
        first, second = confound["labels"]
        notes.append(
            "{} e {} andam juntos na amostra ({}): diferenças entre um podem ser do outro; a "
            "comparação não isola o efeito.".format(first, second.lower(), confound["mapping"]))
    choices = metrics.get("choices", pd.DataFrame())
    if choices is not None and not choices.empty and (choices["chosen_text"] != "").any():
        notes.append(
            "Escolha, marcas consideradas e embalagens vêm do registro de campo em texto livre, "
            "normalizado automaticamente pelas marcas e atributos do projeto; confira na tabela de escolhas.")
        channels = choices.loc[choices["chosen_text"] != ""].groupby("task")["channel"].nunique()
        if (channels > 1).any() and metrics.get("confounds"):
            notes.append(
                "Para escolha e tempo de compra, a {} aconteceu em lojas dos dois canais: ali a comparação "
                "entre canais é feita na mesma tarefa, ao contrário do olhar.".format(
                    " e a ".join(TASK_LABELS.get(t, t).lower() for t in channels[channels > 1].index)))
    times = metrics.get("times", pd.DataFrame())
    if times is not None and not times.empty:
        pairs = times.pivot_table(index=["participant", "task"], columns="source", values="seconds", aggfunc="first")
        if {"campo", "planilha"} <= set(pairs.columns):
            both = pairs.dropna(subset=["campo", "planilha"])
            differ = both[(both["campo"] - both["planilha"]).abs() > 1]
            if len(differ):
                notes.append(
                    "O Tempo da planilha difere do tempo de compra do registro de campo em {} de {} "
                    "participante(s) (até {} s); as duas medidas aparecem separadas e rotuladas.".format(
                        len(differ), len(both), _num((differ["campo"] - differ["planilha"]).abs().max(), 0)))
    brands = metrics.get("brand", pd.DataFrame())
    if not brands.empty and (brands["n"] < MIN_N_TEST).any():
        small = sorted(brands.loc[brands["n"] < MIN_N_TEST, "cell"].unique())
        notes.append(
            "Células com menos de {} participantes ({}) são descritivas: não há teste estatístico "
            "com essa amostra.".format(MIN_N_TEST, ", ".join(small)))
    if not metrics.get("packaging", {}).get("elements", pd.DataFrame()).empty:
        notes.append(
            "Embalagens só existem agregadas por perfil: há totais do grupo, sem variação entre "
            "participantes nem teste.")
        coverage = metrics["packaging"].get("coverage", pd.DataFrame())
        if not coverage.empty and coverage["aoi_coverage"].notna().any():
            lowest = coverage["aoi_coverage"].min()
            if lowest < 0.5:
                notes.append(
                    "Nas Embalagens, os elementos mapeados somam só {} a {} do tempo gravado, "
                    "conforme o perfil: o resto ficou fora de qualquer elemento. As shares por "
                    "elemento comparam só o tempo dentro dos elementos; tempos absolutos dependem "
                    "de quanto de cada gravação era a tarefa.".format(
                        _pct(lowest), _pct(coverage["aoi_coverage"].max())))
    notes.append(
        "Eye tracking mede atenção visual: não prova preferência, intenção nem compra. "
        "FixationCount, sacadas e pupila não são usados — a ~23 Hz o rastreador não separa fixações.")
    return notes


# ---------------------------------------------------------------------------
# Tudo junto
# ---------------------------------------------------------------------------

def _filter_rows(frame: Optional[pd.DataFrame], filters: Dict) -> Optional[pd.DataFrame]:
    """Escolhas e tempos no mesmo recorte da página (tarefa, loja, perfil, canal)."""
    if frame is None:
        return None
    for key, column in (("tasks", "task"), ("stores", "store"), ("profiles", "profile"), ("channels", "channel")):
        chosen = filters.get(key)
        if chosen and column in frame:
            frame = frame[frame[column].isin(chosen)]
    return frame


def compute_all(model: Dict, filters: Optional[Dict] = None) -> Dict:
    """Todas as tabelas, achados e limitações para um recorte."""

    filters = filters or {}
    meta = model.get("meta") or {}
    focus = meta.get("focus_brand") or ""
    threshold = float(filters.get("examined_threshold_s") or meta.get("examined_threshold_s") or 1.0)
    kinds = tuple(filters.get("kinds") or ("produto",))
    view = apply_filters(model, filters)
    recordings, gaze, pooled = view["recordings"], view["gaze"], view["pooled"]
    catalog = model["catalog"]

    brand = brand_table(gaze, recordings, catalog, kinds=kinds, examined_threshold_s=threshold,
                        focus_brand=focus)
    per_brand = per_recording_brand(gaze, recordings, kinds)
    times = _filter_rows(model.get("times"), filters)
    choices = _filter_rows(model.get("choices"), filters)
    summary = recording_summary(gaze, recordings, model["participants"], focus, times)
    metrics = {
        "filters": filters,
        "sample": {
            "participants": int(recordings["participant"].nunique()) if not recordings.empty else 0,
            "recordings": int(len(recordings)),
            "by_cell": [
                {"cell": cell["cell"], "n": cell["n"], "task": cell["task"], "store": cell["store"]}
                for cell in _cells(recordings)
            ],
            "pooled_groups": sorted(pooled["group"].unique().tolist()) if not pooled.empty else [],
        },
        "brand": brand,
        "per_recording_brand": per_brand.merge(
            recordings[["recording_key", "participant", "task", "store", "store_label", "channel", "profile"]],
            on="recording_key", how="left") if not per_brand.empty else per_brand,
        "sku": sku_table(gaze, recordings),
        "price": price_table(gaze, recordings),
        "attributes": attribute_table(gaze, recordings, list((meta.get("dimensions") or {}).keys()), catalog),
        "recording_summary": summary,
        "times": times,
        "decision": decision_table(times) if times is not None else decision_table(pd.DataFrame()),
        "choices": choices,
        "choice": choice_table(choices, focus),
        "variants": variant_table(choices, meta.get("dimensions") or {}),
        "attention_choice": attention_to_choice(choices, per_brand, threshold),
        "consideration": consideration_table(choices) if choices is not None else consideration_table(pd.DataFrame()),
        "packaging": packaging_tables(pooled, meta.get("element_labels")),
        "confounds": design_confounds(recordings),
    }
    comparisons = []
    if focus and not summary.empty:
        for task, rows in summary.groupby("task"):
            for group_col, label in (("profile", "perfil"), ("store_label", "loja")):
                if rows[group_col].nunique() < 2:
                    continue
                table = compare_groups(rows, "focus_share", group_col)
                if not table.empty:
                    table.insert(0, "metric", "share de {}".format(focus))
                    table.insert(0, "by", label)
                    table.insert(0, "task", task)
                    comparisons.append(table)
    metrics["comparisons"] = pd.concat(comparisons, ignore_index=True) if comparisons else pd.DataFrame()
    metrics["findings"] = generate_findings(metrics, focus)
    metrics["limitations"] = limitations(model, metrics)
    return metrics
