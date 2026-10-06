"""
Colunas escolhidas de uma aba de um .xlsx grande, lidas em streaming.

A base consolidada de EEG de um estudo passa de 150 MB em xlsx, e o openpyxl
leria todas as células de todas as abas. Aqui se lê o XML de uma só aba, uma
linha por vez, guardando só as colunas pedidas: as colunas com nome de
participante nunca chegam à memória. Serve ao script de envio (no computador
de quem envia) e ao servidor, quando a planilha chega pela tela.
"""

import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Sequence, Union

import pandas as pd

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_LETTERS = re.compile(r"^([A-Z]+)")

Source = Union[str, Path, BinaryIO]


def _fold(text: object) -> str:
    value = " ".join(str(text or "").split())
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _sheet_paths(archive: zipfile.ZipFile) -> Dict[str, str]:
    """Nome da aba -> caminho do XML dela dentro do pacote."""
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target") for rel in relations.iter(_PKG_REL + "Relationship")}
    paths = {}
    for sheet in workbook.iter(_MAIN + "sheet"):
        target = targets.get(sheet.get(_DOC_REL + "id"))
        if not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        paths[sheet.get("name")] = path
    return paths


def sheet_names(source: Source) -> List[str]:
    with zipfile.ZipFile(source) as archive:
        return list(_sheet_paths(archive))


def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    strings: List[str] = []
    with archive.open("xl/sharedStrings.xml") as handle:
        for _event, element in ElementTree.iterparse(handle, events=("end",)):
            if element.tag == _MAIN + "si":
                strings.append("".join(t.text or "" for t in element.iter(_MAIN + "t")))
                element.clear()
    return strings


def _cell_value(cell, strings: Sequence[str]):
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(_MAIN + "t"))
    value = cell.find(_MAIN + "v")
    if value is None or value.text is None:
        return None
    if kind == "s":
        return strings[int(value.text)]
    if kind == "b":
        return value.text == "1"
    if kind in ("str", "e"):
        return value.text
    try:
        return float(value.text)
    except ValueError:
        return value.text


def read_columns(source: Source, sheet: str, columns: Sequence[str], *, header_scan: int = 10) -> pd.DataFrame:
    """As `columns` da aba `sheet`, na ordem pedida.

    O cabeçalho é a primeira linha (entre as `header_scan` primeiras) que tem
    todas as colunas pedidas; nome de aba e de coluna comparam sem acento e
    sem caixa. Falta de aba ou coluna levanta `ValueError` com o que falta.
    """
    wanted = [str(column) for column in columns]
    with zipfile.ZipFile(source) as archive:
        paths = _sheet_paths(archive)
        path = paths.get(sheet) or next((p for name, p in paths.items() if _fold(name) == _fold(sheet)), None)
        if path is None:
            raise ValueError("Aba {!r} não encontrada. Abas: {}.".format(sheet, ", ".join(paths)))
        strings = _shared_strings(archive)
        positions: Optional[Dict[int, str]] = None
        data: Dict[str, list] = {column: [] for column in wanted}
        scanned = 0
        with archive.open(path) as handle:
            sheet_data = None
            for event, element in ElementTree.iterparse(handle, events=("start", "end")):
                if event == "start":
                    if element.tag == _MAIN + "sheetData":
                        sheet_data = element
                    continue
                if element.tag != _MAIN + "row":
                    continue
                cells: Dict[int, object] = {}
                for position, cell in enumerate(element.iter(_MAIN + "c")):
                    reference = cell.get("r")
                    match = _LETTERS.match(reference) if reference else None
                    index = _column_index(match.group(1)) if match else position
                    if positions is None or index in positions:
                        cells[index] = _cell_value(cell, strings)
                # Linha lida sai da árvore: a aba inteira nunca fica na memória.
                element.clear()
                if sheet_data is not None:
                    sheet_data.clear()
                if positions is None:
                    scanned += 1
                    by_name = {_fold(value): index for index, value in cells.items() if value is not None}
                    found = {by_name[_fold(column)]: column for column in wanted if _fold(column) in by_name}
                    if len(found) == len(wanted):
                        positions = found
                    elif scanned >= header_scan:
                        missing = [c for c in wanted if _fold(c) not in by_name]
                        raise ValueError("Colunas não encontradas na aba {!r}: {}.".format(sheet, ", ".join(missing)))
                    continue
                if not cells:
                    continue
                for index, column in positions.items():
                    data[column].append(cells.get(index))
        if positions is None:
            raise ValueError("A aba {!r} não tem as colunas: {}.".format(sheet, ", ".join(wanted)))
    return pd.DataFrame(data, columns=wanted)
