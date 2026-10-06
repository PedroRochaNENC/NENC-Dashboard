"""Desenho, indices, limpeza e o modelo do Teste Sensorial, com dados sinteticos."""

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from tests.test_sensorial_ingest import CANARY_WORDS, _indicadores_csv, _texts
from utils import sensorial_cleaning, sensorial_design, sensorial_indices, sensorial_ingest, sensorial_store
from utils.sensorial_model import build_model


def _psd_row(**values) -> dict:
    row = {column: 1.0 for column in sensorial_indices.BAND_COLUMNS}
    row.update(values)
    return row


class IndexTests(unittest.TestCase):
    def test_the_formulas_of_the_spss_syntax(self):
        row = _psd_row(F3_Alpha=10.0, F4_Alpha=100.0, T3_Alpha=1.0, T4_Alpha=10.0,
                       C3_Alpha=2.0, C4_Alpha=4.0, P3_Alpha=6.0, P4_Alpha=8.0)
        for channel in ("F3", "F4", "P3", "P4"):
            row.update({channel + "_Beta": 2.0, channel + "_Gamma": 4.0, channel + "_Theta": 5.0})
        for channel in ("Fz", "Cz"):
            row.update({channel + "_Beta": 6.0, channel + "_Alpha": 3.0})
        for channel in ("T5", "T6"):
            row.update({channel + "_Theta": 5.0, channel + "_Gamma": 4.0})
        indices = sensorial_indices.compute_indices(pd.DataFrame([row])).iloc[0]
        self.assertAlmostEqual(indices["FAI"], 1.0)
        self.assertAlmostEqual(indices["TEMP_ASYM"], 1.0)
        # Teta de C3/C4 vale 1 e de P3/P4 vale 5: média 3; alfa médio 5.
        self.assertAlmostEqual(indices["ALPHA_THETA"], 5.0 / 3.0)
        # Atenção: beta 2 e gama 4 (média 3) sobre alfa médio (10+100+6+8)/4 = 31 e teta 5 (média 18).
        self.assertAlmostEqual(indices["ATTENTION_IDX"], 3.0 / 18.0)
        self.assertAlmostEqual(indices["MEMORY_IDX"], (5.0 + 4.0) / 2)
        self.assertAlmostEqual(indices["MIDLINE_AROUSAL"], 2.0)

    def test_missing_channels_zero_denominators_and_the_log_floor(self):
        rows = pd.DataFrame([
            _psd_row(C3_Alpha=np.nan, C4_Alpha=2.0, P3_Alpha=2.0, P4_Alpha=2.0),
            _psd_row(C3_Theta=0.0, C4_Theta=0.0, P3_Theta=0.0, P4_Theta=0.0),
            _psd_row(F3_Alpha=0.0),
            _psd_row(F4_Alpha=np.nan),
        ])
        indices = sensorial_indices.compute_indices(rows)
        self.assertAlmostEqual(indices["ALPHA_THETA"].iloc[0], 2.0)  # MEAN do SPSS: só os presentes
        self.assertTrue(math.isnan(indices["ALPHA_THETA"].iloc[1]))
        self.assertAlmostEqual(indices["FAI"].iloc[2], 30.0)  # log10(1) − log10(1e-30)
        self.assertTrue(math.isnan(indices["FAI"].iloc[3]))

    def test_ppi_standardized_or_as_in_spss(self):
        frame = pd.DataFrame({"FAI": [1.0, 2.0, 3.0], "ATTENTION_IDX_MID": [10.0, 20.0, 30.0],
                              "MEMORY_IDX": [1e-12, 2e-12, 3e-12]})
        raw = sensorial_indices.ppi(frame, "spss")
        self.assertAlmostEqual(raw.iloc[0], 0.4 * 1 + 0.3 * 10 + 0.3 * 1e-12)
        standardized = sensorial_indices.ppi(frame, "z")
        self.assertEqual([round(v, 6) for v in standardized], [-1.0, 0.0, 1.0])
        weighted = sensorial_indices.ppi(frame, "z", {"FAI": 1.0, "ATTENTION_IDX_MID": 0.0, "MEMORY_IDX": 0.0})
        self.assertEqual([round(v, 6) for v in weighted], [-1.0, 0.0, 1.0])


class CleaningTests(unittest.TestCase):
    def test_marks_and_mahalanobis_follow_the_syntax(self):
        from scipy.spatial.distance import mahalanobis
        from scipy.stats import chi2

        generator = np.random.default_rng(7)
        columns = ["c{}".format(i) for i in range(6)]
        frame = pd.DataFrame(np.exp(generator.normal(size=(300, 6))), columns=columns)
        frame.loc[0, columns] = math.exp(8.0)  # 6 marcas positivas
        frame.loc[1, "c0"] = math.exp(6.0)  # 1 marca
        frame.loc[2, "c1"] = 0.0  # sem ln: fora da contagem e da Mahalanobis
        flags = sensorial_cleaning.outlier_flags(frame, columns, min_marks=5)
        self.assertEqual(flags.loc[0, "marcas_positivas"], 6)
        self.assertFalse(flags.loc[0, "valida"])
        self.assertTrue(math.isnan(flags.loc[2, "mahalanobis"]))
        logs = np.log(frame.drop(index=2)[columns])
        inverse = np.linalg.inv(np.cov(logs.to_numpy(), rowvar=False))
        expected = mahalanobis(logs.loc[1], logs.mean(), inverse) ** 2
        self.assertAlmostEqual(flags.loc[1, "mahalanobis"], expected, places=6)
        self.assertAlmostEqual(flags.loc[1, "p_mahalanobis"], chi2.sf(expected, 6), places=9)
        self.assertEqual(bool(flags.loc[1, "valida"]), not chi2.sf(expected, 6) < 0.001)

    def test_base_limpa_keys_round_the_time(self):
        windows = pd.DataFrame({"sessao_id": ["s1", "s1"], "Etapa": ["Basal", "Basal"], "Bloco": [1, 1],
                                "Tempo": [0.25, 0.5]})
        keys = pd.DataFrame({"sessao_id": ["s1"], "Etapa": ["Basal"], "Bloco": [1], "Tempo": [0.2500000001]})
        self.assertEqual(sensorial_cleaning.in_base_limpa(windows, keys).tolist(), [True, False])
        self.assertIsNone(sensorial_cleaning.in_base_limpa(windows, None))
        trials = pd.DataFrame({"sessao_id": ["s1", "s1"], "tentativa": [1, 2]})
        kept = sensorial_cleaning.in_base_limpa(trials, pd.DataFrame({"sessao_id": ["s1"], "tentativa": [2]}))
        self.assertEqual(kept.tolist(), [False, True])


class DesignTests(unittest.TestCase):
    def test_conditions_and_stages_are_deduced(self):
        sessions = pd.DataFrame({
            "experimento": ["2001Basal", "2001Neutro", "2002Neutro", "2001A", "2001B", "2001B", "2001B", "Piloto"],
            "amostra": [None, "N", None, "A", "B1", "B2", "B1-IAT e Prosodia", None],
        })
        manifest = {"configuracao": {"PROJETO": {"etapas": {"padronizar": {
            "^Basal$": "Basal", "^Olfacao$": "Olfacao", "^PosOlfacao$": "PosOlfacao"}}}}}
        design = sensorial_design.deduce_design(
            sessions, ["PosOlfacao", "Audio>Gravacao", "Olfacao", "Basal"], [manifest])
        self.assertEqual([(c["codigo"], c["papel"]) for c in design["condicoes"]],
                         [("Basal", "basal"), ("N", "controle"), ("A", "amostra"), ("B1", "amostra"),
                          ("B2", "amostra")])
        self.assertEqual(design["mapa"]["2002Neutro|"], "N")
        self.assertEqual(design["mapa"]["2001B|B1-IAT e Prosodia"], "B1")
        self.assertEqual(design["mapa"]["Piloto|"], "")
        self.assertEqual([s["codigo"] for s in design["etapas"]], ["Basal", "Olfacao", "PosOlfacao"])
        self.assertEqual(design["referencia"], {"condicao": "Basal", "etapa": "Basal"})
        self.assertEqual(design["controle"], "N")
        self.assertEqual(sensorial_design.condition_for("2001B", "B2", design), "B2")

    def test_project_settings_sit_on_top_of_the_defaults(self):
        settings = sensorial_design.resolve_settings({
            "indices": {"ppi": {"modo": "spss"}, "nomes": {"FAI": "Atratividade"}},
            "limpeza": {"regra_spss": True},
        })
        self.assertEqual(settings["indices"]["ppi"]["modo"], "spss")
        self.assertEqual(settings["indices"]["ppi"]["pesos"]["FAI"], 0.4)  # o resto continua do padrão
        self.assertTrue(settings["limpeza"]["regra_spss"])
        self.assertTrue(settings["limpeza"]["usar_base_limpa"])
        self.assertEqual(sensorial_design.index_names(settings)["FAI"], "Atratividade")
        self.assertEqual(sensorial_design.DEFAULT_SETTINGS["limpeza"]["regra_spss"], False)  # padrão intacto


def _windows(sessions, stages=("Basal", "Olfacao", "PosOlfacao", "Audio>Gravacao"), per_stage=4, seed=3):
    generator = np.random.default_rng(seed)
    rows = []
    for sessao_id, code, experimento, amostra in sessions:
        for stage in stages:
            for step in range(per_stage):
                row = {"sessao_id": sessao_id, "participant_code": code, "experimento": experimento,
                       "amostra": amostra, "Etapa": stage, "Bloco": 1, "Tempo": step * 0.25,
                       "primeira_olfacao_s": 2.0}
                row.update({c: float(np.exp(generator.normal())) for c in sensorial_indices.BAND_COLUMNS})
                rows.append(row)
    return pd.DataFrame(rows)


class BundleBase(unittest.TestCase):
    """Tabelas sintéticas em Parquet e o modelo montado sobre elas, sem banco."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.files = []

    def _file(self, role, table, meta=None):
        path = sensorial_store.write_table(table, self.folder / "{}.parquet".format(len(self.files)))
        self.files.append({"id": len(self.files) + 1, "role": role, "filename": role, "run_id": None,
                           "meta": meta or {}, "table_path": str(path)})

    def _model(self, settings=None, decisions=(), participants=()):
        return build_model({"project": {"id": 1, "data_version": 3}, "settings": settings or {},
                            "files": self.files, "participants": list(participants), "sessions": list(decisions)})


class ModelTests(BundleBase):
    SESSIONS = [
        ("s1", "P07", "2001A", "A"),
        ("s2", "P07", "2001Basal", None),
        ("s3", None, "2001A", "A"),
        ("s4", "P08", "2001A", "A"),
        ("s5", "P08", "2001A", "A"),
    ]

    def test_rules_apply_in_order_and_say_why(self):
        psd = _windows(self.SESSIONS)
        self._file("eeg_psd", psd)
        kept = psd[psd["sessao_id"] != "s4"][["sessao_id", "Etapa", "Bloco", "Tempo"]]
        self._file("base_limpa_eeg", kept)
        decisions = [
            {"sessao_id": "s1", "layer": "eeg", "status": "excluida", "reason": "eletrodo solto",
             "participant_code_override": None, "condition_override": None},
            {"sessao_id": "s3", "layer": "todas", "status": "auto", "reason": None,
             "participant_code_override": "P09", "condition_override": None},
        ]
        model = self._model(decisions=decisions)
        sessions = model["sessions"].set_index("sessao_id")
        self.assertEqual(sessions.loc["s3", "participant_code"], "P09")
        self.assertEqual(sessions.loc["s1", "motivo_eeg"], "excluída: eletrodo solto")
        self.assertEqual(sessions.loc["s4", "motivo_eeg"], "fora da BASE LIMPA")
        self.assertTrue(sessions.loc["s5", "incluida_eeg"])
        self.assertTrue(sessions.loc["s4", "repetida"] and sessions.loc["s5", "repetida"])
        self.assertEqual(sessions.loc["s2", "condicao"], "Basal")

        eeg = model["eeg"]
        audio = eeg[eeg["Etapa"] == "Audio>Gravacao"]
        self.assertFalse(audio["incluida"].any())
        self.assertTrue((audio[audio["sessao_id"] == "s5"]["motivo"] == "etapa fora da análise").all())
        included = eeg[eeg["incluida"]]
        self.assertEqual(sorted(included["sessao_id"].unique()), ["s2", "s3", "s5"])
        self.assertAlmostEqual(included["PPI"].mean(), 0.0, places=9)  # z no conjunto analisado
        self.assertTrue(eeg.loc[~eeg["incluida"], "PPI"].isna().all())
        self.assertNotIn("F3_Alpha", eeg)  # as bandas não ficam no modelo
        self.assertEqual([c["codigo"] for c in model["design"]["condicoes"]], ["Basal", "A"])
        self.assertTrue(any(i["code"] == "repetidas" for i in model["issues"]))

        # Incluir por decisão vale mais que a BASE LIMPA.
        decisions.append({"sessao_id": "s4", "layer": "todas", "status": "incluida", "reason": None,
                          "participant_code_override": None, "condition_override": None})
        model = self._model(decisions=decisions)
        eeg = model["eeg"]
        self.assertTrue(eeg[(eeg["sessao_id"] == "s4") & (eeg["Etapa"] == "Olfacao")]["incluida"].all())

    def test_the_spss_rule_only_runs_when_asked_and_without_base_limpa(self):
        psd = _windows(self.SESSIONS[:2], per_stage=40)
        psd.loc[psd.index[5], [c for c in sensorial_indices.BAND_COLUMNS[:10]]] = 1e9
        self._file("eeg_psd", psd)
        self.assertNotIn("marcas_outlier", self._model()["eeg"])
        eeg = self._model({"limpeza": {"regra_spss": True}})["eeg"]
        self.assertEqual(eeg.loc[5, "motivo"], "outlier pela regra do SPSS")

    def test_peripherals_follow_quality_unless_the_base_limpa_decides(self):
        rows = []
        for sessao_id, code, experimento, amostra in self.SESSIONS[:2]:
            for quality in ("ok", "fluxo corrompido"):
                rows.append({"sessao_id": sessao_id, "participant_code": code, "experimento": experimento,
                             "amostra": amostra, "Etapa": "Olfacao", "Bloco": 1, "Tempo": 0.0 if quality == "ok"
                             else 1.0, "BPM": 70.0, "RMSSD": 30.0, "GSR_CAL_mean": 2.0, "BPM_zscore": 1.0,
                             "RMSSD_zscore": 0.0, "GSR_CAL_zscore": 1.0, "qualidade_fc": quality,
                             "qualidade_gsr": "ok"})
        self._file("perifericos_metricas", pd.DataFrame(rows))
        peri = self._model()["peripherals"]
        self.assertAlmostEqual(peri["Emotional_Index"].iloc[0], math.cos(math.atan(1.0 / (1.0 + 1e-6))))
        self.assertAlmostEqual(peri["Comfort_Score"].iloc[0], 0.5)
        self.assertEqual(peri["incluida_fc"].tolist(), [True, False, True, False])
        self.assertTrue(peri["incluida_gsr"].all())
        self.assertTrue(peri.loc[1, "motivo_fc"].startswith("sinal ruim"))
        keys = pd.DataFrame(rows)[["sessao_id", "Etapa", "Bloco", "Tempo"]].iloc[[1, 3]]
        self._file("base_limpa_perifericos", keys)
        model = self._model()
        self.assertEqual(model["peripherals"]["incluida_fc"].tolist(), [False, True, False, True])
        self.assertTrue(any(i["code"] == "perifericos_qualidade" for i in model["issues"]))

    def test_trials_drop_empty_words_and_get_the_cr_of_their_session(self):
        trials = pd.DataFrame({
            "sessao_id": ["s1"] * 4, "participant_code": ["P07"] * 4, "experimento": ["2001A"] * 4,
            "amostra": ["A"] * 4, "tentativa": [1, 2, 3, 4], "palavra": ["Fresco", "Leve", "Doce", None],
            "rt": [1.0, 2.0, 3.0, 9.0], "resposta": ["Sim", "Nao", "Sim", None]})
        self._file("associacao_tentativas", trials)
        model = self._model()
        table = model["trials"]
        self.assertEqual(table["cr"].tolist(), [1.0, 0.0, -1.0])
        self.assertEqual(table["sim"].tolist(), [True, False, True])
        self.assertEqual([c["palavra"] for c in model["claims"]], ["Doce", "Fresco", "Leve"])
        self._file("base_limpa_associacao", pd.DataFrame({"sessao_id": ["s1", "s1"], "tentativa": [1, 3]}))
        table = self._model()["trials"]
        self.assertEqual(table["incluida"].tolist(), [True, False, True])
        # Só as tentativas 1 e 3 entram: média 2 e desvio √2 entre elas.
        self.assertEqual([round(v, 9) for v in table["cr"].dropna()], [round(2 ** -0.5, 9), -round(2 ** -0.5, 9)])

    def test_tables_read_from_the_pipeline_never_bring_names_back(self):
        parsed = sensorial_ingest.parse_file("indicadores.csv", _indicadores_csv())
        self._file("eeg_indicadores", parsed.table)
        model = self._model(participants=[{"code": "P07", "profile": {"sexo": "F"}, "notes": None}])
        self.assertFalse(model["eeg"].empty)
        self.assertNotIn("FAI", model["eeg"])  # sem PSD, sem os índices do relatório
        self.assertTrue(any(i["code"] == "sem_psd" for i in model["issues"]))
        self.assertIn("atencao", model["pipeline_indicators"])
        self.assertEqual(model["participants"].set_index("participant_code").loc["P07", "perfil_sexo"], "F")
        text = _texts(model["sessions"]) + _texts(model["eeg"]) + _texts(model["participants"])
        for word in CANARY_WORDS:
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
