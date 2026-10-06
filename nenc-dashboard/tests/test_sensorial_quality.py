"""Qualidade das sessões do Teste Sensorial, por camada."""

import json

import pandas as pd

from tests.test_sensorial_model import BundleBase, _windows
from utils import sensorial_quality

SESSIONS = [("s1", "P07", "2001A", "A"), ("s2", "P08", "2001A", "A"), ("s3", "P09", "2001A", "A")]


class QualityTests(BundleBase):
    def _build(self):
        psd = _windows(SESSIONS, stages=("Basal", "Olfacao"), per_stage=60)
        psd = psd[~((psd["sessao_id"] == "s3") & (psd["Etapa"] == "Olfacao"))]  # s3 fica com poucas janelas
        psd = psd[~((psd["sessao_id"] == "s3") & (psd["Tempo"] > 3))]
        self._file("eeg_psd", psd)
        self._file("eeg_qualidade", pd.DataFrame({
            "sessao_id": ["s1", "s2", "s3"], "participant_code": ["P07", "P08", "P09"],
            "experimento": ["2001A"] * 3, "amostra": ["A"] * 3,
            "observacao": [None, None, "sessão gravou só marcadores"]}))
        self._file("campo_qualidade", pd.DataFrame({
            "participant_code": ["P08"], "n_canais_problema": [2], "canais_problema": ["1,4"]}))
        windows = []
        for sessao_id, quality in (("s1", ["ok"] * 10), ("s2", ["ok"] * 6 + ["sem pulso"] * 4)):
            for step, value in enumerate(quality):
                windows.append({"sessao_id": sessao_id, "Etapa": "Olfacao", "Bloco": 1, "Tempo": step * 0.25,
                                "qualidade_fc": value, "qualidade_gsr": "ok", "BPM_zscore": 0.0,
                                "GSR_CAL_zscore": 0.0, "RMSSD_zscore": 0.0})
        self._file("perifericos_metricas", pd.DataFrame(windows))
        self._file("perifericos_qualidade", pd.DataFrame({
            "sessao_id": ["s1", "s2"], "pulso_ok": ["True", "False"], "ppg_invertido": ["True", "False"]}))
        trials = pd.DataFrame({"sessao_id": ["s1"] * 3, "tentativa": [1, 2, 3], "palavra": ["a", "b", "c"],
                               "rt": [1.0, 2.0, 3.0], "resposta": ["Sim"] * 3})
        self._file("associacao_tentativas", trials)
        return self._model()

    def test_each_layer_gets_a_status_and_a_reason(self):
        model = self._build()
        quality = sensorial_quality.session_quality(model).set_index("sessao_id")
        self.assertEqual(quality.loc["s1", "qualidade_eeg"], "pass")
        self.assertEqual(quality.loc["s2", "qualidade_eeg"], "warn")  # 2 canais com problema no campo
        self.assertIn("canal(is) 1,4", quality.loc["s2", "detalhe_eeg"])
        self.assertEqual(quality.loc["s3", "qualidade_eeg"], "fail")  # 13 janelas: abaixo de 20
        self.assertIn("só marcadores", quality.loc["s3", "detalhe_eeg"])
        self.assertEqual(quality.loc["s1", "qualidade_fc"], "warn")  # PPG invertido
        self.assertEqual(quality.loc["s2", "qualidade_fc"], "fail")  # sem pulso no PPG
        self.assertIn("pulso ok em 60% das janelas", quality.loc["s2", "detalhe_fc"])
        self.assertEqual(quality.loc["s3", "qualidade_fc"], "na")
        self.assertEqual(quality.loc["s1", "qualidade_associacao"], "warn")  # 3 tentativas: abaixo de 5
        self.assertEqual(quality.loc["s2", "qualidade_associacao"], "na")
        self.assertEqual(quality.loc["s2", "qualidade"], "fail")

    def test_project_thresholds_sit_on_top_of_the_defaults(self):
        thresholds = sensorial_quality.thresholds_for_project(
            {"quality_thresholds": json.dumps({"tentativas_alerta": 2, "desconhecido": 9})})
        self.assertEqual(thresholds["tentativas_alerta"], 2.0)
        self.assertNotIn("desconhecido", thresholds)
        self.assertEqual(thresholds["eeg_janelas_alerta"], sensorial_quality.DEFAULT_THRESHOLDS["eeg_janelas_alerta"])
        quality = sensorial_quality.session_quality(self._build(), thresholds).set_index("sessao_id")
        self.assertEqual(quality.loc["s1", "qualidade_associacao"], "pass")
        self.assertEqual(sensorial_quality.badge("na"), "— não se aplica")


if __name__ == "__main__":
    import unittest

    unittest.main()
