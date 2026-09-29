"""Métricas da Jornada: definições que o relatório promete.

Cada teste fixa uma propriedade: shares somam 100%, gravação não codificada
não entra em denominador, quem não olhou produto nenhum conta no alcance,
empate divide a primeira marca notada, revisita é por AOI, o agregado
combina TTFF pelo número de quem olhou, e a share não depende da unidade.
"""

import math
import unittest

import pandas as pd

from utils.jornada_metrics import (
    attribute_table,
    brand_table,
    cliffs_delta,
    compare_groups,
    compute_all,
    design_confounds,
    limitations,
    packaging_tables,
    per_recording_brand,
    permutation_p_value,
)
from utils.jornada_model import build_model
from tests.test_jornada_model import _bundle, _export, _file, _frames


def _gaze_row(key, aoi, brand, share, dwell, visits, ttff, kind="produto", store="1", product=None):
    return {
        "recording_key": key, "participant": key.split("|")[0], "task": "livre", "store": store,
        "aoi": aoi, "aoi_key": "{}|{}".format(store, aoi), "kind": kind, "brand": brand,
        "line": "", "product": product or aoi, "part": "", "element": "", "include": True,
        "is_focus": brand == "A", "shelf_weight": None, "dwell_s": dwell, "visits": visits,
        "ttff_s": ttff, "looked": visits > 0 or dwell > 0, "avg_visit_s": math.nan,
        "max_visit_s": math.nan, "share_of_recording": share, "dwell_raw": dwell,
        "ttff_raw": ttff, "unit": "segundos", "status": "incluida", "source_file_id": 1,
    }


def _recording(key, status="incluida", store="1", profile="P1", channel="FARMA", task="livre"):
    participant = key.split("|")[0]
    return {"recording_key": key, "participant": participant, "task": task, "store": store,
            "store_label": "Loja {}".format(store), "channel": channel, "profile": profile,
            "status": status, "hz": 23.0, "has_frames": True, "unit": "segundos",
            "task_label": "Jornada Livre"}


def _model(gaze_rows, recordings, pooled=None):
    gaze = pd.DataFrame(gaze_rows)
    catalog = gaze[["store", "aoi", "aoi_key", "kind", "brand", "product", "include"]].drop_duplicates(
        "aoi_key").assign(shelf_weight=None, line="", part="", element="", is_focus=False, source="auto")
    return {
        "recordings": pd.DataFrame(recordings),
        "gaze": gaze,
        "pooled": pooled if pooled is not None else pd.DataFrame(
            columns=["used", "include", "is_outside", "task", "profile", "group"]),
        "catalog": catalog,
        "participants": pd.DataFrame({
            "participant": [r["participant"] for r in recordings],
            "profile": [r["profile"] for r in recordings],
            "tempo_decisao_s": [10.0 + i for i in range(len(recordings))],
        }),
        "meta": {"focus_brand": "A", "dimensions": {}, "examined_threshold_s": 1.0},
    }


# Tres gravacoes codificadas, uma nao codificada e uma que so olhou preco.
ROWS = [
    _gaze_row("Pt01|livre|1", "A p1", "A", 0.10, 2.0, 2, 5.0),
    _gaze_row("Pt01|livre|1", "A p2", "A", 0.05, 1.0, 1, 8.0),
    _gaze_row("Pt01|livre|1", "B", "B", 0.05, 1.0, 1, 3.0),
    _gaze_row("Pt02|livre|1", "A p1", "A", 0.02, 0.4, 1, 9.0),
    _gaze_row("Pt02|livre|1", "A p2", "A", 0.00, 0.0, 0, math.nan),
    _gaze_row("Pt02|livre|1", "B", "B", 0.06, 1.2, 1, 9.0),
    _gaze_row("Pt03|livre|1", "A p1", "A", 0.00, 0.0, 0, math.nan),
    _gaze_row("Pt03|livre|1", "A p2", "A", 0.00, 0.0, 0, math.nan),
    _gaze_row("Pt03|livre|1", "B", "B", 0.00, 0.0, 0, math.nan),
    _gaze_row("Pt03|livre|1", "A preço", "A", 0.01, 0.2, 1, 4.0, kind="preco", product="A p1"),
]
RECORDINGS = [
    _recording("Pt01|livre|1"),
    _recording("Pt02|livre|1", profile="P2"),
    _recording("Pt03|livre|1"),
    _recording("Pt04|livre|1", status="nao_codificada"),
]


class StatisticsTests(unittest.TestCase):
    def test_exact_permutation_and_cliffs_delta(self):
        p_value, method = permutation_p_value([1, 2, 3], [4, 5, 6])
        self.assertAlmostEqual(p_value, 0.1)
        self.assertEqual(method, "permutação exata")
        self.assertEqual(cliffs_delta([1, 2, 3], [4, 5, 6]), -1.0)

    def test_small_groups_stay_descriptive(self):
        values = pd.DataFrame({"v": [1, 2, 3, 4, 5, 6], "g": ["a"] * 3 + ["b"] * 3})
        default = compare_groups(values, "v", "g")
        self.assertEqual(default.loc[0, "method"], "descritivo")
        self.assertTrue(math.isnan(default.loc[0, "p_value"]))
        tested = compare_groups(values, "v", "g", min_n_test=3)
        self.assertAlmostEqual(tested.loc[0, "p_value"], 0.1)

    def test_confounded_design_is_detected(self):
        recordings = pd.DataFrame({
            "channel": ["FARMA", "FARMA", "C&C"], "task": ["livre", "livre", "estimulada"],
            "store": ["1", "2", "3"], "profile": ["x", "y", "x"],
        })
        found = design_confounds(recordings)
        self.assertEqual([item["variables"] for item in found], [("channel", "task")])
        self.assertIn("FARMA = Jornada Livre", found[0]["mapping"])


class BrandTests(unittest.TestCase):
    def setUp(self):
        self.model = _model(ROWS, RECORDINGS)
        included = self.model["recordings"][self.model["recordings"]["status"] == "incluida"]
        self.table = brand_table(self.model["gaze"], included, self.model["catalog"],
                                 examined_threshold_s=1.0, focus_brand="A").set_index("brand")
        self.per = per_recording_brand(self.model["gaze"], included)

    def test_shares_sum_to_one_per_recording_and_per_cell(self):
        per_recording = self.per.dropna(subset=["share"]).groupby("recording_key")["share"].sum()
        self.assertTrue(all(abs(total - 1) < 1e-9 for total in per_recording))
        self.assertAlmostEqual(self.table["share_mean"].sum(), 1.0)
        # Pt01: A = 0,15 / 0,20; Pt02: A = 0,02 / 0,08.
        self.assertAlmostEqual(self.table.loc["A", "share_mean"], (0.75 + 0.25) / 2)

    def test_uncoded_recordings_are_outside_every_denominator(self):
        self.assertEqual(int(self.table.loc["A", "n"]), 3)
        # Pt03 nao olhou produto (so o preco): fora da share, dentro do alcance.
        self.assertEqual(int(self.table.loc["A", "n_pos"]), 2)
        self.assertAlmostEqual(self.table.loc["A", "reach"], 2 / 3)

    def test_funnel_revisit_is_per_aoi(self):
        # Pt01 tem 2 visitas numa AOI de A; Pt02 so 1 visita em cada AOI.
        self.assertAlmostEqual(self.table.loc["A", "revisit"], 1 / 3)
        self.assertAlmostEqual(self.table.loc["A", "examined"], 1 / 3)
        self.assertAlmostEqual(self.table.loc["B", "examined"], 2 / 3)
        self.assertAlmostEqual(self.table.loc["A", "examined_given_noticed"], 1 / 2)

    def test_first_noticed_splits_ties_and_relative_ttff(self):
        # Pt01: B (3 s) antes de A (5 s); Pt02: empate A e B em 9 s.
        self.assertAlmostEqual(self.table.loc["B", "first_noticed"], (1 + 0.5) / 2)
        self.assertAlmostEqual(self.table.loc["A", "first_noticed"], 0.5 / 2)
        self.assertAlmostEqual(self.table.loc["A", "rel_ttff_median"], 1.0)
        self.assertAlmostEqual(self.table.loc["A", "ttff_median"], 7.0)

    def test_presence_index_uses_aoi_count_without_weights(self):
        self.assertAlmostEqual(self.table.loc["A", "presence"], 2 / 3)
        self.assertEqual(self.table.loc["A", "presence_source"], "nº de AOIs (aproximação)")

    def test_compute_all_builds_findings_and_limitations(self):
        metrics = compute_all(self.model, {})
        texts = " ".join(f["text"] for f in metrics["findings"])
        # A e B dividem a atencao meio a meio nestes dados.
        self.assertIn("Empate técnico", texts)
        self.assertIn("primeira marca notada por 1,5 de 2", texts)
        self.assertIn("1 de 3 participantes não olharam A", texts)
        self.assertTrue(any("sem codificação" in note for note in metrics["limitations"]))
        self.assertEqual(metrics["sample"]["recordings"], 3)
        self.assertFalse(metrics["price"].empty)


class AttributeTests(unittest.TestCase):
    def test_attribute_share_is_read_against_shelf_presence(self):
        rows = [
            dict(_gaze_row("Pt01|livre|1", "A N p1", "A", 0.10, 2.0, 2, 5.0), attr_tipo="Noturno"),
            dict(_gaze_row("Pt01|livre|1", "A N p2", "A", 0.05, 1.0, 1, 8.0), attr_tipo="Noturno"),
            dict(_gaze_row("Pt01|livre|1", "B D", "B", 0.05, 1.0, 1, 3.0), attr_tipo="Diurno"),
        ]
        gaze = pd.DataFrame(rows)
        catalog = gaze[["store", "aoi", "aoi_key", "kind", "brand", "product", "include", "attr_tipo"]].assign(
            shelf_weight=None)
        recordings = pd.DataFrame([_recording("Pt01|livre|1")])
        table = attribute_table(gaze, recordings, ["tipo"], catalog).set_index("value")
        self.assertAlmostEqual(table.loc["Noturno", "share_mean"], 0.75)
        # Duas das três AOIs com tipo são Noturno: 75% de atenção sobre 67% do espaço.
        self.assertAlmostEqual(table.loc["Noturno", "presence"], 2 / 3)
        self.assertAlmostEqual(table.loc["Noturno", "presence_index"], 0.75 / (2 / 3))
        # Sem catálogo, a presença fica em branco em vez de inventada.
        self.assertTrue(attribute_table(gaze, recordings, ["tipo"])["presence"].isna().all())


class PackagingTests(unittest.TestCase):
    def _pooled(self):
        rows = []
        for group, profile, n, lookers, ttff, dwell in (
            ("PERFIL 1", "P1", 5, 2, 10.0, 4.0),
            ("PERFIL 2", "P2", 6, 3, 20.0, 6.0),
        ):
            rows.append({"task": "embalagens", "store": "", "group": group, "profile": profile,
                         "n_group": n, "aoi": "A_MARCA", "aoi_key": "|A_MARCA", "kind": "embalagem",
                         "brand": "A", "element": "MARCA", "is_outside": False, "include": True,
                         "used": True, "dwell_sum_s": dwell, "visits_sum": lookers * 2,
                         "lookers": lookers, "ttff_mean_lookers_s": ttff, "share_of_pool_time": 0.01,
                         "max_visit_s": 1.0})
        rows.append(dict(rows[0], group="TODOS", profile="Todos", n_group=11, lookers=99))
        return pd.DataFrame(rows)

    def test_groups_combine_by_sum_and_ttff_weighted_by_lookers(self):
        tables = packaging_tables(self._pooled())
        elements = tables["elements"].set_index("group")
        self.assertNotIn("TODOS", elements.index)
        combined = elements.loc["Todos"]
        self.assertEqual(int(combined["n_group"]), 11)
        self.assertAlmostEqual(combined["reach"], 5 / 11)
        self.assertAlmostEqual(combined["ttff_mean_s"], (10 * 2 + 20 * 3) / 5)
        self.assertAlmostEqual(elements.loc["PERFIL 1", "dwell_per_participant_s"], 4.0 / 5)
        brands = tables["brands"].set_index("group")
        self.assertAlmostEqual(brands.loc["PERFIL 2", "logo_reach"], 0.5)

    def test_low_element_coverage_becomes_a_limitation(self):
        pooled = self._pooled()
        outside = []
        # Tempo do grupo = tempo no elemento / fração (4 s / 0,01 = 400 s; 6 s / 0,01 = 600 s).
        for group, dwell, share in (("PERFIL 1", 384.0, 0.96), ("PERFIL 2", 564.0, 0.94)):
            outside.append(dict(pooled.iloc[0].to_dict(), group=group, aoi="", aoi_key="|", kind="fora",
                                brand="", element="", is_outside=True, dwell_sum_s=dwell,
                                share_of_pool_time=share))
        pooled = pd.concat([pooled, pd.DataFrame(outside)], ignore_index=True)
        tables = packaging_tables(pooled)
        coverage = tables["coverage"].set_index("group")["aoi_coverage"]
        self.assertAlmostEqual(coverage["PERFIL 1"], 0.04)
        notes = limitations(_model(ROWS, RECORDINGS), {"packaging": tables})
        self.assertTrue(any("somam só 4% a 6% do tempo gravado" in note for note in notes), notes)


class UnitFreeShareTests(unittest.TestCase):
    def test_share_is_the_same_in_samples_and_in_seconds(self):
        header = ["Marca A p1", "Marca B"]
        samples = _export([
            [header[0], 30, 30 / 200, 10, 10, 10, 1, 40, 1, "Jornadas Livres", "Pt01"],
            [header[1], 10, 10 / 200, 10, 10, 10, 1, 90, 1, "Jornadas Livres", "Pt01"],
        ])
        seconds = _export([
            [header[0], 1.5, 1.5 / 10, 0.5, 0.5, 0.5, 1, 2.0, 1, "Jornadas Livres", "Pt01"],
            [header[1], 0.5, 0.5 / 10, 0.5, 0.5, 0.5, 1, 4.5, 1, "Jornadas Livres", "Pt01"],
        ])
        frames = _file(2, "Pt01-JLivre-DSP1234.csv", _frames(200, 0.05))
        from_samples = compute_all(build_model(_bundle([_file(1, "DSP1234-INDIVIDUAL2.csv", samples), frames])))
        from_seconds = compute_all(build_model(_bundle([_file(1, "DSP1234-INDIVIDUAL2.csv", seconds)])))
        a = from_samples["brand"].set_index("brand")["share_mean"]
        b = from_seconds["brand"].set_index("brand")["share_mean"]
        self.assertAlmostEqual(a["Marca A"], b["Marca A"])
        self.assertAlmostEqual(a["Marca A"], 0.75)


if __name__ == "__main__":
    unittest.main()
