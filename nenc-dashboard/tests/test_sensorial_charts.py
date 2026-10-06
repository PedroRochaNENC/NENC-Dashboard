"""Gráficos do Teste Sensorial: cores fixas por papel, figuras com e sem dados."""

import pandas as pd

from tests.test_sensorial_model import BundleBase, _windows
from utils import sensorial_charts as charts
from utils import sensorial_metrics
from utils.chart_kit import BRAND_SEQUENCE, DEEMPHASIS, OTHER_COLOR

SESSIONS = ([("a{}".format(i), "P{:02d}".format(i), "2001A", "A") for i in range(1, 7)]
            + [("n{}".format(i), "P{:02d}".format(i), "2001Neutro", "N") for i in range(1, 7)]
            + [("b{}".format(i), "P{:02d}".format(i), "2001Basal", None) for i in range(1, 7)])


class ChartTests(BundleBase):
    def setUp(self):
        super().setUp()
        psd = _windows(SESSIONS, stages=("Basal", "Olfacao", "PosOlfacao"), per_stage=8)
        psd["primeira_olfacao_s"] = 0.5
        self._file("eeg_psd", psd)
        self.model = self._model()
        self.metrics = sensorial_metrics.compute_all(self.model)
        self.design = self.model["design"]

    def test_colors_follow_the_role_of_the_condition(self):
        colors = charts.condition_colors(self.design)
        self.assertEqual((colors["Basal"], colors["N"], colors["A"]), (DEEMPHASIS, OTHER_COLOR, BRAND_SEQUENCE[0]))

    def test_every_chart_builds_with_and_without_data(self):
        summary = self.metrics["eeg"]["resumo"]
        bars = charts.condition_stage_bars(summary, "FAI", self.design, "Valência")
        self.assertEqual(len(bars.data), 3)  # Basal, controle e amostra
        self.assertTrue(any(shape["type"] == "line" for shape in bars.layout.shapes))  # linha do basal
        table = self.metrics["eeg"]["comparacoes"]
        fai = table[table["medida"] == "FAI"]
        self.assertGreater(len(charts.comparison_chart(fai, {}, {}).data), 0)
        curve = sensorial_metrics.curves(self.model, "FAI", "olfacao")
        figure = charts.curve_chart(curve["curva"], curve["referencia"], self.design, "Valência", "olfacao")
        self.assertGreater(len(figure.data), 0)
        frame = self.model["eeg"][self.model["eeg"]["sessao_id"] == "a1"].assign(t=lambda f: f["Tempo"])
        self.assertEqual(len(charts.timeline_chart(frame, ["FAI"], {}, {}).data), 1)
        association = pd.DataFrame({"condicao": ["A", "A"], "palavra": ["Fresco", "Leve"], "pct_sim": [0.8, 0.3],
                                    "cr_sim": [0.4, -0.2], "score": [0.32, -0.06], "faixa": ["Muito alta", "Baixa"],
                                    "quadrante": ["Dominante", "Sem aderência"]})
        settings = self.model["settings"]["associacao"]
        self.assertEqual(len(charts.score_bars(association, self.design, settings["faixas"]).data), 1)
        self.assertEqual(len(charts.quadrant_chart(association, self.design, settings["quadrantes"]).data), 1)
        for empty in (charts.condition_stage_bars(summary.iloc[0:0], "FAI", self.design, ""),
                      charts.comparison_chart(fai.iloc[0:0], {}, {}),
                      charts.curve_chart(curve["curva"].iloc[0:0], None, self.design, "", "etapa"),
                      charts.score_bars(association.iloc[0:0], self.design, settings["faixas"])):
            self.assertEqual(len(empty.data), 0)

    def test_the_results_matrix_marks_direction_and_result(self):
        table = pd.DataFrame({"medida": ["FAI", "FAI", "PPI"], "condicao_a": ["A", "A", "A"],
                              "condicao_b": ["N", "Basal", "N"], "etapa": ["Olfacao"] * 3,
                              "diferenca_mediana": [0.2, -0.1, 0.05],
                              "resultado": ["diferença", "tendência", "sem diferença"]})
        matrix = charts.results_matrix(table, {"FAI": "Valência"}, {}, {}, ["FAI", "PPI"])
        self.assertEqual(matrix["medida"].tolist(), ["Valência", "PPI"])
        self.assertEqual(matrix.loc[0, "A × N · Olfacao"], "▲")
        self.assertEqual(matrix.loc[0, "A × Basal · Olfacao"], "▽")
        self.assertEqual(matrix.loc[1, "A × N · Olfacao"], "·")

    def test_curve_points_with_few_participants_are_hidden(self):
        everyone = sensorial_metrics.curves(self.model, "FAI", "etapa")["curva"]
        self.assertTrue((everyone["n"] >= sensorial_metrics.MIN_CURVE_PARTICIPANTS).all())
        strict = sensorial_metrics.curves(self.model, "FAI", "etapa", min_participants=7)["curva"]
        self.assertTrue(strict.empty)  # só 6 participantes por condição


if __name__ == "__main__":
    import unittest

    unittest.main()
