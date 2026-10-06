"""Planilhas para Excel e Power BI: valores seguros, abas divididas e nomes válidos.

Compartilhado pelos exports da Prosódia, da Jornada de Compra e do Teste
Sensorial: além de gravar o arquivo, prepara as tabelas (texto que cabe na
célula, sem ±inf, cabeçalho estável), as abas de análises de IA e o
dicionário de colunas.
"""

import io
import json
import re
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


EXCEL_SAFE_MAX_DATA_ROWS = 1_000_000
EXCEL_SHEET_NAME_MAX_LENGTH = 31
# Uma celula do Excel guarda no maximo 32.767 caracteres.
EXCEL_CELL_MAX_CHARS = 32_767
# Folga para o aviso de corte caber na celula.
_TEXT_LIMIT = EXCEL_CELL_MAX_CHARS - 200
_CUT_NOTE = " […texto cortado no limite de uma célula do Excel]"
_INVALID_EXCEL_CHARACTERS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def safe_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_value).strip("_")
    return slug[:80] or "projeto"


def table_sheet_name(base_name: str, part: int) -> str:
    if part == 1:
        return base_name[:EXCEL_SHEET_NAME_MAX_LENGTH]
    suffix = f"_{part}"
    return base_name[: EXCEL_SHEET_NAME_MAX_LENGTH - len(suffix)] + suffix


def excel_value(value: Any) -> Any:
    """Valor que o Excel abre como dado: sem controle e sem virar fórmula."""
    if isinstance(value, (dict, list, tuple)):
        return json_text(value)
    if isinstance(value, str):
        clean_value = _INVALID_EXCEL_CHARACTERS.sub("", value)
        if clean_value.startswith(("=", "+", "-", "@")):
            return "'" + clean_value
        return clean_value
    return value


def write_workbook(
    tables: Dict[str, pd.DataFrame],
    max_rows_per_sheet: int = EXCEL_SAFE_MAX_DATA_ROWS,
) -> bytes:
    """Uma aba por tabela; tabela maior que o limite continua em `Nome_2`, `Nome_3`…"""
    if max_rows_per_sheet < 1:
        raise ValueError("O limite de linhas por aba deve ser maior que zero.")

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for table_name, frame in tables.items():
            safe_frame = frame.copy()
            for column in safe_frame.columns:
                safe_frame[column] = safe_frame[column].map(excel_value)

            row_count = len(safe_frame)
            part_count = max(1, (row_count + max_rows_per_sheet - 1) // max_rows_per_sheet)
            for part in range(1, part_count + 1):
                start = (part - 1) * max_rows_per_sheet
                end = start + max_rows_per_sheet
                safe_frame.iloc[start:end].to_excel(
                    writer,
                    sheet_name=table_sheet_name(table_name, part),
                    index=False,
                )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Preparo das tabelas
# ---------------------------------------------------------------------------

def cap_text(value):
    """Texto longo cortado no limite de uma célula, com aviso."""
    if isinstance(value, str) and len(value) > _TEXT_LIMIT:
        return value[:_TEXT_LIMIT] + _CUT_NOTE
    return value


def clean_frame(frame: Optional[pd.DataFrame], columns: Sequence[str] = ()) -> pd.DataFrame:
    """Tabela pronta para a planilha: cabeçalho estável, sem ±inf e texto que cabe na célula."""
    if frame is None or (frame.empty and len(frame.columns) == 0):
        return pd.DataFrame(columns=list(columns))
    out = frame.reset_index(drop=True).copy()
    numeric = out.select_dtypes(include="number").columns
    if len(numeric):
        out[numeric] = out[numeric].replace([np.inf, -np.inf], np.nan)
    for column in out.columns:
        if out[column].dtype == object:
            out[column] = out[column].map(cap_text)
    return out


def _first(item: Mapping, *keys: str):
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def analysis_rows(analyses: Iterable[Mapping], data_version) -> Tuple[List[Dict], List[Dict]]:
    """Linhas das abas de análises de IA e de citações; `is_current` compara a versão dos dados."""
    analysis_list, citation_list = [], []
    for analysis in analyses or ():
        analysis_list.append({
            "id": analysis.get("id"),
            "created_at": analysis.get("created_at"),
            "model": analysis.get("model"),
            "mode": analysis.get("mode"),
            "data_version": analysis.get("data_version"),
            "is_current": (analysis.get("data_version") is not None
                           and analysis.get("data_version") == data_version),
            "analysis_text": analysis.get("analysis_text"),
            "kb_file_id": analysis.get("kb_file_id"),
            "filters": json_text(analysis.get("filters") or {}),
            "search": json_text(analysis.get("search") or {}),
        })
        for index, citation in enumerate(analysis.get("citations") or [], start=1):
            data = citation if isinstance(citation, dict) else {"quote": str(citation)}
            citation_list.append({
                "analysis_id": analysis.get("id"),
                "citation_index": index,
                "file_id": _first(data, "file_id"),
                "filename": _first(data, "filename", "file_name", "source"),
                "quote": _first(data, "quote", "text", "content"),
                "score": _first(data, "score"),
                "citation_json": json_text(data),
            })
    return analysis_list, citation_list


def dictionary_frame(
    tables: Mapping[str, pd.DataFrame],
    sheet_descriptions: Mapping[str, str],
    column_descriptions: Mapping[str, str],
    fallback: Optional[Callable[[str], str]] = None,
    dictionary_sheet: str = "Dicionario",
) -> pd.DataFrame:
    """A aba Dicionário: cada aba e cada coluna, com a descrição (ou a de `fallback`)."""
    rows = []
    for sheet, frame in tables.items():
        for column in frame.columns:
            description = column_descriptions.get(column)
            if description is None and fallback is not None:
                description = fallback(str(column))
            rows.append({"aba": sheet, "descricao_aba": sheet_descriptions.get(sheet, ""),
                         "coluna": column, "descricao": description or ""})
    rows.append({"aba": dictionary_sheet, "descricao_aba": sheet_descriptions.get(dictionary_sheet, ""),
                 "coluna": "aba / coluna / descricao", "descricao": "Aba, coluna e o que ela contém."})
    return pd.DataFrame(rows, columns=["aba", "descricao_aba", "coluna", "descricao"])
