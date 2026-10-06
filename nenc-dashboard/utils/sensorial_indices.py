"""
Índices de EEG do relatório, calculados da potência por canal e banda (PSD).

As fórmulas são as da sintaxe do SPSS usada nos estudos: médias regionais
(MEAN do SPSS: a média do que existe, sem exigir todos os canais), log10 com
piso de 1e-30 nas assimetrias e razão só com denominador positivo. Calculados
sobre o PSD do pipeline, batem com o consolidado do SPSS janela a janela.

O PPI é a soma ponderada de três componentes em escalas muito diferentes (a
memória vem em potência absoluta, perto de 1e-12, e na fórmula do SPSS quase
não pesa). Por padrão os componentes entram padronizados (z, no conjunto
analisado); o modo "spss" repete a fórmula original.
"""

from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

CHANNELS = ("Fp1", "Fp2", "F3", "F4", "C3", "C4", "Cz", "Fz", "P3", "P4", "T3", "T4", "F7", "F8", "T5", "T6")
BANDS = ("Delta", "Theta", "Alpha", "Beta", "Gamma")
BAND_COLUMNS = tuple("{}_{}".format(channel, band) for channel in CHANNELS for band in BANDS)
LOG_FLOOR = 1e-30
PPI_COMPONENTS = ("FAI", "ATTENTION_IDX_MID", "MEMORY_IDX")
DEFAULT_PPI_WEIGHTS = {"FAI": 0.4, "ATTENTION_IDX_MID": 0.3, "MEMORY_IDX": 0.3}


def has_bands(frame: pd.DataFrame) -> bool:
    return all(column in frame for column in ("F3_Alpha", "F4_Alpha"))


def _mean(frame: pd.DataFrame, band: str, channels: Iterable[str]) -> pd.Series:
    """MEAN do SPSS: média dos canais presentes na linha."""
    columns = ["{}_{}".format(channel, band) for channel in channels if "{}_{}".format(channel, band) in frame]
    if not columns:
        return pd.Series(np.nan, index=frame.index)
    return frame[columns].astype("float64").mean(axis=1, skipna=True)


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    valid = numerator.notna() & denominator.notna() & (denominator > 0)
    return (numerator / denominator).where(valid)


def _log_asymmetry(frame: pd.DataFrame, right: str, left: str) -> pd.Series:
    if right not in frame or left not in frame:
        return pd.Series(np.nan, index=frame.index)
    a = frame[right].astype("float64")
    b = frame[left].astype("float64")
    value = np.log10(a.clip(lower=LOG_FLOOR)) - np.log10(b.clip(lower=LOG_FLOOR))
    return value.where(a.notna() & b.notna())


def compute_indices(psd: pd.DataFrame) -> pd.DataFrame:
    """Os nove índices por janela (sem o PPI, que depende do conjunto analisado)."""
    out = pd.DataFrame(index=psd.index)
    out["FAI"] = _log_asymmetry(psd, "F4_Alpha", "F3_Alpha")
    out["TEMP_ASYM"] = _log_asymmetry(psd, "T4_Alpha", "T3_Alpha")
    out["ALPHA_THETA"] = _ratio(_mean(psd, "Alpha", ("C3", "C4", "P3", "P4")),
                                _mean(psd, "Theta", ("C3", "C4", "P3", "P4")))
    post_temp = ("P3", "P4", "T3", "T4", "T5", "T6")
    out["BETA_GAMMA"] = _ratio(_mean(psd, "Beta", post_temp), _mean(psd, "Gamma", post_temp))
    for code, channels in (("ATTENTION_IDX", ("F3", "F4", "P3", "P4")),
                           ("ATTENTION_IDX_MID", ("F3", "F4", "Fz", "Cz", "P3", "P4"))):
        high = pd.concat([_mean(psd, "Beta", channels), _mean(psd, "Gamma", channels)], axis=1).mean(axis=1)
        low = pd.concat([_mean(psd, "Alpha", channels), _mean(psd, "Theta", channels)], axis=1).mean(axis=1)
        out[code] = _ratio(high, low)
    memory = ("P3", "P4", "T5", "T6")
    theta_mem, gamma_mem = _mean(psd, "Theta", memory), _mean(psd, "Gamma", memory)
    out["MEMORY_IDX"] = ((theta_mem + gamma_mem) / 2).where(theta_mem.notna() & gamma_mem.notna())
    front = ("Fp1", "Fp2", "F3", "F4", "Fz")
    out["AROUSAL_FRONT"] = _ratio(_mean(psd, "Beta", front), _mean(psd, "Alpha", front))
    out["MIDLINE_AROUSAL"] = _ratio(_mean(psd, "Beta", ("Fz", "Cz")), _mean(psd, "Alpha", ("Fz", "Cz")))
    return out


def zscore(values: pd.Series) -> pd.Series:
    """Padronização com o desvio amostral (ddof=1), como a DESCRIPTIVES /SAVE do SPSS."""
    values = values.astype("float64")
    deviation = values.std(ddof=1)
    if not np.isfinite(deviation) or deviation == 0:
        return pd.Series(np.nan, index=values.index)
    return (values - values.mean()) / deviation


def ppi(indices: pd.DataFrame, mode: str = "z", weights: Optional[Dict[str, float]] = None) -> pd.Series:
    """PPI das linhas recebidas: componentes padronizados nelas (`z`) ou crus (`spss`)."""
    weights = dict(DEFAULT_PPI_WEIGHTS, **(weights or {}))
    parts = []
    for component in PPI_COMPONENTS:
        values = indices[component].astype("float64")
        parts.append(float(weights.get(component, 0.0)) * (zscore(values) if mode == "z" else values))
    total = parts[0] + parts[1] + parts[2]
    complete = indices[list(PPI_COMPONENTS)].notna().all(axis=1)
    return total.where(complete)
