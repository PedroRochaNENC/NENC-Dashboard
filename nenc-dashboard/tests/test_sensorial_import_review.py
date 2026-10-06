"""Prévia da importação do Teste Sensorial: só resumo, nunca linha nem nome."""

import json
import unittest

from tests.test_sensorial_imports import RUN_A, _ImportBase, _sha
from tests.test_sensorial_ingest import CANARY_WORDS, _indicadores_csv
from utils import sensorial_db, sensorial_import_review, sensorial_imports, sensorial_ingest


class SummaryTests(unittest.TestCase):
    def test_the_preview_keeps_counts_and_codes_only(self):
        parsed = sensorial_ingest.parse_file("indicadores.csv", _indicadores_csv(),
                                             rel_path=RUN_A + "indicadores.csv")
        read = sensorial_import_review.summary(parsed)
        self.assertNotIn("table", read)
        text = json.dumps(read, ensure_ascii=False).lower()
        for word in CANARY_WORDS:
            self.assertNotIn(word, text)
        rows = sensorial_import_review.data_rows([(1, "indicadores.csv", "x" * 64, read)], set(), None)
        row = rows.iloc[0]
        self.assertEqual((row["papel"], row["rodada"], row["sessoes"], row["participantes"], row["sem_codigo"]),
                         ("EEG: indicadores por janela", "run_20260915-171636", 2, 1, 3))
        self.assertTrue(row["incluir"])

    def test_duplicates_and_base_limpa_coverage(self):
        keys = sensorial_ingest.parse_file("x.base_limpa_eeg.csv", b"sessao_id,Etapa,Bloco,Tempo\ns1,Basal,1,0.25\n")
        read = sensorial_import_review.summary(keys)
        rows = sensorial_import_review.data_rows([(2, "x.base_limpa_eeg.csv", "d" * 64, read)], {"d" * 64}, 4)
        self.assertFalse(rows.iloc[0]["incluir"])
        self.assertIn("já está no projeto", rows.iloc[0]["avisos"])
        self.assertIn("cobre 1 das 4 sessões do EEG", rows.iloc[0]["avisos"])

    def test_the_report_text(self):
        text = sensorial_import_review.report_text({"files": 3, "duplicates": 1, "participants": 35,
                                                    "documents": 0, "skipped": [], "warnings": []})
        self.assertEqual(text, "Gravado: 3 arquivo(s); 1 já estava(m) no projeto; 35 participante(s) novo(s).")


class ParticipantTests(_ImportBase):
    def test_recording_registers_the_codes_found_in_the_data(self):
        batch_id = sensorial_imports.create_batch(self.org, self.project_id)
        data = _indicadores_csv()
        sensorial_imports.receive_chunk(self.org, batch_id, rel_path=RUN_A + "indicadores.csv",
                                        role="eeg_indicadores", sha256=_sha(data), size=len(data), offset=0,
                                        data=data)
        sensorial_imports.close_batch(self.org, batch_id)
        with self._as(self.first_admin):
            report = sensorial_imports.apply_batch(self.project_id, batch_id)
        self.assertEqual(report["participants"], 1)
        self.assertEqual([p["code"] for p in sensorial_db.list_participants(self.project_id)], ["P07"])
        self.assertEqual(sensorial_db.list_participants(self.project_id)[0]["source"], "dados")

    def test_a_screen_upload_takes_the_same_path(self):
        parsed = sensorial_ingest.parse_file("perfil.csv", "Código;Sexo\n7;F\n12;M\n".encode("utf-8"), role="perfil")
        with self._as(self.first_admin):
            result = sensorial_imports.record_upload(self.project_id, "perfil.csv",
                                                     "Código;Sexo\n7;F\n12;M\n".encode("utf-8"), parsed)
        self.assertEqual((len(result["added"]), result["participants"]), (1, 2))
        profiles = {p["code"]: p["profile"] for p in sensorial_db.list_participants(self.project_id)}
        self.assertEqual(profiles, {"P07": {"sexo": "F"}, "P12": {"sexo": "M"}})


if __name__ == "__main__":
    unittest.main()
