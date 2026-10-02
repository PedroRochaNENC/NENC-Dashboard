"""Escolha e tempo até a decisão: do registro de campo às métricas.

Cada tempo fica na tarefa a que pertence (o de compra na estimulada; o da
planilha na tarefa resolvida por regra), e a escolha compara lojas e canais
dentro da mesma tarefa, mesmo onde não há olhar codificado.
"""

import math
import unittest

import pandas as pd

from tests.test_jornada_choice import _field_log
from tests.test_jornada_model import _bundle, _file
from utils.jornada_metrics import (
    attention_to_choice,
    choice_table,
    compute_all,
    decision_by_store,
    decision_table,
    time_kpi,
)
from utils.jornada_model import build_model


def _model(**settings):
    bundle = _bundle(
        [_file(9, "Relação Coletas.xlsx", _field_log())],
        participants=[
            {"code": "Pt01", "tempo_informado": "17s"},   # só fez a livre
            {"code": "Pt02", "tempo_informado": "19s"},   # igual ao tempo de compra
            {"code": "Pt03", "tempo_informado": "5s"},    # diferente do tempo de compra (81 s)
        ],
    )
    bundle["project"]["marcas"] = "Alfa\nBeta\nGama Livre"
    bundle["project"]["marca_foco"] = "Alfa"
    bundle["settings"] = {
        "dimensions": {"tipo": ["Diurno", "Noturno"]},
        "groups": {"PERFIL 1": {"profile": "Shopper Farma"}, "PERFIL 2": {"profile": "Shopper Atacado"}},
        "stores": {"1234": {"label": "DSP 1234", "channel": "FARMA"},
                   "atacado": {"label": "Atacado", "channel": "C&C"}},
    }
    bundle["settings"].update(settings)
    return build_model(bundle)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.model = _model()

    def test_choices_are_normalized_with_the_project_brands(self):
        choices = self.model["choices"].set_index(["participant", "task"])
        self.assertEqual(choices.loc[("Pt01", "livre"), "chosen_brands"], ["Alfa"])
        self.assertEqual(choices.loc[("Pt01", "livre"), "chosen_values_text"], "tipo: Diurno, Noturno")
        # A variante veio da aba de controle ("Beta Diurno").
        self.assertEqual(choices.loc[("Pt02", "estimulada"), "chosen_values_text"], "tipo: Diurno")
        self.assertEqual(choices.loc[("Pt03", "estimulada"), "considered_brands"], ["Alfa", "Gama Livre"])
        self.assertEqual(choices.loc[("Pt03", "estimulada"), "packs"], [32])
        self.assertEqual(choices.loc[("Pt03", "estimulada"), "channel"], "C&C")
        self.assertFalse(choices["has_gaze"].any())

    def test_the_profile_comes_from_the_group_mapped_in_the_project(self):
        participants = self.model["participants"].set_index("participant")
        self.assertEqual(participants.loc["Pt01", "profile"], "Shopper Farma")
        self.assertEqual(participants.loc["Pt03", "profile"], "Shopper Atacado")
        self.assertEqual(participants.loc["Pt01", "field_notes"], "anda pela loja")

    def test_each_time_lands_in_its_own_task(self):
        times = self.model["times"]
        sheet = times[times["source"] == "planilha"].set_index("participant")
        self.assertEqual(sheet.loc["Pt01", "task"], "livre")        # única tarefa de gôndola
        self.assertEqual(sheet.loc["Pt02", "task"], "estimulada")   # igual ao tempo de compra
        self.assertEqual(sheet.loc["Pt03", "task"], "estimulada")   # só fez a estimulada
        field = times[times["source"] == "campo"].set_index("participant")
        self.assertEqual(field.loc["Pt03", "seconds"], 81.0)
        self.assertNotIn("Pt04", field.index)                       # sem tempo anotado


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.metrics = compute_all(_model(), {})

    def test_choice_shares_compare_groups_inside_the_same_task(self):
        table = choice_table(self.metrics["choices"], "Alfa")
        stimulated = table[(table["task"] == "estimulada") & (table["group_type"] == "canal")]
        alfa = stimulated[stimulated["brand"] == "Alfa"].set_index("group")
        self.assertEqual((alfa.loc["C&C", "chose_n"], alfa.loc["C&C", "n"]), (1, 2))
        # Marca escolhida em outro grupo aparece com zero, para comparar.
        self.assertEqual(alfa.loc["FARMA", "chose_n"], 0)
        self.assertTrue(alfa["is_focus"].all())

    def test_decision_times_by_task_and_source(self):
        decision = self.metrics["decision"]
        stores = decision[decision["group_type"] == "loja"]
        field = stores[(stores["task"] == "estimulada") & (stores["source"] == "campo")].set_index("group")
        self.assertEqual(field.loc["Atacado", "median_s"], 81.0)
        self.assertEqual(field.loc["DSP 1234", "median_s"], 19.0)
        headline = decision_by_store(decision)
        # Na estimulada vale o tempo de compra; a planilha só aparece onde não há campo (a livre).
        self.assertEqual(set(headline["source"]), {"campo", "planilha"})
        self.assertEqual(set(headline.loc[headline["source"] == "planilha", "task"]), {"livre"})
        label, value = time_kpi(self.metrics)
        self.assertEqual((label, value), ("Tempo de compra (mediana)", 50.0))

    def test_findings_and_limitations_speak_about_choice_and_time(self):
        texts = [f["text"] for f in self.metrics["findings"] if f["section"] in ("escolha", "decisao")]
        self.assertTrue(any("1 de 2 escolheu Alfa" in t or "escolheram" in t for t in texts), texts)
        self.assertTrue(any(t.startswith("Tempo de compra na Jornada Estimulada") for t in texts), texts)
        notes = " ".join(self.metrics["limitations"])
        self.assertIn("registro de campo em texto livre", notes)
        self.assertIn("difere do tempo de compra", notes)

    def test_the_filter_also_cuts_choices_and_times(self):
        metrics = compute_all(_model(), {"tasks": ["livre"]})
        self.assertEqual(set(metrics["choices"]["task"]), {"livre"})
        self.assertEqual(set(metrics["times"]["task"]), {"livre"})


class AttentionToChoiceTests(unittest.TestCase):
    def test_the_chosen_brand_is_checked_against_the_gaze_of_the_same_task(self):
        choices = pd.DataFrame([{
            "participant": "Pt01", "task": "livre", "task_label": "Jornada Livre", "store_label": "DSP 1234",
            "chosen_brands": ["Beta"], "has_gaze": True, "recording_key": "Pt01|livre|1234",
        }])
        per = pd.DataFrame([
            {"recording_key": "Pt01|livre|1234", "brand": "Alfa", "share": 0.3, "looked": True, "dwell_s": 2.0,
             "first_credit": 1.0},
            {"recording_key": "Pt01|livre|1234", "brand": "Beta", "share": 0.7, "looked": True, "dwell_s": 4.0,
             "first_credit": 0.0},
        ])
        row = attention_to_choice(choices, per, 1.0).iloc[0]
        self.assertTrue(row["looked"] and row["examined"] and row["top_share"])
        self.assertFalse(row["first_noticed"])
        self.assertEqual(row["share_rank"], 1)

    def test_empty_inputs_give_empty_tables(self):
        self.assertTrue(decision_table(pd.DataFrame()).empty)
        self.assertTrue(choice_table(pd.DataFrame()).empty)
        self.assertTrue(math.isnan(time_kpi({"times": pd.DataFrame(columns=["source", "seconds"])})[1]))


if __name__ == "__main__":
    unittest.main()
