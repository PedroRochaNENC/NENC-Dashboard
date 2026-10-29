"""
Estatística das comparações do Teste Sensorial.

O estudo é intra-sujeito: cada participante passa por todas as condições.
Por isso a comparação é pareada, pela média de cada participante. O teste é o
de postos sinalizados de Wilcoxon (scipy, exato quando cabe) até a Aline
confirmar o da sintaxe dela; o t pareado já está aqui para a troca
(`settings["estatistica"]["teste"]`). O efeito vem como diferença mediana, r de
postos (−1 a 1) e diferença média com o IC 95%, que é o que o gráfico mostra.

O critério é o do relatório do estudo: "diferença" com p < alfa (0,05), sem
correção para múltiplas comparações; "tendência" até `alfa_tendencia` (0,10).
Abaixo de `MIN_PAIRS` pares, a comparação fica só descritiva.
"""

import math
from typing import Dict, Sequence

import numpy as np

MIN_PAIRS = 5
ALPHA = 0.05
ALPHA_TREND = 0.10
TESTS = ("wilcoxon", "t_pareado")

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


def ci95_half_width(deviation: float, n: int) -> float:
    """Meia largura do IC 95% da média, com o t de Student de n − 1 graus (como o SPSS)."""
    from scipy.stats import t

    if n < 2 or deviation != deviation:
        return math.nan
    return float(t.ppf(0.975, n - 1) * deviation / math.sqrt(n))


def paired_test(a: Sequence[float], b: Sequence[float], min_pairs: int = MIN_PAIRS,
                test: str = "wilcoxon") -> Dict[str, float]:
    """Teste pareado de `a` contra `b` (alinhados por participante; par com falta sai)."""
    from scipy.stats import ttest_rel, wilcoxon

    if test not in TESTS:
        raise ValueError("Teste desconhecido: {!r}".format(test))
    first = np.asarray(a, dtype=float)
    second = np.asarray(b, dtype=float)
    keep = ~(np.isnan(first) | np.isnan(second))
    first, second = first[keep], second[keep]
    differences = first - second
    deviation = float(differences.std(ddof=1)) if differences.size > 1 else math.nan
    half_width = ci95_half_width(deviation, int(differences.size))
    mean_difference = float(differences.mean()) if differences.size else math.nan
    result = {
        "n": int(first.size),
        "media_a": float(first.mean()) if first.size else math.nan,
        "media_b": float(second.mean()) if second.size else math.nan,
        "mediana_a": float(np.median(first)) if first.size else math.nan,
        "mediana_b": float(np.median(second)) if second.size else math.nan,
        "diferenca_mediana": float(np.median(differences)) if differences.size else math.nan,
        "diferenca_media": mean_difference,
        "ic_inf": mean_difference - half_width,
        "ic_sup": mean_difference + half_width,
        "r": rank_biserial(differences),
        "p": math.nan,
        "metodo": DESCRIPTIVE,
    }
    if first.size < min_pairs:
        return result
    if not np.any(differences != 0):
        result.update(p=1.0, metodo=test)
        return result
    if test == "t_pareado":
        statistic = ttest_rel(first, second)
    else:
        statistic = wilcoxon(first, second, zero_method="wilcox", alternative="two-sided")
    result.update(p=float(statistic.pvalue), metodo=test)
    return result


def verdict(p: float, alpha: float = ALPHA, alpha_trend: float = ALPHA_TREND) -> str:
    """"diferença" com p < alfa; "tendência" com p < alfa de tendência; sem p, descritivo."""
    if p != p:
        return DESCRIPTIVE
    if p < alpha:
        return DIFFERENCE
    if p < alpha_trend:
        return TREND
    return NO_DIFFERENCE


def describe(values: Sequence[float]) -> Dict[str, float]:
    """n, média, desvio, erro padrão, meia largura do IC 95% e mediana do que não é NaN."""
    data = np.asarray([v for v in values if v == v], dtype=float)
    n = int(data.size)
    deviation = float(data.std(ddof=1)) if n > 1 else math.nan
    return {
        "n": n,
        "media": float(data.mean()) if n else math.nan,
        "dp": deviation,
        "ep": deviation / math.sqrt(n) if n > 1 else math.nan,
        "ic95": ci95_half_width(deviation, n),
        "mediana": float(np.median(data)) if n else math.nan,
    }
