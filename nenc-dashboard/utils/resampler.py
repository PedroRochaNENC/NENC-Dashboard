"""
Resampler — Alinha dados EEG e Periféricos em timeline unificada.

EEG (indicadores) tem Tempo = timestamp de fim-de-janela por Etapa (step 0.25s).
Periféricos tem 1 valor por Etapa com Tempo = duração total.

Estratégia: para cada (filename, Etapa), replica o valor de periféricos
para cada ponto temporal do EEG, criando timeline global contínua
(Etapas empilhadas sequencialmente).
"""

import pandas as pd
from typing import Dict, List, Optional


def build_unified_timeline(
    indicadores: pd.DataFrame,
    perifericos: pd.DataFrame,
    filename: Optional[str] = None,
) -> pd.DataFrame:
    """
    Constrói timeline unificada mesclando indicadores EEG e periféricos.

    Para cada participante, empilha as Etapas sequencialmente e replica
    os valores de periféricos (step function) em cada janela EEG.

    Args:
        indicadores: DataFrame com indicadores (Tempo por janela).
        perifericos: DataFrame com periféricos (1 linha por Etapa).
        filename: Se fornecido, filtra para um participante específico.

    Returns:
        DataFrame com Tempo_global e todas as métricas alinhadas.
    """
    if indicadores.empty:
        return pd.DataFrame()

    ind = indicadores.copy()
    per = perifericos.copy() if perifericos is not None and not perifericos.empty else pd.DataFrame()

    if filename:
        ind = ind[ind["filename"] == filename]
        if not per.empty:
            per = per[per["filename"] == filename]

    if ind.empty:
        return pd.DataFrame()

    all_merged = []

    for fn in ind["filename"].unique():
        fn_ind = ind[ind["filename"] == fn]
        fn_per = per[per["filename"] == fn] if not per.empty else pd.DataFrame()

        # Etapas na ordem de aparecimento no DataFrame
        etapas = fn_ind["Etapa"].unique()
        global_offset = 0.0

        for etapa in etapas:
            etapa_ind = fn_ind[fn_ind["Etapa"] == etapa].sort_values("Tempo").copy()
            if etapa_ind.empty:
                continue

            # Timeline global: offset + tempo local
            etapa_ind["Tempo_global"] = global_offset + etapa_ind["Tempo"].values
            etapa_ind["Etapa_inicio"] = global_offset + etapa_ind["Tempo"].min()
            etapa_ind["Etapa_fim"] = global_offset + etapa_ind["Tempo"].max()

            # Expandir periféricos (step function)
            if not fn_per.empty and "Etapa" in fn_per.columns:
                etapa_per = fn_per[fn_per["Etapa"] == etapa]
                if not etapa_per.empty:
                    per_row = etapa_per.iloc[0]
                    per_cols = [
                        c for c in etapa_per.columns
                        if c not in ("filename", "Etapa", "Tempo")
                    ]
                    for col in per_cols:
                        etapa_ind[col] = per_row[col]

            all_merged.append(etapa_ind)
            global_offset += etapa_ind["Tempo"].max()

    if not all_merged:
        return pd.DataFrame()

    return pd.concat(all_merged, ignore_index=True)


def compute_participant_average(
    indicadores: pd.DataFrame,
    perifericos: pd.DataFrame,
) -> tuple:
    """
    Calcula média entre participantes para visualização de timeline.

    Agrupa indicadores por (Etapa, Tempo) e periféricos por Etapa,
    retornando DataFrames "virtuais" com filename='Média Geral'
    que podem ser passados diretamente para build_unified_timeline.

    Returns:
        (avg_indicadores, avg_perifericos)
    """
    meta_cols = {"filename", "Codigo"}
    group_cols = ["Etapa", "Tempo"]
    available_group = [c for c in group_cols if c in indicadores.columns]

    num_cols = [
        c for c in indicadores.columns
        if c not in meta_cols.union(set(group_cols))
        and pd.api.types.is_numeric_dtype(indicadores[c])
    ]

    avg_ind = indicadores.groupby(available_group)[num_cols].mean().reset_index()
    avg_ind["filename"] = "Média Geral"

    avg_per = pd.DataFrame()
    if perifericos is not None and not perifericos.empty:
        per_num = [
            c for c in perifericos.columns
            if c not in ("filename", "Etapa", "Tempo")
            and pd.api.types.is_numeric_dtype(perifericos[c])
        ]
        if per_num and "Etapa" in perifericos.columns:
            avg_per = perifericos.groupby("Etapa")[per_num].mean().reset_index()
            avg_per["filename"] = "Média Geral"

    return avg_ind, avg_per


def get_etapa_boundaries(merged: pd.DataFrame) -> List[Dict]:
    """
    Extrai limites de início/fim de cada Etapa na timeline global.

    Returns:
        Lista de dicts: [{"etapa": str, "inicio": float, "fim": float}, ...]
    """
    if merged.empty or "Etapa" not in merged.columns:
        return []

    boundaries = []
    seen: set = set()

    for etapa in merged["Etapa"].unique():
        s = str(etapa)
        if s in seen:
            continue
        seen.add(s)

        etapa_data = merged[merged["Etapa"] == etapa]
        if "Tempo_global" in etapa_data.columns:
            boundaries.append({
                "etapa": s,
                "inicio": float(etapa_data["Tempo_global"].min()),
                "fim": float(etapa_data["Tempo_global"].max()),
            })

    return boundaries
