"""
Jornada Loader — Carrega e valida dados de Eye-Tracking (Jornada de Compra).

Suporta 6 tipos de arquivo (todos opcionais):
- Tabelas: dados brutos por participante × AOI
- PorMarca: agrupados por marca com metadados
- Médias: médias por AOI
- TBVisualShare: share visual por marca
- ANOVA: testes de significância estatística
- Consolidado: Excel consolidado (mesmo formato de Tabelas)
"""

import pandas as pd
import io
from pathlib import Path
from typing import Dict, List, Tuple


# Colunas numéricas esperadas nos dados de eye-tracking
NUMERIC_COLS = [
    "TotalGazeDuration", "NormalizedGazeDuration", "AverageGazeDuration",
    "MaximumGazeDuration", "MinimumGazeDuration", "GazeCount",
    "TimeToFirstFixation", "GazedAtBy", "AOITransitionRate",
    "FixationCount", "SaccadeCount", "GazePointValidity",
    "AverageFixationDuration", "AverageSaccadeDuration",
    "AverageSaccadeLength", "FixationRate", "FixationSaccadeTimeRatio",
    "ScanPathLength", "ScanPathDuration", "ScanPathArea", "MeanPupilDilation",
]

# Métricas principais para análise
KEY_METRICS = [
    "TotalGazeDuration", "NormalizedGazeDuration", "AverageGazeDuration",
    "GazeCount", "TimeToFirstFixation", "FixationCount",
    "AverageFixationDuration", "ScanPathLength",
]

# Métricas para Visual Share
VISUAL_SHARE_METRICS = [
    "Soma de TotalGazeDuration", "Média de AverageGazeDuration",
]


def _convert_european_decimals(df: pd.DataFrame) -> pd.DataFrame:
    """Converte colunas com vírgula decimal para float."""
    for col in df.columns:
        if df[col].dtype == object:
            try:
                converted = df[col].str.replace(",", ".", regex=False)
                converted = pd.to_numeric(converted, errors="coerce")
                if converted.notna().sum() > 0:
                    df[col] = converted
            except (AttributeError, TypeError):
                pass
    return df


def _read_source(source, **kwargs) -> pd.DataFrame:
    """Lê DataFrame de um caminho ou UploadedFile."""
    if isinstance(source, (Path, str)):
        path = Path(source)
        if path.suffix == ".xlsx":
            return pd.read_excel(path, **kwargs)
        return pd.read_csv(path, **kwargs)
    name = source.name
    if name.endswith(".xlsx"):
        return pd.read_excel(source, **kwargs)
    return pd.read_csv(source, **kwargs)


# ------------------------------------------------------------------
# Tabelas (per-participant × AOI)
# ------------------------------------------------------------------

def load_tabelas(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega Banco_Tabelas.csv.

    Formato: CSV com vírgula como delimitador, decimais europeus entre aspas.
    Coluna 'Nome da Origem' = ID do participante (ex: '01-Thais.csv').
    """
    errors: List[str] = []
    try:
        df = _read_source(source)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar Tabelas: {e}"]

    # Renomear coluna de participante
    if "Nome da Origem" in df.columns:
        df = df.rename(columns={"Nome da Origem": "Participante"})

    # Converter decimais europeus
    df = _convert_european_decimals(df)

    # Remover linhas totalmente vazias
    df = df.dropna(how="all")

    if "Participante" not in df.columns and "AOI" not in df.columns:
        errors.append("Tabelas: colunas 'Nome da Origem' e 'AOI' não encontradas.")

    return df, errors


# ------------------------------------------------------------------
# Consolidado (Excel — mesmo formato do Compilado-participantes)
# ------------------------------------------------------------------

def load_consolidado(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega Banco_Consolidado.xlsx.

    Formato: Excel com participantes separados por linhas de cabeçalho repetidas.
    Colunas: ID, Nome, AOI, + métricas numéricas.
    Linhas com ID vazio marcam separação de participantes (header repetido).
    """
    errors: List[str] = []
    try:
        df = _read_source(source)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar Consolidado: {e}"]

    # Detectar e remover linhas de cabeçalho repetidas
    if "ID" in df.columns:
        # Linhas onde ID é NaN e a coluna AOI contém "AOI" são headers repetidos
        header_mask = df["ID"].isna() & (
            df.get("AOI", pd.Series(dtype=str)).astype(str).str.strip() == "AOI"
        )
        df = df[~header_mask].copy()

        # Propagar ID e Nome para linhas seguintes
        if "Nome" in df.columns:
            df["ID"] = df["ID"].ffill()
            df["Nome"] = df["Nome"].ffill()

    # Converter decimais europeus
    df = _convert_european_decimals(df)

    # Remover linhas sem AOI
    if "AOI" in df.columns:
        df = df.dropna(subset=["AOI"])
        df = df[df["AOI"].astype(str).str.strip() != ""]

    # Criar coluna Participante combinando ID e Nome
    if "ID" in df.columns and "Nome" in df.columns:
        df["Participante"] = (
            df["ID"].astype(str).str.strip()
            + "-"
            + df["Nome"].astype(str).str.strip()
        )

    return df, errors


# ------------------------------------------------------------------
# PorMarca (agrupado por marca)
# ------------------------------------------------------------------

def load_por_marca(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega Banco_PorMarca.csv.

    Formato: primeira coluna rotulada "AOI" (apenas na 1ª linha),
    segunda coluna = nome do AOI, depois Marca, Tipo, Linha, Emb, Interação + métricas.
    """
    errors: List[str] = []
    try:
        df = _read_source(source)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar PorMarca: {e}"]

    # A primeira coluna pode ter "AOI" apenas na primeira linha de dados
    first_col = df.columns[0]
    second_col = df.columns[1]

    # Renomear colunas para padronizar
    if first_col == "" or first_col.startswith("Unnamed"):
        df = df.rename(columns={first_col: "_label", second_col: "AOI"})
    elif first_col == "AOI":
        df = df.rename(columns={first_col: "_label", second_col: "AOI"})

    # Converter decimais europeus
    df = _convert_european_decimals(df)

    # Remover linhas sem AOI
    if "AOI" in df.columns:
        df = df.dropna(subset=["AOI"])
        df = df[df["AOI"].astype(str).str.strip() != ""]

    # Limpar coluna _label
    if "_label" in df.columns:
        df = df.drop(columns=["_label"])

    return df, errors


# ------------------------------------------------------------------
# Médias (médias por AOI)
# ------------------------------------------------------------------

def load_medias(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega Banco_medias.csv.

    Formato similar ao PorMarca: ADI na 1ª coluna (apenas 1ª linha),
    AOI na 2ª coluna, depois Marca, Tipo, Emb, Interação + métricas.
    """
    errors: List[str] = []
    try:
        df = _read_source(source)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar Médias: {e}"]

    first_col = df.columns[0]
    second_col = df.columns[1]

    # Renomear colunas para padronizar
    if first_col in ("ADI", "AOI", "") or first_col.startswith("Unnamed"):
        df = df.rename(columns={first_col: "_label", second_col: "AOI"})

    # Converter decimais europeus
    df = _convert_european_decimals(df)

    if "AOI" in df.columns:
        df = df.dropna(subset=["AOI"])
        df = df[df["AOI"].astype(str).str.strip() != ""]

    if "_label" in df.columns:
        df = df.drop(columns=["_label"])

    return df, errors


# ------------------------------------------------------------------
# TBVisualShare (share visual por marca)
# ------------------------------------------------------------------

def load_visual_share(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega Banco_TBVisualShare.csv.

    Formato multi-seção:
    - Linha 0: "Interação, - todas -, "
    - Linha 2: cabeçalho "Marca, Soma de TotalGazeDuration, Média de AverageGazeDuration"
    - Linhas seguintes: dados por marca até "Total Resultado"
    - Seções seguintes: percentuais (podem conter fórmulas Excel =B12/$B$16)
    """
    errors: List[str] = []
    try:
        if isinstance(source, (Path, str)):
            with open(source, "r", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            content = source.read()
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            source.seek(0)
            lines = content.splitlines(keepends=True)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar VisualShare: {e}"]

    # Encontrar a seção principal (primeira tabela com dados por marca)
    data_rows = []
    header_found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Marca,"):
            header_found = True
            continue
        if header_found:
            if not stripped or stripped == ",,":
                break
            parts = stripped.split(",")
            marca = parts[0].strip().strip('"')
            if marca and marca != "Total Resultado":
                # Extrair valores numéricos (podem estar entre aspas com vírgula)
                try:
                    csv_df = pd.read_csv(
                        io.StringIO(stripped),
                        header=None,
                        names=["Marca", "Soma de TotalGazeDuration", "Média de AverageGazeDuration"],
                    )
                    data_rows.append(csv_df.iloc[0])
                except Exception:
                    pass

    if not data_rows:
        return pd.DataFrame(), ["VisualShare: nenhum dado encontrado."]

    df = pd.DataFrame(data_rows).reset_index(drop=True)
    df = _convert_european_decimals(df)

    return df, errors


# ------------------------------------------------------------------
# ANOVA (testes estatísticos)
# ------------------------------------------------------------------

def load_anova(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega Banco_ANOVA.csv.

    Formato: primeira linha "ANOVA,,,,,,", segunda linha cabeçalho.
    Cada métrica tem 3 sub-linhas: "Entre Grupos", "Nos grupos", "Total".
    O nome da métrica aparece apenas na primeira sub-linha.
    """
    errors: List[str] = []
    try:
        df = _read_source(source, header=1)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar ANOVA: {e}"]

    # Renomear colunas
    cols = df.columns.tolist()
    if len(cols) >= 2:
        if cols[0] == "" or str(cols[0]).startswith("Unnamed"):
            cols[0] = "Métrica"
        if cols[1] == "" or str(cols[1]).startswith("Unnamed"):
            cols[1] = "Grupo"
        df.columns = cols

    # Propagar nome da métrica
    if "Métrica" in df.columns:
        df["Métrica"] = df["Métrica"].replace("", pd.NA).ffill()

    # Converter decimais europeus
    df = _convert_european_decimals(df)

    # Remover linhas vazias
    df = df.dropna(how="all")

    return df, errors


# ------------------------------------------------------------------
# Entrevistas (transcrições qualitativas)
# ------------------------------------------------------------------

def load_entrevistas(source) -> Tuple[pd.DataFrame, List[str]]:
    """
    Carrega arquivo de entrevistas (CSV ou Excel).

    Formato esperado: colunas 'arquivo', 'ep', 'identificacao', 'texto'.
    """
    errors: List[str] = []
    try:
        df = _read_source(source)
    except Exception as e:
        return pd.DataFrame(), [f"Erro ao carregar Entrevistas: {e}"]

    df = df.dropna(how="all")

    expected_cols = {"arquivo", "ep", "identificacao", "texto"}
    missing = expected_cols - set(df.columns.str.lower())
    if missing:
        errors.append(
            f"Entrevistas: colunas esperadas não encontradas: {', '.join(sorted(missing))}."
        )

    return df, errors


# ------------------------------------------------------------------
# Upload combinado
# ------------------------------------------------------------------

def load_jornada_from_upload(
    tabelas_file=None,
    por_marca_file=None,
    medias_file=None,
    visual_share_file=None,
    anova_file=None,
    consolidado_file=None,
    entrevistas_file=None,
) -> Dict:
    """Carrega dados de Jornada a partir de arquivos enviados pelo usuário."""
    results: Dict = {}
    errors: List[str] = []

    loaders = [
        (tabelas_file, "tabelas", load_tabelas),
        (por_marca_file, "por_marca", load_por_marca),
        (medias_file, "medias", load_medias),
        (visual_share_file, "visual_share", load_visual_share),
        (anova_file, "anova", load_anova),
        (consolidado_file, "consolidado", load_consolidado),
        (entrevistas_file, "entrevistas", load_entrevistas),
    ]

    for file, key, loader in loaders:
        if file is not None:
            df, errs = loader(file)
            if not df.empty:
                results[key] = df
            errors.extend(errs)

    if errors:
        results["_errors"] = errors

    return results


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def get_jornada_participants(data: Dict) -> List[str]:
    """Retorna lista de participantes dos dados de Jornada."""
    participants: set = set()

    for key in ("tabelas", "consolidado"):
        if key in data and "Participante" in data[key].columns:
            participants.update(
                data[key]["Participante"].dropna().unique()
            )

    # Fallback: Nome da coluna Nome no consolidado
    if not participants:
        for key in ("tabelas", "consolidado"):
            if key in data and "Nome" in data[key].columns:
                participants.update(
                    data[key]["Nome"].dropna().unique()
                )

    return sorted(str(p) for p in participants)


def get_jornada_aois(data: Dict) -> List[str]:
    """Retorna lista de AOIs únicos."""
    aois: set = set()
    for key in ("tabelas", "consolidado", "por_marca", "medias"):
        if key in data and "AOI" in data[key].columns:
            aois.update(data[key]["AOI"].dropna().unique())
    return sorted(str(a).strip() for a in aois if str(a).strip())


def get_jornada_marcas(data: Dict) -> List[str]:
    """Retorna lista de marcas únicas."""
    marcas: set = set()
    for key in ("por_marca", "medias", "visual_share"):
        if key in data and "Marca" in data[key].columns:
            marcas.update(data[key]["Marca"].dropna().unique())
    return sorted(str(m) for m in marcas)


def get_jornada_summary(data: Dict) -> Dict:
    """Retorna resumo dos dados de Jornada."""
    summary: Dict = {}
    summary["arquivos_carregados"] = [
        k for k in data if k != "_errors"
    ]
    summary["n_arquivos"] = len(summary["arquivos_carregados"])
    summary["n_participantes"] = len(get_jornada_participants(data))
    summary["n_aois"] = len(get_jornada_aois(data))
    summary["n_marcas"] = len(get_jornada_marcas(data))

    for key in ("tabelas", "consolidado", "por_marca", "medias", "entrevistas"):
        if key in data:
            summary[f"n_linhas_{key}"] = len(data[key])

    return summary
