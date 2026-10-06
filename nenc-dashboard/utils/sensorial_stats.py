"""
Estatística das comparações do Teste Sensorial.

O estudo é intra-sujeito: cada participante passa por todas as condições.
Por isso a comparação é pareada, pela média de cada participante, com o
teste de postos sinalizados de Wilcoxon (scipy, exato quando cabe). O efeito
vem em duas medidas que não dependem de normalidade: a diferença mediana e o
r de postos (correlação bisserial de postos pareada, de −1 a 1).

Muitas comparações por índice e etapa inflam o falso positivo: o p de cada
família (índice × etapa) passa pela correção de Holm, e só o que passa nela é
"diferença". Abaixo de `MIN_PAIRS` pares, a comparação fica só descritiva.
"""

import math
from typing import Dict, List, Sequence

import numpy as np

MIN_PAIRS = 5
ALPHA = 0.05

DIFFERENCE = "diferença"
TREND = "tendência"
NO_DIFFERENCE = "sem diferença"
DESCRIPTIVE = "descritivo"


def rank_biserial(differences: Sequence[float]) -> float:
    """r de postos pareado: (soma dos postos positivos − negativos) / soma dos postos, sem os zeros."""
    from scipy.stats import rankdata

    values = np.asarray([d for d in differences if d == d and d != 0], dtype=float)
    if not values.size:
        return 0.0 if len(differences) else math.nan
    ranks = rankdata(np.abs(values))
    return float((ranks[values > 0].sum() - ranks[values < 0].sum()) / ranks.sum())


def paired_test(a: Sequence[float], b: Sequence[float], min_pairs: int = MIN_PAIRS) -> Dict[str, float]:
    """Wilcoxon pareado de `a` contra `b` (alinhados por participante; par com falta sai)."""
    from scipy.stats import wilcoxon

    first = np.asarray(a, dtype=float)
    second = np.asarray(b, dtype=float)
    keep = ~(np.isnan(first) | np.isnan(second))
    first, second = first[keep], second[keep]
    differences = first - second
    result = {
        "n": int(first.size),
        "media_a": float(first.mean()) if first.size else math.nan,
        "media_b": float(second.mean()) if second.size else math.nan,
        "mediana_a": float(np.median(first)) if first.size else math.nan,
        "mediana_b": float(np.median(second)) if second.size else math.nan,
        "diferenca_mediana": float(np.median(differences)) if differences.size else math.nan,
        "r": rank_biserial(differences),
        "p": math.nan,
        "metodo": DESCRIPTIVE,
    }
    if first.size < min_pairs:
        return result
    if not np.any(differences != 0):
        result.update(p=1.0, metodo="wilcoxon")
        return result
    statistic = wilcoxon(first, second, zero_method="wilcox", alternative="two-sided")
    result.update(p=float(statistic.pvalue), metodo="wilcoxon")
    return result


def holm(p_values: Sequence[float]) -> List[float]:
    """p corrigido por Holm, na ordem recebida. NaN fica NaN e não conta na família."""
    indexed = [(p, i) for i, p in enumerate(p_values) if p == p]
    m = len(indexed)
    adjusted = [math.nan] * len(p_values)
    running = 0.0
    for rank, (p, index) in enumerate(sorted(indexed)):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[index] = running
    return adjusted


def verdict(p: float, p_holm: float, alpha: float = ALPHA) -> str:
    """"diferença" só com o p de Holm abaixo de alfa; "tendência" com o p bruto."""
    if p != p:
        return DESCRIPTIVE
    if p_holm == p_holm and p_holm < alpha:
        return DIFFERENCE
    if p < alpha:
        return TREND
    return NO_DIFFERENCE


def describe(values: Sequence[float]) -> Dict[str, float]:
    """n, média, desvio, erro padrão e mediana do que não é NaN."""
    data = np.asarray([v for v in values if v == v], dtype=float)
    n = int(data.size)
    deviation = float(data.std(ddof=1)) if n > 1 else math.nan
    return {
        "n": n,
        "media": float(data.mean()) if n else math.nan,
        "dp": deviation,
        "ep": deviation / math.sqrt(n) if n > 1 else math.nan,
        "mediana": float(np.median(data)) if n else math.nan,
    }
