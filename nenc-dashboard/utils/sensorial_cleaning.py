"""
Limpeza das janelas e tentativas do Teste Sensorial.

Duas fontes, nesta ordem:

1. **BASE LIMPA** enviada (as chaves da aba do consolidado do SPSS): quando o
   projeto tem a de uma camada, ela decide quais janelas (EEG, periféricos) ou
   tentativas (teste de associação) entram — é a curadoria do analista;
2. **Regra de outliers da sintaxe do SPSS**, desligada por padrão: ln de cada
   banda × canal, z no conjunto, marca |z| > 3,29 e calcula a distância de
   Mahalanobis das 80 variáveis (p < 0,001 no qui-quadrado com 80 graus de
   liberdade). A linha sai com 5 marcas ou mais, ou com 1 marca e Mahalanobis.

A decisão do usuário sobre a sessão (Participantes) vem depois de tudo.
"""

from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

WINDOW_KEYS = ("sessao_id", "Etapa", "Bloco", "Tempo")
TRIAL_KEYS = ("sessao_id", "tentativa")


def window_key(frame: pd.DataFrame) -> pd.Series:
    """Chave de janela com o tempo arredondado a 0,01 s: o xlsx e o CSV gravam o tempo diferente."""
    tempo = pd.to_numeric(frame["Tempo"], errors="coerce").round(2)
    bloco = pd.to_numeric(frame["Bloco"], errors="coerce").round()
    return (frame["sessao_id"].astype(str) + "|" + frame["Etapa"].astype(str) + "|"
            + bloco.astype("Int64").astype(str) + "|" + tempo.map(lambda v: "{:.2f}".format(v)))


def in_base_limpa(frame: pd.DataFrame, keys: Optional[pd.DataFrame]) -> Optional[pd.Series]:
    """Se cada linha está na BASE LIMPA; None quando a camada não tem BASE LIMPA."""
    if keys is None or keys.empty:
        return None
    if all(column in keys for column in WINDOW_KEYS):
        return window_key(frame).isin(set(window_key(keys)))
    if all(column in keys for column in TRIAL_KEYS):
        wanted = set(zip(keys["sessao_id"].astype(str), pd.to_numeric(keys["tentativa"]).astype("Int64")))
        pairs = zip(frame["sessao_id"].astype(str), pd.to_numeric(frame["tentativa"], errors="coerce").astype("Int64"))
        return pd.Series([pair in wanted for pair in pairs], index=frame.index)
    return None


def outlier_flags(frame: pd.DataFrame, columns: Sequence[str], *, z: float = 3.29,
                  p_mahalanobis: float = 0.001, min_marks: int = 5) -> pd.DataFrame:
    """A regra de outliers da sintaxe do SPSS, linha a linha.

    Valor ≤ 0 não tem ln e fica de fora da contagem (como no SPSS); a
    Mahalanobis só existe para linhas com as 80 variáveis.
    """
    from scipy.stats import chi2

    columns = [column for column in columns if column in frame]
    values = frame[columns].astype("float64")
    logs = np.log(values.where(values > 0))
    means = logs.mean()
    deviations = logs.std(ddof=1).replace(0, np.nan)
    scores = (logs - means) / deviations
    positive = (scores > z).sum(axis=1)
    negative = (scores < -z).sum(axis=1)
    total = positive + negative

    distance = pd.Series(np.nan, index=frame.index)
    complete = logs.notna().all(axis=1)
    if complete.sum() > len(columns):
        matrix = logs[complete].to_numpy()
        centered = matrix - matrix.mean(axis=0)
        inverse = np.linalg.pinv(np.cov(matrix, rowvar=False))
        distance[complete] = np.einsum("ij,jk,ik->i", centered, inverse, centered)
    p_value = pd.Series(1 - chi2.cdf(distance, len(columns)), index=frame.index).where(distance.notna())
    multivariate = (p_value < p_mahalanobis).where(p_value.notna())
    remove = (total >= min_marks) | ((total >= 1) & (multivariate == 1))
    return pd.DataFrame({
        "marcas_positivas": positive.astype(int),
        "marcas_negativas": negative.astype(int),
        "marcas": total.astype(int),
        "mahalanobis": distance,
        "p_mahalanobis": p_value,
        "outlier_multivariado": multivariate,
        "valida": ~remove,
    }, index=frame.index)


def summary(flags: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    """Janelas removidas pela regra, por grupo (ex.: participante × condição × etapa)."""
    table = groups.copy()
    table["removida"] = ~flags["valida"]
    keys = list(groups.columns)
    result = table.groupby(keys, dropna=False)["removida"].agg(janelas="size", removidas="sum").reset_index()
    result["pct_removidas"] = result["removidas"] / result["janelas"]
    return result


def settings_text(settings: Dict) -> str:
    """Descrição curta da limpeza em uso, para avisos e exportações."""
    cleaning = settings.get("limpeza") or {}
    if not cleaning.get("regra_spss"):
        return "regra de outliers desligada"
    return "regra de outliers ligada (|z| > {}, Mahalanobis p < {}, {} marcas)".format(
        cleaning.get("z", 3.29), cleaning.get("p_mahalanobis", 0.001), cleaning.get("min_marcas", 5))
