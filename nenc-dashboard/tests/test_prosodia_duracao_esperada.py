"""Faixa de duração esperada: persistência e limiares dos checks objetivos.

Antes da faixa, o mesmo padrão (fala mínima de 60 s, 100 palavras) valia para
recados de 20 s e para entrevistas de 1 h: os recados falhavam todos e as
entrevistas só falhavam com menos de 1 min de fala. A faixa calibra os
limiares; projetos sem ela seguem com os padrões do tipo.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils import auth, prosodia_db
from utils.prosodia_project_types import (
    DURACAO_ESPERADA_LABELS,
    ENTREVISTA_QUALITATIVA,
    PESQUISA_OPINIAO,
    normalize_duracao_esperada,
)
from utils.prosodia_quality import (
    DEFAULT_THRESHOLDS,
    DEFAULT_THRESHOLDS_PESQUISA_OPINIAO,
    default_thresholds,
    run_quality_checks,
    thresholds_for_duracao,
    thresholds_for_project,
)

FAIXAS = list(DURACAO_ESPERADA_LABELS)


class DuracaoEsperadaPersistenceTests(unittest.TestCase):
    def setUp(self):
        # Banco descartavel: sem NENC_DB_PATH o teste escreveria no banco local.
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.environment = patch.dict(
            os.environ, {"NENC_DB_PATH": str(self.database_path)}
        )
        self.environment.start()
        organization = auth.create_organization(
            "Organization One", database_path=self.database_path, _bootstrap=True
        )
        admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        prosodia_db.init_db()
        for patcher in (
            patch("utils.prosodia_db._require_write", return_value=admin),
            patch("utils.prosodia_db._active_organization_id", return_value=organization.id),
            patch("utils.prosodia_db._claim_external_project_resources"),
            patch("utils.prosodia_db._audit"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_new_project_without_range_has_none(self):
        project_id = prosodia_db.create_project("Projeto")

        self.assertIsNone(prosodia_db.get_project(project_id)["duracao_esperada"])

    def test_range_is_saved_on_create(self):
        project_id = prosodia_db.create_project("Recados", duracao_esperada="ate_30s")

        self.assertEqual(prosodia_db.get_project(project_id)["duracao_esperada"], "ate_30s")

    def test_update_without_range_keeps_the_saved_one(self):
        # Como qr_codes.py e whatsapp_campanhas.py: regravam o projeto
        # repassando os proprios campos, sem a faixa.
        project_id = prosodia_db.create_project("Recados", duracao_esperada="ate_30s")

        prosodia_db.update_project(project_id, name="Recados", qr_verification_text="Novo")

        self.assertEqual(prosodia_db.get_project(project_id)["duracao_esperada"], "ate_30s")

    def test_update_with_range_changes_it(self):
        project_id = prosodia_db.create_project("Recados", duracao_esperada="ate_30s")

        prosodia_db.update_project(project_id, name="Recados", duracao_esperada="30_60min")

        self.assertEqual(prosodia_db.get_project(project_id)["duracao_esperada"], "30_60min")

    def test_unknown_range_is_refused(self):
        with self.assertRaises(ValueError):
            prosodia_db.create_project("Projeto", duracao_esperada="5min")

        project_id = prosodia_db.create_project("Projeto", duracao_esperada="1_3min")
        with self.assertRaises(ValueError):
            prosodia_db.update_project(project_id, name="Projeto", duracao_esperada="longa")
        self.assertEqual(prosodia_db.get_project(project_id)["duracao_esperada"], "1_3min")


class LegacyDatabaseMigrationTests(unittest.TestCase):
    def test_existing_projects_get_no_range(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            auth.create_organization(
                "Legacy Owner", database_path=database_path, _bootstrap=True
            )
            database = sqlite3.connect(database_path)
            try:
                database.execute(
                    "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                database.execute("INSERT INTO projects (name) VALUES ('Projeto antigo')")
                database.commit()
            finally:
                database.close()

            with patch.dict(os.environ, {"NENC_DB_PATH": str(database_path)}):
                prosodia_db.init_db()
                prosodia_db.init_db()  # a migracao roda a cada pagina: precisa ser idempotente

            database = sqlite3.connect(database_path)
            try:
                duracao = database.execute("SELECT duracao_esperada FROM projects").fetchone()[0]
            finally:
                database.close()

        self.assertIsNone(duracao)


class ThresholdsByRangeTests(unittest.TestCase):
    def test_every_range_has_thresholds_shaped_like_the_defaults(self):
        # get_merged_thresholds converte os personalizados pelo tipo do padrão:
        # um int virando float (ou o contrário) mudaria a conversão.
        for faixa in FAIXAS:
            with self.subTest(faixa=faixa):
                thresholds = thresholds_for_duracao(faixa)
                self.assertEqual(set(thresholds), set(DEFAULT_THRESHOLDS))
                for key, value in DEFAULT_THRESHOLDS.items():
                    self.assertIs(type(thresholds[key]), type(value), key)

    def test_speech_minimums_grow_with_the_range(self):
        for key in ("duration_fail_s", "duration_warn_s", "words_fail", "words_warn",
                    "min_vad_segments_warn"):
            with self.subTest(key=key):
                values = [thresholds_for_duracao(faixa)[key] for faixa in FAIXAS]
                self.assertEqual(values, sorted(values))
                self.assertLess(values[0], values[-1])

    def test_warning_comes_before_failure(self):
        for faixa in FAIXAS:
            with self.subTest(faixa=faixa):
                t = thresholds_for_duracao(faixa)
                self.assertLess(t["duration_fail_s"], t["duration_warn_s"])
                self.assertLess(t["words_fail"], t["words_warn"])
                self.assertLess(t["unintelligible_warn_pct"], t["unintelligible_fail_pct"])
                self.assertLess(t["wpm_low_warn"], t["wpm_high_warn"])
                self.assertGreaterEqual(t["min_vad_segments_warn"], 1)

    def test_short_audio_is_more_tolerant_than_long(self):
        curto = thresholds_for_duracao("ate_30s")
        longo = thresholds_for_duracao("acima_1h")

        self.assertGreater(curto["unintelligible_warn_pct"], longo["unintelligible_warn_pct"])
        self.assertGreater(curto["speaker_dominance_warn_pct"], longo["speaker_dominance_warn_pct"])
        self.assertLess(curto["wpm_low_warn"], longo["wpm_low_warn"])
        self.assertGreater(curto["wpm_high_warn"], longo["wpm_high_warn"])
        # Volume não depende da duração.
        self.assertEqual(curto["loudness_low_warn"], longo["loudness_low_warn"])

    def test_range_wins_over_the_type(self):
        for tipo in (ENTREVISTA_QUALITATIVA, PESQUISA_OPINIAO):
            with self.subTest(tipo=tipo):
                self.assertEqual(
                    thresholds_for_project({"tipo_projeto": tipo, "duracao_esperada": "10_30min"}),
                    thresholds_for_duracao("10_30min"),
                )

    def test_project_without_range_keeps_the_type_defaults(self):
        for duracao in (None, "", "desconhecida"):
            with self.subTest(duracao=duracao):
                self.assertIsNone(normalize_duracao_esperada(duracao))
                self.assertEqual(
                    thresholds_for_project({"duracao_esperada": duracao}), DEFAULT_THRESHOLDS
                )
                self.assertEqual(
                    default_thresholds(PESQUISA_OPINIAO, duracao),
                    DEFAULT_THRESHOLDS_PESQUISA_OPINIAO,
                )

    def test_custom_thresholds_override_the_range(self):
        thresholds = thresholds_for_project(
            {
                "duracao_esperada": "ate_30s",
                "quality_thresholds": json.dumps({"words_warn": 40}),
            }
        )

        self.assertEqual(thresholds["words_warn"], 40)
        self.assertEqual(
            thresholds["duration_fail_s"], thresholds_for_duracao("ate_30s")["duration_fail_s"]
        )


def _audio(n_segments, segment_s, words_per_segment, speakers=("Entrevistador", "Entrevistado")):
    """VAD contíguo com pausas de 1 s e uma transcrição alternando locutores."""
    starts = [i * (segment_s + 1.0) for i in range(n_segments)]
    vad = pd.DataFrame(
        {
            "start": starts,
            "end": [s + segment_s for s in starts],
            "duration": [segment_s] * n_segments,
        }
    )
    transcript = pd.DataFrame(
        {
            "SpeakerName": [speakers[i % len(speakers)] for i in range(n_segments)],
            "Text": [" ".join(["palavra"] * words_per_segment)] * n_segments,
            "word_count": [words_per_segment] * n_segments,
        }
    )
    return vad, transcript


def _statuses(vad, transcript, project):
    checks = run_quality_checks(
        vad, transcript, None, thresholds_for_project(project),
        tipo_projeto=project.get("tipo_projeto"),
    )
    return {check["id"]: check["status"] for check in checks}


class ChecksByRangeTests(unittest.TestCase):
    def test_a_good_voice_note_passes_in_its_range_and_fails_as_an_interview(self):
        # Recado de ~25 s: 6 segmentos de 3,5 s, 9 palavras cada (~150 WPM).
        vad, transcript = _audio(6, 3.5, 9, speakers=("Respondente",))
        recado = {"tipo_projeto": PESQUISA_OPINIAO, "duracao_esperada": "ate_30s"}
        entrevista = {"tipo_projeto": PESQUISA_OPINIAO, "duracao_esperada": "30_60min"}

        statuses = _statuses(vad, transcript, recado)
        for check in ("duration", "word_count", "segment_count", "speaking_rate"):
            self.assertEqual(statuses[check], "pass", check)

        statuses = _statuses(vad, transcript, entrevista)
        self.assertEqual(statuses["duration"], "fail")
        self.assertEqual(statuses["word_count"], "fail")
        self.assertEqual(statuses["segment_count"], "warn")

    def test_a_full_interview_passes_in_its_range(self):
        # Entrevista de ~40 min: 600 segmentos de 3 s, 8 palavras cada (160 WPM).
        vad, transcript = _audio(600, 3.0, 8)
        project = {"tipo_projeto": ENTREVISTA_QUALITATIVA, "duracao_esperada": "30_60min"}

        statuses = _statuses(vad, transcript, project)

        for check in ("duration", "word_count", "segment_count", "speaking_rate",
                      "silence_ratio", "speaker_balance"):
            self.assertEqual(statuses[check], "pass", check)

    def test_a_cut_interview_is_flagged_in_its_range(self):
        # 5 min de fala num projeto de entrevistas de 30 min a 1 h.
        vad, transcript = _audio(100, 3.0, 8)
        project = {"tipo_projeto": ENTREVISTA_QUALITATIVA, "duracao_esperada": "30_60min"}

        statuses = _statuses(vad, transcript, project)

        self.assertEqual(statuses["duration"], "fail")
        self.assertEqual(statuses["word_count"], "warn")
        self.assertEqual(statuses["segment_count"], "warn")


if __name__ == "__main__":
    unittest.main()
