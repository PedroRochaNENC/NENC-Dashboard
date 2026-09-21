"""Tipo de projeto: persistência, limiares por tipo e escolha do prompt.

O tipo decide o prompt da IA e os limiares de qualidade. O ponto frágil é o
update_project: ele regrava todas as colunas, e as telas de texto do QR, de
vínculo com a API e de campanha o chamam sem conhecer o tipo.
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
from utils.prosodia_project_types import ENTREVISTA_QUALITATIVA, PESQUISA_OPINIAO
from utils.prosodia_prompts import (
    ADENDO_PESQUISA_OPINIAO,
    PROMPT_ENTREVISTA,
    PROMPT_PESQUISA_OPINIAO,
    PROSODIA_PROJECT_SYSTEM_PROMPT_ENTREVISTA,
    PROSODIA_PROJECT_SYSTEM_PROMPT_PESQUISA_OPINIAO,
    PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL,
    PROSODIA_PROJECT_SYSTEM_PROMPT_STRATEGIC,
    PROSODIA_SYSTEM_PROMPT_STATISTICAL,
    PROSODIA_SYSTEM_PROMPT_STRATEGIC,
    get_prosodia_project_system_prompt,
    get_prosodia_system_prompt,
)
from utils.prosodia_quality import (
    DEFAULT_THRESHOLDS,
    DEFAULT_THRESHOLDS_PESQUISA_OPINIAO,
    compute_overall_status,
    run_quality_checks,
    thresholds_for_project,
)


class ProjectTypePersistenceTests(unittest.TestCase):
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

    def test_new_project_is_an_interview_by_default(self):
        project_id = prosodia_db.create_project("Projeto")

        self.assertEqual(
            prosodia_db.get_project(project_id)["tipo_projeto"], ENTREVISTA_QUALITATIVA
        )

    def test_opinion_survey_type_is_saved_and_listed(self):
        project_id = prosodia_db.create_project(
            "Feedback da loja", tipo_projeto=PESQUISA_OPINIAO
        )

        self.assertEqual(
            prosodia_db.get_project(project_id)["tipo_projeto"], PESQUISA_OPINIAO
        )
        listed = {p["id"]: p for p in prosodia_db.get_projects()}
        self.assertEqual(listed[project_id]["tipo_projeto"], PESQUISA_OPINIAO)

    def test_update_without_type_keeps_the_saved_type(self):
        # Como projetos.py e whatsapp_campanhas.py: regravam o projeto
        # repassando os proprios campos, sem o tipo.
        project_id = prosodia_db.create_project(
            "Feedback da loja", tipo_projeto=PESQUISA_OPINIAO
        )

        prosodia_db.update_project(
            project_id, name="Feedback da loja", qr_verification_text="Novo texto"
        )

        project = prosodia_db.get_project(project_id)
        self.assertEqual(project["tipo_projeto"], PESQUISA_OPINIAO)
        self.assertEqual(project["qr_verification_text"], "Novo texto")

    def test_update_with_type_changes_it(self):
        project_id = prosodia_db.create_project(
            "Feedback da loja", tipo_projeto=PESQUISA_OPINIAO
        )

        prosodia_db.update_project(
            project_id, name="Feedback da loja", tipo_projeto=ENTREVISTA_QUALITATIVA
        )

        self.assertEqual(
            prosodia_db.get_project(project_id)["tipo_projeto"], ENTREVISTA_QUALITATIVA
        )

    def test_unknown_type_is_refused(self):
        with self.assertRaises(ValueError):
            prosodia_db.create_project("Projeto", tipo_projeto="Depoimento")

        project_id = prosodia_db.create_project("Projeto", tipo_projeto=PESQUISA_OPINIAO)
        with self.assertRaises(ValueError):
            prosodia_db.update_project(project_id, name="Projeto", tipo_projeto="Entrevista")
        self.assertEqual(
            prosodia_db.get_project(project_id)["tipo_projeto"], PESQUISA_OPINIAO
        )


class LegacyDatabaseMigrationTests(unittest.TestCase):
    def test_existing_projects_become_interviews(self):
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
                tipo = database.execute("SELECT tipo_projeto FROM projects").fetchone()[0]
            finally:
                database.close()

        self.assertEqual(tipo, ENTREVISTA_QUALITATIVA)


def _short_monologue():
    """Recado de 8 s e 12 palavras de um único respondente, como os do WhatsApp."""
    vad = pd.DataFrame(
        {"start": [0.0, 3.5], "end": [3.0, 8.5], "duration": [3.0, 5.0]}
    )
    transcript = pd.DataFrame(
        {
            "SpeakerName": ["Entrevistado", "Entrevistado"],
            "Text": [
                "Gostei muito do atendimento da loja",
                "mas a entrega atrasou dois dias",
            ],
            "word_count": [6, 6],
        }
    )
    return vad, transcript


def _statuses(checks):
    return {check["id"]: check["status"] for check in checks}


class QualityThresholdsByTypeTests(unittest.TestCase):
    def test_default_thresholds_depend_on_the_type(self):
        self.assertEqual(thresholds_for_project({}), DEFAULT_THRESHOLDS)
        self.assertEqual(thresholds_for_project(None), DEFAULT_THRESHOLDS)
        self.assertEqual(
            thresholds_for_project(
                {"tipo_projeto": PESQUISA_OPINIAO, "quality_thresholds": None}
            ),
            DEFAULT_THRESHOLDS_PESQUISA_OPINIAO,
        )

    def test_custom_thresholds_override_the_type_defaults(self):
        thresholds = thresholds_for_project(
            {
                "tipo_projeto": PESQUISA_OPINIAO,
                "quality_thresholds": json.dumps({"words_warn": 40}),
            }
        )

        self.assertEqual(thresholds["words_warn"], 40)
        self.assertEqual(
            thresholds["duration_fail_s"],
            DEFAULT_THRESHOLDS_PESQUISA_OPINIAO["duration_fail_s"],
        )

    def test_invalid_saved_thresholds_fall_back_to_the_type_defaults(self):
        for saved in ("{", "[1, 2]"):
            with self.subTest(saved=saved):
                self.assertEqual(
                    thresholds_for_project(
                        {"tipo_projeto": PESQUISA_OPINIAO, "quality_thresholds": saved}
                    ),
                    DEFAULT_THRESHOLDS_PESQUISA_OPINIAO,
                )

    def test_short_monologue_is_a_problem_for_an_interview(self):
        vad, transcript = _short_monologue()
        project = {"tipo_projeto": ENTREVISTA_QUALITATIVA}

        checks = run_quality_checks(
            vad, transcript, None, thresholds_for_project(project),
            tipo_projeto=project["tipo_projeto"],
        )

        statuses = _statuses(checks)
        self.assertEqual(statuses["duration"], "fail")
        self.assertEqual(statuses["speaker_balance"], "warn")
        self.assertEqual(compute_overall_status(checks), "fail")

    def test_short_monologue_only_warns_in_an_opinion_survey(self):
        vad, transcript = _short_monologue()
        project = {"tipo_projeto": PESQUISA_OPINIAO}

        checks = run_quality_checks(
            vad, transcript, None, thresholds_for_project(project),
            tipo_projeto=project["tipo_projeto"],
        )

        statuses = _statuses(checks)
        self.assertEqual(statuses["duration"], "warn")
        self.assertEqual(statuses["word_count"], "warn")
        # Um só respondente é o esperado: o equilíbrio entre locutores não é checado.
        self.assertNotIn("speaker_balance", statuses)
        self.assertEqual(compute_overall_status(checks), "warn")


class PromptByTypeTests(unittest.TestCase):
    def test_interview_and_unknown_types_keep_the_current_prompts(self):
        for project_type in (None, "", ENTREVISTA_QUALITATIVA, "Depoimento"):
            with self.subTest(project_type=project_type):
                self.assertIs(get_prosodia_system_prompt(project_type), PROMPT_ENTREVISTA)
                self.assertIs(
                    get_prosodia_system_prompt(project_type, "estatistica"),
                    PROSODIA_SYSTEM_PROMPT_STATISTICAL,
                )
                self.assertIs(
                    get_prosodia_system_prompt(project_type, "estrategica"),
                    PROSODIA_SYSTEM_PROMPT_STRATEGIC,
                )
                self.assertIs(
                    get_prosodia_project_system_prompt(project_type),
                    PROSODIA_PROJECT_SYSTEM_PROMPT_ENTREVISTA,
                )
                self.assertIs(
                    get_prosodia_project_system_prompt(project_type, "estatistica"),
                    PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL,
                )
                self.assertIs(
                    get_prosodia_project_system_prompt(project_type, "estrategica"),
                    PROSODIA_PROJECT_SYSTEM_PROMPT_STRATEGIC,
                )

    def test_opinion_survey_has_its_own_quick_prompts(self):
        self.assertIs(get_prosodia_system_prompt(PESQUISA_OPINIAO), PROMPT_PESQUISA_OPINIAO)
        self.assertIs(
            get_prosodia_project_system_prompt(PESQUISA_OPINIAO),
            PROSODIA_PROJECT_SYSTEM_PROMPT_PESQUISA_OPINIAO,
        )
        self.assertNotIn("Neutralize o entrevistador", PROMPT_PESQUISA_OPINIAO)

    def test_two_step_modes_get_the_opinion_survey_addendum(self):
        self.assertEqual(
            get_prosodia_system_prompt(PESQUISA_OPINIAO, "estatistica"),
            PROSODIA_SYSTEM_PROMPT_STATISTICAL + ADENDO_PESQUISA_OPINIAO,
        )
        self.assertEqual(
            get_prosodia_project_system_prompt(PESQUISA_OPINIAO, "estrategica"),
            PROSODIA_PROJECT_SYSTEM_PROMPT_STRATEGIC + ADENDO_PESQUISA_OPINIAO,
        )

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            get_prosodia_system_prompt(PESQUISA_OPINIAO, "estrategico")


if __name__ == "__main__":
    unittest.main()
