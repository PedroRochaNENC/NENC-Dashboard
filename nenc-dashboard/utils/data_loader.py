"""
Data Loader — Carrega e valida dados do pipeline NENC.

Suporta dois modos:
- Upload: usuário envia arquivos via st.file_uploader
- Pasta: usuário fornece caminho para 2.2.Dados Processados/
"""

import streamlit as st
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Colunas obrigatórias por arquivo
REQUIRED_COLUMNS = {
    "indicadores": ["filename", "Etapa", "Tempo"],
    "perifericos": ["filename", "Etapa", "Tempo"],
    "psd_results": ["filename", "Etapa", "Tempo"],
}

INDICADORES_METRICS = [
    "atencao", "WTP", "Alpha/Beta", "sens_asym", "inst_sens",
    "Memoria", "Memoria_log", "assimetria", "assimetria2",
    "AWI_frontal", "engagement_score",
]

PERIFERICOS_METRICS_RAW = ["BPM", "RMSSD", "GSR_CAL_mean", "GSR_RAW_mean"]
PERIFERICOS_METRICS_Z = ["BPM_zscore", "RMSSD_zscore", "GSR_CAL_zscore", "GSR_RAW_zscore"]


def _read_file(source) -> pd.DataFrame:
    """Lê DataFrame de um caminho (Path/str) ou UploadedFile."""
    if isinstance(source, (Path, str)):
        path = Path(source)
        if path.suffix == ".xlsx":
            return pd.read_excel(path)
        return pd.read_csv(path)
    # Streamlit UploadedFile
    name = source.name
    if name.endswith(".xlsx"):
        return pd.read_excel(source)
    return pd.read_csv(source)


def load_file(source, dataset_key: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega um arquivo e valida colunas obrigatórias.

    Returns:
        (DataFrame, lista de avisos/erros)
    """
    errors: List[str] = []
    try:
        df = _read_file(source)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar: {e}"]

    required = REQUIRED_COLUMNS.get(dataset_key, [])
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"Colunas obrigatórias ausentes em {dataset_key}: {missing}")

    return df, errors


@st.cache_data
def load_from_folder(folder_path: str) -> Dict:
    """Carrega todos os outputs do pipeline a partir de 2.2.Dados Processados/."""
    base = Path(folder_path)
    results: Dict = {}
    errors: List[str] = []

    # Indicadores (EEG)
    for fname in ["indicadores.xlsx", "indicadores.csv"]:
        p = base / "2.EEG" / fname
        if p.exists():
            df, errs = load_file(p, "indicadores")
            results["indicadores"] = df
            errors.extend(errs)
            break

    # PSD Results
    for fname in ["psd_results.xlsx", "psd_results.csv"]:
        p = base / "2.EEG" / fname
        if p.exists():
            df, errs = load_file(p, "psd_results")
            results["psd_results"] = df
            errors.extend(errs)
            break

    # Periféricos — tenta com e sem acento
    for folder_name in ["3.Periféricos", "3.Perifericos"]:
        for fname in ["perifericos_metrics.csv", "perifericos_metrics.xlsx"]:
            p = base / folder_name / fname
            if p.exists():
                df, errs = load_file(p, "perifericos")
                results["perifericos"] = df
                errors.extend(errs)
                break
        if "perifericos" in results:
            break

    if errors:
        results["_errors"] = errors

    return results


def load_from_uploads(
    indicadores_file=None,
    perifericos_file=None,
    psd_file=None,
) -> Dict:
    """Carrega dados a partir de arquivos enviados pelo usuário."""
    results: Dict = {}
    errors: List[str] = []

    if indicadores_file is not None:
        df, errs = load_file(indicadores_file, "indicadores")
        results["indicadores"] = df
        errors.extend(errs)

    if perifericos_file is not None:
        df, errs = load_file(perifericos_file, "perifericos")
        results["perifericos"] = df
        errors.extend(errs)

    if psd_file is not None:
        df, errs = load_file(psd_file, "psd_results")
        results["psd_results"] = df
        errors.extend(errs)

    if errors:
        results["_errors"] = errors

    return results


def get_participants(data: Dict) -> List[str]:
    """Retorna lista ordenada de participantes (valores únicos de filename)."""
    participants: set = set()
    for key in ("indicadores", "perifericos", "psd_results"):
        if key in data and "filename" in data[key].columns:
            participants.update(data[key]["filename"].dropna().unique())
    return sorted(str(p) for p in participants)


def get_etapas(data: Dict, filename: Optional[str] = None) -> List[str]:
    """Retorna etapas únicas preservando ordem de aparecimento."""
    etapas: List[str] = []
    for key in ("indicadores", "perifericos"):
        if key in data and "Etapa" in data[key].columns:
            df = data[key]
            if filename:
                df = df[df["filename"] == filename]
            etapas.extend(df["Etapa"].dropna().unique())
    # Preservar ordem, deduplicar
    seen: set = set()
    result: List[str] = []
    for e in etapas:
        s = str(e)
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def get_data_summary(data: Dict) -> Dict:
    """Retorna resumo estatístico dos dados carregados."""
    summary: Dict = {}
    participants = get_participants(data)
    summary["n_participantes"] = len(participants)

    if "indicadores" in data:
        df = data["indicadores"]
        summary["n_etapas"] = df["Etapa"].nunique()
        summary["n_linhas_indicadores"] = len(df)
        if "Tempo" in df.columns:
            summary["tempo_max_s"] = round(df["Tempo"].max(), 2)

    if "perifericos" in data:
        summary["n_linhas_perifericos"] = len(data["perifericos"])

    if "psd_results" in data:
        df = data["psd_results"]
        summary["n_linhas_psd"] = len(df)
        meta = {"filename", "Codigo", "Etapa", "Tempo"}
        summary["n_colunas_psd"] = len([c for c in df.columns if c not in meta])

    return summary
