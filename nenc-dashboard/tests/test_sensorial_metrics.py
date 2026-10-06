"""Estatistica e metricas do Teste Sensorial, com dados sinteticos."""

import math
import unittest

import pandas as pd

from tests.test_sensorial_model import BundleBase, _windows
from utils import sensorial_design, sensorial_metrics, sensorial_stats


class StatsTests(unittest.TestCase):
    def test_holm_matches_the_step_down_rule(self):
        self.assertEqual([round(p, 6) for p in sensorial_stats.holm([0.01, 0.04, math.nan, 0.03])][:2],
                         [0.03, 0.06])
        adjusted = sensorial_stats.holm([0.01, 0.04, math.nan, 0.03])
        self.assertTrue(math.isnan(adjusted[2]))
        self.assertAlmostEqual(adjusted[3], 0.06)

    def test_paired_wilcoxon_effect_and_the_descriptive_floor(self):
        from scipy.stats import wilcoxon

        a = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        b = [4.0, 4.5, 6.0, 6.0, 8.5, 7.0]
        result = sensorial_stats.paired_test(a, b)
        self.assertAlmostEqual(result["p"], wilcoxon(a, b).pvalue)
        self.assertEqual(result["r"], 1.0)  # todas as diferenças positivas
        self.assertEqual(sensorial_stats.paired_test(a[:4], b[:4])["metodo"], sensorial_stats.DESCRIPTIVE)
        self.assertEqual(sensorial_stats.verdict(0.01, 0.2), sensorial_stats.TREND)
        self.assertEqual(sensorial_stats.verdict(0.01, 0.03), sensorial_stats.DIFFERENCE)


class AssociationTests(unittest.TestCase):
    def test_score_bands_quadrants_and_pooling(self):
        settings = sensorial_design.resolve_settings({})
        trials = pd.DataFrame({
            "condicao": ["B1", "B1", "B2", "B2"], "palavra": ["Fresco"] * 4,
            "participant_code": ["P01", "P02", "P01", "P02"], "sim": [True, True, True, False],
            "cr": [0.5, 0.3, -0.4, 0.2]})
        table = sensorial_metrics.association_table(trials, settings).set_index("condicao")
        self.assertAlmostEqual(table.loc["B1", "score"], 0.4)
        self.assertEqual((table.loc["B1", "faixa"], table.loc["B1", "quadrante"]), ("Muito alta", "Dominante"))
        self.assertEqual((table.loc["B2", "faixa"], table.loc["B2", "quadrante"]), ("Muito baixa", "Potencial"))
        pooled = sensorial_metrics.association_table(trials, settings, {"B": ["B1", "B2"]})
        self.assertEqual(pooled["condicao"].tolist(), ["B"])
        self.assertEqual(pooled["tentativas"].tolist(), [4])


class ComputeAllTests(BundleBase):
    def test_compute_all_pairs_participants_and_filters_by_profile(self):
        sessions = [("s{}".format(i), "P{:02d}".format(i), "2001A", "A") for i in range(1, 7)] + \
                   [("b{}".format(i), "P{:02d}".format(i), "2001Basal", None) for i in range(1, 7)]
        self._file("eeg_psd", _windows(sessions, per_stage=3))
        participants = [{"code": "P{:02d}".format(i), "profile": {"sexo": "F" if i <= 3 else "M"}, "notes": None}
                        for i in range(1, 7)]
        model = self._model(participants=participants)
        result = sensorial_metrics.compute_all(model)
        table = result["eeg"]["comparacoes"]
        fai = table[(table["medida"] == "FAI") & (table["tipo"] == "vs_basal") & (table["etapa"] == "Olfacao")]
        self.assertEqual(fai["n"].tolist(), [6])
        self.assertEqual(fai["metodo"].tolist(), ["wilcoxon"])
        counts = result["n_por_condicao"].set_index("condicao")
        self.assertEqual(counts.loc["A", "eeg"], 6)
        filtered = sensorial_metrics.compute_all(model, {"perfil": {"sexo": ["F"]}, "comparar_por": "sexo"})
        self.assertEqual(filtered["participantes"], 3)
        narrow = filtered["eeg"]["comparacoes"]
        self.assertTrue((narrow["metodo"] == sensorial_stats.DESCRIPTIVE).all())  # n < 5
        self.assertTrue(any("Recorte por perfil" in note for note in filtered["limitacoes"]))
        curve = sensorial_metrics.curves(model, "FAI", "olfacao")
        self.assertFalse(curve["curva"].empty)
        self.assertIsNotNone(curve["referencia"])


if __name__ == "__main__":
    unittest.main()
