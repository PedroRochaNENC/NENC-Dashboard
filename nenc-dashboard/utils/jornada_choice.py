"""
Escolha e consideração a partir do registro de campo da Jornada de Compra.

A equipe de campo anota em texto livre o produto escolhido ("Alfa diurno e
noturno", "Beta Toda Protegida") e as marcas consideradas ("Beta, Alfa, Gama
Livre (comparou os preços dos noturnos e levou o pacote com 32 da Alfa)").
Aqui esses textos viram marcas, variantes, linhas e tamanhos de
embalagem, usando as marcas e os atributos do próprio projeto — nada de lista
fixa de cliente. Funções puras, sem Streamlit.
"""

import difflib
import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils.jornada_ingest import to_number
from utils.jornada_taxonomy import fold

_WORD = re.compile(r"[a-z0-9]+")
# "pct com 32", "pct de 32 e 48 und", "pct c/16", "8 und", "32 abs".
_PACK_AFTER = re.compile(r"(?:pct|pcts|pacote|pacotes)\s*(?:de|com|c/|c)?\s*(\d{1,3})\b")
_PACK_BEFORE = re.compile(r"\b(\d{1,3})\s*(?:und|unid|unidade|unidades|un|abs)\b")


def _words(text: str) -> List[str]:
    return _WORD.findall(fold(text))


def _phrase(text: str) -> str:
    return " " + " ".join(_words(text)) + " "


def find_brands(text: str, brands: Iterable[str]) -> List[str]:
    """Marcas citadas, na ordem em que aparecem; a mais longa vence ("Gama Livre" antes de "Livre")."""
    phrase = _phrase(text)
    taken: List[Tuple[int, int]] = []
    found: List[Tuple[int, str]] = []
    for brand in sorted({b for b in brands if str(b).strip()}, key=lambda b: -len(_phrase(b))):
        key = _phrase(brand)
        if key.strip() == "":
            continue
        start = phrase.find(key)
        while start >= 0:
            # O trecho da marca sem os espaços das pontas: palavras vizinhas
            # dividem o espaço entre elas, e isso não é sobreposição.
            word_start, word_end = start + 1, start + len(key) - 1
            if not any(word_start < t_end and t_start < word_end for t_start, t_end in taken):
                taken.append((word_start, word_end))
                found.append((word_start, brand))
            start = phrase.find(key, start + 1)
    ordered: List[str] = []
    for _, brand in sorted(found):
        if brand not in ordered:
            ordered.append(brand)
    return ordered


def find_values(text: str, dimensions: Dict[str, Sequence[str]]) -> Dict[str, List[str]]:
    """Valores de atributo citados ("diurno e noturno" -> tipo: Diurno, Noturno).

    Aceita erro de digitação de campo ("noturo", "diruno") por semelhança, só
    em palavras de 4 letras ou mais.
    """
    words = _words(text)
    result: Dict[str, List[str]] = {}
    for dimension, values in (dimensions or {}).items():
        lookup = {fold(value): value for value in values if str(value).strip()}
        found: List[str] = []
        for word in words:
            match = lookup.get(word)
            if match is None and len(word) >= 4:
                close = difflib.get_close_matches(word, list(lookup), n=1, cutoff=0.8)
                match = lookup[close[0]] if close else None
            if match is not None and match not in found:
                found.append(match)
        if found:
            result[dimension] = found
    return result


def find_lines(text: str, lines: Iterable[str]) -> List[str]:
    """Linhas de produto citadas ("Toda Protegida"), frase inteira."""
    phrase = _phrase(text)
    return [line for line in sorted({l for l in lines if str(l).strip()}, key=lambda l: -len(l))
            if _phrase(line) in phrase]


def find_packs(text: str) -> List[int]:
    """Tamanhos de embalagem citados, em unidades, na ordem do texto."""
    folded = fold(text)
    hits: List[Tuple[int, int]] = []
    for pattern in (_PACK_AFTER, _PACK_BEFORE):
        for match in pattern.finditer(folded):
            hits.append((match.start(1), int(match.group(1))))
    ordered: List[int] = []
    for _, size in sorted(hits):
        if 0 < size <= 200 and size not in ordered:
            ordered.append(size)
    return ordered


def parse_choice(
    text: str,
    brands: Iterable[str],
    dimensions: Optional[Dict[str, Sequence[str]]] = None,
    lines: Iterable[str] = (),
    fallback: str = "",
) -> Dict[str, object]:
    """Produto escolhido: marcas, variantes e linhas.

    `fallback` é outro registro do mesmo participante (a aba Controle quando a
    escolha vem da aba Estimuladas): se ele cita as mesmas marcas e traz a
    variante que o principal omitiu ("Beta" × "Beta Diurno"), a variante
    vem dele.
    """
    brands = list(brands)
    chosen = find_brands(text, brands)
    values = find_values(text, dimensions or {})
    found_lines = find_lines(text, lines)
    if fallback and chosen:
        other = find_brands(fallback, brands)
        if set(other) == set(chosen):
            if not values:
                values = find_values(fallback, dimensions or {})
            if not found_lines:
                found_lines = find_lines(fallback, lines)
    return {"brands": chosen, "values": values, "lines": found_lines}


def parse_considered(
    text: str, brands: Iterable[str], dimensions: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, object]:
    """Marcas consideradas durante a compra, embalagens e variantes citadas."""
    return {
        "brands": find_brands(text, brands),
        "packs": find_packs(text),
        "values": find_values(text, dimensions or {}),
    }


def parse_fraction(value: object) -> Tuple[float, str]:
    """Fração anotada à mão ("0.0737", "13, 33%"); texto que não é número vira nota."""
    text = str(value if value is not None else "").strip()
    if not text or text.lower() == "nan":
        return math.nan, ""
    number = to_number(text.replace(" ", ""))
    if number != number:
        return math.nan, text
    if "%" not in text and number > 1:
        number = number / 100.0
    return float(number), ""


def describe_values(values: Dict[str, Sequence[str]]) -> str:
    """"tipo: Diurno, Noturno; cobertura: Suave" para tabelas e prompt."""
    return "; ".join("{}: {}".format(dimension, ", ".join(found)) for dimension, found in values.items())
