import unittest

from utils.jornada_taxonomy import (
    aoi_key,
    build_catalog,
    element_label,
    format_dimensions,
    parse_aoi,
    parse_dimensions,
    suggest_brands,
)

BRANDS = ["Marca A", "Marca Sempre", "Outra"]
DIMENSIONS = {"tipo": ["Diurno", "Noturno"], "cobertura": ["Seco", "Suave", "Toque Macio"]}


def _parse(name):
    return parse_aoi(name, brands=BRANDS, dimensions=DIMENSIONS)


class ParseAoiTests(unittest.TestCase):
    def test_product_part_and_attributes(self):
        result = _parse("Marca A Noturno Seco pt 3")
        self.assertEqual(result["kind"], "produto")
        self.assertEqual(result["brand"], "Marca A")
        self.assertEqual(result["product"], "Marca A Noturno Seco")
        self.assertEqual(result["part"], "3")
        self.assertEqual(result["attrs"], {"tipo": "Noturno", "cobertura": "Seco"})
        self.assertEqual(_parse("Marca Sempre Noturno p1")["part"], "1")
        self.assertEqual(_parse("Marca Sempre Noturno Suave pt 1.1")["part"], "1.1")
        self.assertEqual(_parse("Outra Noturno pt1")["part"], "1")

    def test_price_is_a_whole_word(self):
        # "Sempre" contem "pre": so a palavra inteira marca preco.
        self.assertEqual(_parse("Marca Sempre Diurno pt 1")["kind"], "produto")
        price = _parse("Marca Sempre Diurno Preço")
        self.assertEqual(price["kind"], "preco")
        self.assertEqual(price["product"], "Marca Sempre Diurno")
        second = _parse("Marca A Noturno Suave Preco b")
        self.assertEqual((second["kind"], second["product"]), ("preco", "Marca A Noturno Suave"))

    def test_longest_brand_and_line_without_attributes(self):
        result = _parse("Marca Sempre Toda Protegida noturno")
        self.assertEqual(result["brand"], "Marca Sempre")
        self.assertEqual(result["line"], "Toda Protegida")
        self.assertEqual(result["attrs"], {"tipo": "Noturno"})

    def test_multi_word_attribute_values(self):
        result = _parse("Marca A Toque Macio Diurno")
        self.assertEqual(result["attrs"], {"tipo": "Diurno", "cobertura": "Toque Macio"})
        self.assertEqual(result["line"], "")

    def test_packaging_elements_and_aliases(self):
        result = _parse("Marca Sempre_OUTRAS INF")
        self.assertEqual(
            (result["kind"], result["brand"], result["element"]),
            ("embalagem", "Marca Sempre", "OUTRAS INFO"),
        )
        self.assertEqual(element_label("FIG"), "Figura")
        self.assertEqual(element_label("TAM ABS", {"TAM ABS": "Tamanho do item"}), "Tamanho do item")

    def test_outside_and_unknown(self):
        self.assertEqual(_parse("")["kind"], "fora")
        self.assertEqual(_parse("    ")["kind"], "fora")
        unknown = parse_aoi("Gondola Topo", brands=BRANDS, dimensions=DIMENSIONS)
        self.assertEqual((unknown["kind"], unknown["brand"]), ("produto", "Gondola"))


class CatalogTests(unittest.TestCase):
    def test_brand_suggestions_respect_multi_word_packaging_prefixes(self):
        names = ["Marca Sempre_FIG", "Marca Sempre Diurno", "Nova Noturno p1", "Nova Diurno", ""]
        self.assertEqual(suggest_brands(names), ["Marca Sempre", "Nova"])
        self.assertEqual(suggest_brands(names, known=["Nova"])[0], "Nova")

    def test_catalog_applies_manual_overrides_and_focus(self):
        catalog = build_catalog(
            [("1234", "Marca A Noturno p1"), ("1234", "Marca A Noturno p1"), ("1234", "Cesta")],
            brands=BRANDS,
            dimensions=DIMENSIONS,
            overrides=[{"store": "1234", "aoi": "Cesta", "kind": "outro", "include": False,
                        "attrs": {"tipo": "Diurno"}}],
            focus_brand="marca a",
        )
        self.assertEqual(len(catalog), 2)
        product = catalog[catalog["aoi"] == "Marca A Noturno p1"].iloc[0]
        self.assertTrue(product["is_focus"])
        self.assertEqual(product["attr_tipo"], "Noturno")
        self.assertEqual(product["aoi_key"], aoi_key("1234", "Marca A Noturno p1"))
        basket = catalog[catalog["aoi"] == "Cesta"].iloc[0]
        self.assertEqual((basket["kind"], basket["include"], basket["source"]), ("outro", False, "manual"))
        self.assertEqual(basket["attr_tipo"], "Diurno")

    def test_dimensions_round_trip(self):
        text = "tipo: Diurno, Noturno\nCobertura: Seco,  Suave\nlinha sem dois pontos"
        dimensions = parse_dimensions(text)
        self.assertEqual(dimensions, {"tipo": ["Diurno", "Noturno"], "cobertura": ["Seco", "Suave"]})
        self.assertEqual(parse_dimensions(format_dimensions(dimensions)), dimensions)


if __name__ == "__main__":
    unittest.main()
