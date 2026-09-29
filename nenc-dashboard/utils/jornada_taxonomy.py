"""
Taxonomia das AOIs da Jornada de Compra.

O nome de cada AOI carrega o que a analise precisa — marca, linha, atributos,
se e etiqueta de preco, qual parte do produto na gondola — escrito a mao no
software de codificacao:

    "Always Noturno Seco pt 3"         produto, parte 3
    "Sempre Livre Diurno Preço"        preco do produto
    "Intimus_OUTRAS INF"               elemento de embalagem
    ""                                 tempo fora de qualquer AOI

Este modulo le esses nomes sem saber nada do cliente: as marcas e os
atributos (ex.: tipo = Diurno/Noturno) vem da configuracao do projeto, e o
que a leitura errar e corrigido pelo catalogo na tela Dados do Projeto.
"""

import re
import unicodedata
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

KIND_LABELS = {
    "produto": "Produto",
    "preco": "Preço",
    "embalagem": "Elemento de embalagem",
    "fora": "Fora das AOIs",
    "outro": "Outro",
}

# Rotulos dos codigos de elemento mais comuns em teste de embalagem. O projeto
# pode acrescentar ou trocar (settings["element_labels"]).
DEFAULT_ELEMENT_LABELS = {
    "MARCA": "Marca / logo",
    "FIG": "Figura",
    "PROMO": "Promoção",
    "TAM": "Tamanho / quantidade",
    "TIPO": "Tipo",
    "NOME PRODUTO": "Nome do produto",
    "NOVO": "Selo de novidade",
    "OUTRAS INFO": "Outras informações",
    "COBERT": "Cobertura",
}
# Grafias diferentes do mesmo codigo, vistas nos dados reais.
ELEMENT_ALIASES = {
    "OUTRAS INF": "OUTRAS INFO",
    "OUTRAS INFORMACOES": "OUTRAS INFO",
    "LOGO": "MARCA",
}

# "Preço" com e sem acento. Palavra inteira: "Sempre" contem "pre" e seria
# lido como preco por uma busca de substring.
_PRICE = re.compile(r"\bpre[cç]o\b", re.IGNORECASE)
# Sufixo de parte: "p1", "pt 1", "pt1", "pt 1.1" — sempre no fim do nome.
_PART = re.compile(r"\s+(?:pt|p)\s*(\d+(?:\.\d+)?)$", re.IGNORECASE)
# Letra solta depois de "Preço" ("Preço b"): segunda etiqueta do mesmo produto.
_TRAILING_LETTER = re.compile(r"\s+[a-z]$", re.IGNORECASE)


def fold(text: object) -> str:
    """Minusculo, sem acento e com espacos normalizados — para comparar."""

    value = normalize_label(text)
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def normalize_label(text: object) -> str:
    """Texto limpo para exibir: sem espaco duro, sem espaco repetido nas pontas."""

    if text is None:
        return ""
    if isinstance(text, float) and text != text:  # NaN
        return ""
    value = str(text).replace(" ", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_dimensions(text: str) -> Dict[str, List[str]]:
    """Le "tipo: Diurno, Noturno" (uma dimensao por linha) em dict."""

    dimensions: Dict[str, List[str]] = {}
    for line in str(text or "").splitlines():
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        name = fold(name).replace(" ", "_")
        items = [normalize_label(v) for v in values.split(",") if normalize_label(v)]
        if name and items:
            dimensions[name] = items
    return dimensions


def format_dimensions(dimensions: Dict[str, Sequence[str]]) -> str:
    return "\n".join(
        "{}: {}".format(name, ", ".join(values)) for name, values in (dimensions or {}).items()
    )


def _match_brand(name: str, brands: Sequence[str]) -> Optional[str]:
    """Marca conhecida mais longa que abre o nome (sem diferenciar acento/caixa)."""

    folded = fold(name)
    best = None
    for brand in brands:
        brand_folded = fold(brand)
        if not brand_folded:
            continue
        if folded == brand_folded or folded.startswith(brand_folded + " ") or folded.startswith(
            brand_folded + "_"
        ):
            if best is None or len(brand_folded) > len(fold(best)):
                best = brand
    return best


def _strip_prefix(name: str, prefix: str) -> str:
    """Tira as palavras de `prefix` do comeco de `name` (a marca ja casou)."""

    if not prefix:
        return normalize_label(name)
    tokens = normalize_label(name).split(" ")
    return " ".join(tokens[len(normalize_label(prefix).split(" ")):])


def _extract_dimensions(
    text: str, dimensions: Dict[str, Sequence[str]]
) -> Tuple[Dict[str, str], str]:
    """Acha os valores de atributo no texto e devolve (atributos, resto).

    Compara por palavras dobradas (sem acento e sem caixa): um valor de
    atributo so casa com palavras inteiras, e pode ter mais de uma palavra.
    """

    tokens = normalize_label(text).split(" ") if normalize_label(text) else []
    folded = [fold(token) for token in tokens]
    attrs: Dict[str, str] = {}
    for name, values in (dimensions or {}).items():
        for value in values:
            wanted = [fold(token) for token in normalize_label(value).split(" ") if token]
            size = len(wanted)
            if not size:
                continue
            for start in range(len(folded) - size + 1):
                if folded[start:start + size] == wanted:
                    attrs[name] = value
                    del tokens[start:start + size]
                    del folded[start:start + size]
                    break
            if name in attrs:
                break
    return attrs, " ".join(tokens)


def parse_aoi(
    name: object,
    *,
    brands: Sequence[str] = (),
    dimensions: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, object]:
    """Le o nome de uma AOI. Nunca falha: o que nao reconhece vira `outro`."""

    raw = normalize_label(name)
    result: Dict[str, object] = {
        "aoi": raw,
        "kind": "outro",
        "brand": "",
        "line": "",
        "product": "",
        "part": "",
        "element": "",
        "attrs": {},
    }
    if not raw:
        result["kind"] = "fora"
        return result

    if "_" in raw:
        brand_part, element = raw.split("_", 1)
        brand = _match_brand(brand_part, brands) or normalize_label(brand_part)
        element_code = normalize_label(element).upper()
        element_code = ELEMENT_ALIASES.get(fold(element_code).upper(), element_code)
        result.update(
            kind="embalagem", brand=brand, element=element_code, product=brand
        )
        return result

    text = raw
    is_price = bool(_PRICE.search(text))
    if is_price:
        text = normalize_label(_PRICE.sub(" ", text))
        text = normalize_label(_TRAILING_LETTER.sub("", text))

    part = ""
    part_match = _PART.search(text)
    if part_match:
        part = part_match.group(1)
        text = normalize_label(text[: part_match.start()])

    brand = _match_brand(text, brands)
    if brand is None:
        tokens = text.split(" ")
        brand = tokens[0] if tokens else ""
        body = " ".join(tokens[1:])
    else:
        body = _strip_prefix(text, brand)

    attrs, line = _extract_dimensions(body, dimensions or {})
    product = normalize_label(" ".join(filter(None, [brand, body])))
    result.update(
        kind="preco" if is_price else "produto",
        brand=brand,
        line=line,
        product=product,
        part=part,
        attrs=attrs,
    )
    return result


def suggest_brands(aois: Iterable[object], known: Sequence[str] = ()) -> List[str]:
    """Marcas provaveis nos nomes das AOIs, as conhecidas primeiro.

    Os prefixos de embalagem ("Sempre Livre_FIG") dizem onde a marca termina
    mesmo quando ela tem duas palavras; o que sobra usa a primeira palavra.
    """

    names = [normalize_label(aoi) for aoi in aois if normalize_label(aoi)]
    candidates: List[str] = list(dict.fromkeys(normalize_label(b) for b in known if normalize_label(b)))
    for name in names:
        if "_" in name:
            prefix = normalize_label(name.split("_", 1)[0])
            if prefix and not _match_brand(prefix, candidates):
                candidates.append(prefix)

    counts: Counter = Counter()
    for name in names:
        if "_" in name:
            continue
        text = normalize_label(_PRICE.sub(" ", name))
        if _match_brand(text, candidates):
            continue
        first = text.split(" ")[0] if text else ""
        if first and not first.isdigit():
            counts[first] += 1
    for token, _ in counts.most_common():
        if not _match_brand(token, candidates):
            candidates.append(token)
    return candidates


def aoi_key(store: object, aoi: object) -> str:
    return "{}|{}".format(normalize_label(store), normalize_label(aoi))


def build_catalog(
    keys: Iterable[Tuple[object, object]],
    *,
    brands: Sequence[str] = (),
    dimensions: Optional[Dict[str, Sequence[str]]] = None,
    overrides: Iterable[Dict[str, object]] = (),
    focus_brand: str = "",
) -> pd.DataFrame:
    """Catalogo completo: leitura automatica de cada (loja, AOI) mais os ajustes.

    Colunas: store, aoi, aoi_key, kind, brand, line, product, part, element,
    uma `attr_<dimensao>` por dimensao configurada, shelf_weight, include,
    is_focus e source ("auto" ou "manual").
    """

    dimensions = dimensions or {}
    by_key = {}
    for item in overrides or ():
        by_key[(normalize_label(item.get("store")), normalize_label(item.get("aoi")))] = item

    rows = []
    for store, aoi in dict.fromkeys(
        (normalize_label(store), normalize_label(aoi)) for store, aoi in keys
    ):
        parsed = parse_aoi(aoi, brands=brands, dimensions=dimensions)
        row = {
            "store": store,
            "aoi": aoi,
            "aoi_key": aoi_key(store, aoi),
            "kind": parsed["kind"],
            "brand": parsed["brand"],
            "line": parsed["line"],
            "product": parsed["product"],
            "part": parsed["part"],
            "element": parsed["element"],
            "shelf_weight": None,
            "include": parsed["kind"] != "fora",
            "source": "auto",
        }
        attrs = dict(parsed["attrs"])
        manual = by_key.get((store, aoi))
        if manual:
            for field in ("kind", "brand", "line", "product", "part", "element"):
                value = manual.get(field)
                if value not in (None, ""):
                    row[field] = normalize_label(value) if field != "kind" else value
            if manual.get("shelf_weight") not in (None, ""):
                try:
                    row["shelf_weight"] = float(manual["shelf_weight"])
                except (TypeError, ValueError):
                    pass
            if manual.get("include") is not None:
                row["include"] = bool(manual["include"])
            attrs.update(
                {k: normalize_label(v) for k, v in (manual.get("attrs") or {}).items() if v}
            )
            row["source"] = "manual"
        for name in dimensions:
            row["attr_{}".format(name)] = attrs.get(name, "")
        row["is_focus"] = bool(focus_brand) and fold(row["brand"]) == fold(focus_brand)
        rows.append(row)

    columns = [
        "store", "aoi", "aoi_key", "kind", "brand", "line", "product", "part",
        "element",
    ] + ["attr_{}".format(name) for name in dimensions] + [
        "shelf_weight", "include", "is_focus", "source",
    ]
    return pd.DataFrame(rows, columns=columns)


def element_label(code: str, labels: Optional[Dict[str, str]] = None) -> str:
    merged = dict(DEFAULT_ELEMENT_LABELS)
    merged.update({str(k).upper(): v for k, v in (labels or {}).items()})
    return merged.get(str(code or "").upper(), str(code or ""))
