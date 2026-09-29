"""Planilhas para Excel e Power BI: valores seguros, abas divididas e nomes válidos.

Compartilhado pelos exports da Prosódia e da Jornada de Compra.
"""

import io
import json
import re
import unicodedata
from typing import Any, Dict

import pandas as pd


EXCEL_SAFE_MAX_DATA_ROWS = 1_000_000
EXCEL_SHEET_NAME_MAX_LENGTH = 31
# Uma celula do Excel guarda no maximo 32.767 caracteres.
EXCEL_CELL_MAX_CHARS = 32_767
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
