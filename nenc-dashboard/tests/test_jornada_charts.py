"""Gráficos da Jornada: cor por entidade e figuras que não quebram sem dado."""

import math
import unittest

import pandas as pd

from utils import jornada_charts as charts


def _brand_rows():
    rows = []
    for cell, n in (("Jornada Livre · Loja 1", 5), ("Jornada Livre · Loja 2", 3)):
        for brand, share in (("Foco", 0.5), ("Outra", 0.3), ("Terceira", 0.2)):
            rows.append({
                "cell": cell, "brand": brand, "n": n, "n_pos": n, "share_mean": share,
                "reach": 0.8, "examined": 0.6, "revisit": 0.4, "first_noticed": share,
                "presence": 1 / 3, "presence_index": share * 3, "is_focus": brand == "Foco",
                "task": "livre", "store": cell[-1],
            })
    return pd.DataFrame(rows)


class ColorTests(unittest.TestCase):
    def test_the_focus_brand_always_gets_the_first_slot(self):
        colors = charts.brand_color_map(["Outra", "Foco", "Terceira"], "foco")
        self.assertEqual(list(colors), ["Foco", "Outra", "Terceira"])
        self.assertEqual(colors["Foco"], charts.FOCUS_COLOR)

    def test_color_follows_the_brand_not_the_filter(self):
        full = charts.brand_color_map(["A", "B", "C", "D"], "")
        # Uma tela filtrada monta o mapa com a lista inteira e so mostra parte.
        shown = {brand: full[brand] for brand in ("B", "D")}
        self.assertEqual(shown["D"], charts.BRAND_SEQUENCE[3])

    def test_past_eight_brands_fold_into_gray(self):
        colors = charts.brand_color_map(["M{}".format(i) for i in range(10)])
        self.assertEqual(len(set(list(colors.values())[:8])), 8)
        self.assertEqual(colors["M8"], charts.OTHER_COLOR)
        self.assertEqual(colors["M9"], charts.OTHER_COLOR)

    def test_labels_inside_fills_pick_readable_ink(self):
        self.assertEqual(charts._label_color("#d2cefd"), "#0b0b0b")
        self.assertEqual(charts._label_color("#5d5294"), "#ffffff")


class FigureTests(unittest.TestCase):
    def test_every_chart_builds_with_data(self):
        brand = _brand_rows()
        colors = charts.brand_color_map(["Foco", "Outra", "Terceira"], "Foco")
        order = list(colors)
        per = pd.DataFrame({
            "brand": ["Foco", "Outra"], "looked": [True, True], "rel_ttff_s": [0.0, 2.5],
            "ttff_s": [3.0, 5.5], "participant": ["Pt01", "Pt01"],
        })
        figures = [
            charts.share_stacked(brand, colors),
            charts.store_brand_heatmap(brand),
            charts.funnel_bars(brand[brand["cell"].str.endswith("1")], order),
            charts.emphasis_bars(brand.assign(label=brand["brand"]), "label", "first_noticed"),
            charts.ttff_strip(per, order, "Foco"),
            charts.presence_index_chart(brand[brand["cell"].str.endswith("1")], order),
        ]
        for figure in figures:
            self.assertTrue(figure.data)
        stacked = figures[0]
        self.assertEqual([trace.name for trace in stacked.data], order)
        self.assertEqual(stacked.data[0].marker.color, charts.FOCUS_COLOR)
        # Rotulo so dentro de segmento que cabe (>= 12%).
        self.assertEqual(stacked.data[0].text[0], "50%")

    def test_empty_inputs_give_a_message_not_an_exception(self):
        empty = pd.DataFrame(columns=["cell", "brand", "share_mean", "presence_index", "looked",
                                      "rel_ttff_s", "ttff_s", "participant", "dimension",
                                      "tempo_decisao_s", "element_label"])
        for figure in (
            charts.share_stacked(empty, {}),
            charts.presence_index_chart(empty, []),
            charts.ttff_strip(empty, [], ""),
            charts.attribute_stacked(empty, "tipo", []),
            charts.packaging_heatmap(empty),
        ):
            self.assertFalse(figure.data)
            self.assertTrue(figure.layout.annotations)

    def test_presence_bars_diverge_around_one(self):
        brand = _brand_rows()
        figure = charts.presence_index_chart(brand[brand["cell"].str.endswith("1")], ["Foco", "Outra", "Terceira"])
        bar = figure.data[0]
        self.assertEqual(bar.base, 1)
        colors = list(bar.marker.color)
        self.assertEqual(colors[0], charts.ABOVE_COLOR)
        self.assertEqual(colors[2], charts.BELOW_COLOR)
        self.assertTrue(math.isclose(bar.x[0], 0.5))


if __name__ == "__main__":
    unittest.main()
