"""Painel de efeitos do Teste Sensorial, com projetos sinteticos."""

import math
import unittest

import numpy as np
import pandas as pd

from tests.test_sensorial_model import BundleBase, _windows
from utils import sensorial_design, sensorial_effects, sensorial_metrics, sensorial_stats

PARTICIPANTS = ["P{:02d}".format(i) for i in range(1, 9)]
NO_OUTLIER_RULE = {"limpeza": {"regra_spss": False}}


def _sessions(samples=("A", "B"), control=True):
    rows = [("basal{}".format(i), code, "2001Basal", None) for i, code in enumerate(PARTICIPANTS)]
    if control:
        rows += [("n{}".format(i), code, "2001N", "N") for i, code in enumerate(PARTICIPANTS)]
    for sample in samples:
        rows += [("s{}{}".format(sample, i), code, "2001" + sample, sample) for i, code in
                 enumerate(PARTICIPANTS)]
    return rows


def _boost_fai(psd, sample, factor):
    """Sobe a assimetria frontal da amostra: F4 alfa multiplicado, nas etapas de exposição."""
    psd = psd.copy()
    rows = (psd["amostra"] == sample) & psd["Etapa"].isin(["Olfacao", "PosOlfacao"])
    psd.loc[rows, "F4_Alpha"] *= factor
    return psd


class ProjectEffectsTests(BundleBase):
    def _project(self, settings=None, samples=("A", "B"), control=True, factor=3.0):
        psd = _windows(_sessions(samples, control), per_stage=8, seed=11)
        if "A" in samples:
            psd = _boost_fai(psd, "A", factor)
        self._file("eeg_psd", psd)
        return self._model(settings=NO_OUTLIER_RULE if settings is None else settings)

    def test_only_sample_against_control_in_the_exposure_stages_and_catalog_indices(self):
        model = self._project()
        effects = sensorial_effects.project_effects(model)
        self.assertEqual(effects["motivo"], "")
        table = effects["efeitos"]
        self.assertEqual(set(table["condicao"]), {"A", "B"})
        self.assertEqual(set(table["controle"]), {"Neutro"})
        self.assertEqual(set(table["etapa"]), {"Olfacao", "PosOlfacao"})
        self.assertEqual(set(table["etapa_rotulo"]), {"Olfação", "Pós-olfação"})
        self.assertTrue(set(table["medida"]) <= set(sensorial_design.INDEX_CODES))
        self.assertIn("CompA_Energia", set(table["medida"]))  # os 12 índices, não uma lista fixa
        self.assertEqual(len(table), 2 * 2 * len(model["indices"]))

    def test_dz_is_the_mean_difference_over_the_sd_of_differences(self):
        model = self._project()
        table = sensorial_effects.project_effects(model)["efeitos"]
        row = table[(table["condicao"] == "A") & (table["etapa"] == "Olfacao") & (table["medida"] == "FAI")].iloc[0]

        means = sensorial_metrics.eeg_means(model)
        fai = means[(means["medida"] == "FAI") & (means["etapa"] == "Olfacao")]
        pivot = fai.pivot_table(index="participant_code", columns="condicao", values="valor")
        differences = (pivot["A"] - pivot["N"]).dropna()
        deviation = differences.std(ddof=1)
        self.assertEqual(row["n"], len(differences))
        self.assertAlmostEqual(row["diferenca_media"], differences.mean())
        self.assertAlmostEqual(row["dp_diferenca"], deviation)
        self.assertAlmostEqual(row["dz"], differences.mean() / deviation)
        self.assertAlmostEqual(row["dz_inf"], row["ic_inf"] / deviation)
        self.assertAlmostEqual(row["dz_sup"], row["ic_sup"] / deviation)
        # O efeito injetado aparece: log10(3) a mais de assimetria, dentro do IC, com diferença pelo critério.
        self.assertLess(row["ic_inf"], math.log10(3.0))
        self.assertGreater(row["ic_sup"], math.log10(3.0))
        self.assertEqual(row["resultado"], sensorial_stats.DIFFERENCE)
        self.assertGreater(row["dz_inf"], 0)

    def test_paired_sd_inverts_the_95_ci(self):
        a = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        b = [4.0, 4.5, 6.0, 6.0, 8.5, 7.0]
        result = sensorial_stats.paired_test(a, b)
        expected = np.std(np.subtract(a, b), ddof=1)
        self.assertAlmostEqual(sensorial_effects.paired_sd(result["diferenca_media"], result["ic_sup"], 6), expected)
        self.assertTrue(math.isnan(sensorial_effects.paired_sd(1.0, math.nan, 1)))

    def test_projects_without_control_samples_or_psd_say_why(self):
        self.assertEqual(sensorial_effects.project_effects(self._project(control=False))["motivo"],
                         "sem controle no Desenho")

    def test_project_without_eeg_says_why(self):
        model = self._model(settings={"desenho": {"condicoes": [
            {"codigo": "N", "rotulo": "Neutro", "papel": "controle"},
            {"codigo": "A", "rotulo": "Amostra A", "papel": "amostra"}], "controle": "N"}})
        self.assertEqual(sensorial_effects.project_effects(model)["motivo"], "sem EEG gravado")

    def test_config_carries_what_changes_the_effect(self):
        config = sensorial_effects.project_effects(self._project())["config"]
        self.assertEqual(config, {"teste": "wilcoxon", "alfa": 0.05, "ppi": "spss", "base_limpa": True,
                                  "regra_spss": False})


class PanelTests(unittest.TestCase):
    @staticmethod
    def _effects(samples):
        rows = []
        for code, label in samples:
            for stage, stage_label in (("Olfacao", "Olfação"), ("PosOlfacao", "Pós-olfação")):
                rows.append({"condicao": code, "amostra": label, "controle": "Neutro", "etapa": stage,
                             "etapa_rotulo": stage_label, "medida": "FAI", "n": 8, "diferenca_media": 0.1,
                             "ic_inf": 0.0, "ic_sup": 0.2, "dp_diferenca": 0.12, "dz": 0.8, "dz_inf": 0.0,
                             "dz_sup": 1.6, "p": 0.04, "resultado": "diferença"})
        return pd.DataFrame(rows, columns=sensorial_effects.EFFECT_COLUMNS)

    def _frame(self):
        return sensorial_effects.panel_frame([
            {"projeto": {"id": 1, "name": "Estudo 1", "categoria": "Perfumaria"},
             "efeitos": self._effects([("A", "Amostra Alfa"), ("B", "Amostra Beta")]),
             "fichas": {"A": {"base": "Cítrica", "claims_alvo": ["Energia", "Foco"]},
                        "B": {"base": "Amadeirada", "categoria": "Corpo", "claims_alvo": ["Relaxamento"]}}},
            {"projeto": {"id": 2, "name": "Estudo 2", "categoria": ""},
             "efeitos": self._effects([("X", "Amostra X")]), "fichas": {}},
            {"projeto": {"id": 3, "name": "Estudo 3"}, "efeitos": pd.DataFrame(columns=sensorial_effects.EFFECT_COLUMNS),
             "fichas": {}},
        ])

    def test_the_frame_carries_the_sheet_and_falls_back_to_the_project_category(self):
        frame = self._frame()
        self.assertEqual(list(frame.columns), sensorial_effects.PANEL_COLUMNS)
        self.assertEqual(sorted(frame["projeto_id"].unique()), [1, 2])
        by_sample = frame.drop_duplicates("condicao").set_index("condicao")
        self.assertEqual(by_sample.loc["A", "categoria"], "Perfumaria")
        self.assertEqual(by_sample.loc["B", "categoria"], "Corpo")
        self.assertEqual(by_sample.loc["X", "base"], "")
        self.assertEqual(by_sample.loc["X", "claims_alvo"], [])

    def test_filter_options_and_filters(self):
        frame = self._frame()
        options = sensorial_effects.filter_options(frame)
        self.assertEqual(options["bases"], ["Amadeirada", "Cítrica"])
        self.assertEqual(options["categorias"], ["Corpo", "Perfumaria"])
        self.assertEqual(options["claims"], ["Energia", "Foco", "Relaxamento"])
        self.assertEqual(options["etapas"], ["Olfação", "Pós-olfação"])

        self.assertEqual(len(sensorial_effects.apply_filters(frame)), len(frame))
        self.assertEqual(set(sensorial_effects.apply_filters(frame, bases=["Cítrica"])["condicao"]), {"A"})
        self.assertEqual(set(sensorial_effects.apply_filters(frame, categorias=["Perfumaria"])["condicao"]), {"A"})
        self.assertEqual(set(sensorial_effects.apply_filters(frame, claims=["foco", "Relaxamento"])["condicao"]),
                         {"A", "B"})
        only_post = sensorial_effects.apply_filters(frame, etapas=["Pós-olfação"])
        self.assertEqual(set(only_post["etapa"]), {"PosOlfacao"})
        self.assertTrue(sensorial_effects.apply_filters(frame, medidas=["PPI"]).empty)

    def test_comparability_points_out_what_differs(self):
        same = {"teste": "wilcoxon", "alfa": 0.05, "ppi": "spss", "base_limpa": True, "regra_spss": True}
        self.assertEqual(sensorial_effects.comparability({"Estudo 1": same, "Estudo 2": dict(same)}), [])
        notes = sensorial_effects.comparability({"Estudo 1": same, "Estudo 2": dict(same, ppi="z"),
                                                 "Estudo 3": dict(same, teste="t_pareado")})
        self.assertEqual(len(notes), 2)
        self.assertIn("Modo do PPI difere", notes[1])
        self.assertIn("padronizado em Estudo 2", notes[1])
        self.assertIn("t pareado em Estudo 3", notes[0])

    def test_one_organization_at_a_time(self):
        projects = [{"id": 1, "organization_id": 7}, {"id": 2, "organization_id": 8}, {"id": 3, "organization_id": 7}]
        self.assertEqual([p["id"] for p in sensorial_effects.selected_projects(projects, 7)], [1, 3])
        self.assertEqual(sensorial_effects.selected_projects(projects, 0), [])

    def test_scale_columns(self):
        self.assertEqual(sensorial_effects.scale_columns("dz")["efeito"], "dz")
        self.assertEqual(sensorial_effects.scale_columns("bruta"),
                         {"efeito": "diferenca_media", "inf": "ic_inf", "sup": "ic_sup"})

    def test_the_forest_chart_draws_one_trace_per_stage(self):
        from utils.sensorial_charts import effects_forest

        frame = self._frame()
        figure = effects_forest(frame[frame["medida"] == "FAI"], "Atratividade sensorial", "dz")
        self.assertEqual([trace.name for trace in figure.data], ["Olfação", "Pós-olfação"])
        self.assertEqual(len(figure.data[0].y), 3)
        self.assertEqual(list(figure.data[0].x), [0.8, 0.8, 0.8])
        raw = effects_forest(frame[frame["medida"] == "FAI"], "Atratividade sensorial", "bruta")
        self.assertEqual(list(raw.data[0].x), [0.1, 0.1, 0.1])
        self.assertEqual(len(effects_forest(frame.iloc[0:0], "x").data), 0)


if __name__ == "__main__":
    unittest.main()
