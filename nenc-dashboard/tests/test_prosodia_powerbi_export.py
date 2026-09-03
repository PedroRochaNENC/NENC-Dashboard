import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils import auth
from utils import organization_data
from utils import prosodia_db
from utils.prosodia_powerbi_export import export_project_to_powerbi_excel


class ProsodiaPowerBiExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()

        self.organization_one = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.platform_admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=self.organization_one.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.organization_two = auth.create_organization(
            "Organization Two",
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.active_organization = patch.object(
            prosodia_db,
            "_active_organization_id",
            return_value=self.organization_one.id,
        )
        self.active_organization.start()
        self.audit = patch.object(prosodia_db, "_audit")
        self.audit.start()
        # A guarda de escrita nao existia quando estes testes foram escritos:
        # o setUp semeia dados chamando prosodia_db direto, sem sessao. Como o
        # arquivo ja substitui a organizacao ativa e a auditoria, a guarda
        # entra na mesma lista. A autorizacao em si e coberta por
        # test_role_based_access.py.
        self.write_guard = patch.object(
            prosodia_db, "_require_write", return_value=self.platform_admin
        )
        self.write_guard.start()
        prosodia_db.init_db()

    def tearDown(self):
        self.write_guard.stop()
        self.audit.stop()
        self.active_organization.stop()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _create_populated_project(self):
        with patch.object(
            organization_data,
            "_access_context",
            return_value=(self.platform_admin, self.organization_one.id),
        ), patch.object(auth, "audit_business_access"):
            organization_data.claim_external_resource(
                "whatsapp_campaign",
                42,
                created=True,
            )
            organization_data.claim_external_resource(
                "whatsapp_api_project",
                84,
                created=True,
            )
            project_id = prosodia_db.create_project(
                name="Pesquisa de Voz",
                especialidade="Atendimento hospitalar",
                historico="Histórico do estudo",
                problemas="Melhorar a experiência",
                questions="Como foi o atendimento?\nO que deve melhorar?",
                entities="Hospital",
                briefing_filename="briefing.txt",
                briefing_text="Contexto completo do briefing.",
                whatsapp_campaign_id=42,
                api_project_id=84,
                quality_thresholds='{"words_warn": 100}',
            )
        audio_id = prosodia_db.create_audio(
            project_id=project_id,
            session_id="entrevista-001",
            prosodia_json=(
                b'{"result":{"vad":[{"start":0.0,"end":1.5},'
                b'{"start":2.0,"end":3.0}]}}'
            ),
            transcricao_csv=(
                b"SpeakerName,Timestamp,Text,source_label\n"
                b"Ana,00:00:01,Atendimento muito bom,original\n"
            ),
            sincronizado_csv=(
                b"speakers,timestamp_inicio,texto_transcricao,start_s,end_s,"
                b"pitch,dim_arousal\n"
                b"Ana,00:00:01,Atendimento muito bom,0.0,1.5,110.0,0.7\n"
            ),
            whatsapp_message_id="message-123",
        )
        prosodia_db.update_audio_openai_ids(
            audio_id,
            "file-prosodia",
            "file-transcricao",
        )
        first_analysis_id = prosodia_db.save_analysis(
            audio_id,
            "gpt-4.1-mini",
            "Primeira análise.",
            citations=[{"filename": "base.md", "quote": "Trecho inicial"}],
        )
        prosodia_db.save_analysis(
            audio_id,
            "gpt-4.1",
            "Segunda análise.",
            citations=[
                {
                    "filename": "base-2.md",
                    "quote": "Trecho final",
                    "topic": "Atendimento",
                    "timestamp": "00:00:01",
                    "speaker": "Ana",
                    "line_ref": "L3",
                    "justification": "Evidência direta",
                }
            ],
        )
        project_analysis_id = prosodia_db.save_project_analysis(
            project_id,
            "gpt-4.1",
            "Análise consolidada.",
            citations=[{"filename": "relatorio.md", "quote": "Conclusão"}],
        )
        prosodia_db.save_quality_check(
            audio_id,
            "warn",
            [
                {
                    "id": "duration",
                    "label": "Duração de fala",
                    "category": "temporal",
                    "status": "warn",
                    "detail": "Duração abaixo do recomendado.",
                    "value": 3.0,
                }
            ],
            [
                {
                    "question": "Como foi o atendimento?",
                    "covered_ai": True,
                    "covered_keywords": True,
                    "confidence": 0.9,
                    "evidence": "Atendimento muito bom",
                    "timestamp": "00:00:01",
                }
            ],
        )
        prosodia_db.save_quality_check(
            audio_id,
            "pass",
            [
                {
                    "id": "word_count",
                    "label": "Contagem de palavras",
                    "status": "pass",
                    "detail": "Quantidade suficiente.",
                }
            ],
            [
                {
                    "question": "O que deve melhorar?",
                    "covered_ai": False,
                    "covered_keywords": False,
                    "confidence": 0.0,
                    "evidence": "",
                }
            ],
        )
        prosodia_db.save_high_activations(
            audio_id,
            [
                {
                    "Timestamp": "00:00:01",
                    "SpeakerName": "Ana",
                    "Text": "Atendimento muito bom",
                    "dim_arousal": 0.7,
                    "topic": "Atendimento",
                }
            ],
        )
        prosodia_db.save_high_activations(
            audio_id,
            [
                {
                    "timestamp": "00:00:02",
                    "timestamp_end": "00:00:03",
                    "speaker": "Ana",
                    "text": "Precisa melhorar o retorno",
                    "score": 0.8,
                    "reason": "Arousal elevado",
                }
            ],
        )
        return project_id, audio_id, first_analysis_id, project_analysis_id

    def _sheets(self, workbook: bytes):
        return pd.ExcelFile(io.BytesIO(workbook))

    def test_export_contains_related_tables_and_full_history(self):
        project_id, audio_id, first_analysis_id, project_analysis_id = (
            self._create_populated_project()
        )

        workbook, filename = export_project_to_powerbi_excel(project_id)
        sheets = self._sheets(workbook)

        self.assertEqual(
            set(sheets.sheet_names),
            {
                "Projeto",
                "Perguntas_Projeto",
                "Entrevistas",
                "Segmentos_VAD",
                "Transcricoes",
                "Dados_Sincronizados",
                "Analises_Entrevista",
                "Citacoes_Analise_Entrevista",
                "Analises_Projeto",
                "Citacoes_Analise_Projeto",
                "Verificacoes_Qualidade",
                "Checks_Qualidade",
                "Cobertura_Perguntas",
                "Momentos_Alta_Ativacao",
                "Dados_Brutos_Entrevistas",
                "Chunks_Dados_Brutos_Entrevistas",
            },
        )
        self.assertEqual(filename, f"nenclex_powerbi_Pesquisa_de_Voz_{project_id}.xlsx")

        project = pd.read_excel(sheets, "Projeto")
        interviews = pd.read_excel(sheets, "Entrevistas")
        vad = pd.read_excel(sheets, "Segmentos_VAD")
        transcripts = pd.read_excel(sheets, "Transcricoes")
        synchronized = pd.read_excel(sheets, "Dados_Sincronizados")
        analyses = pd.read_excel(sheets, "Analises_Entrevista")
        interview_citations = pd.read_excel(sheets, "Citacoes_Analise_Entrevista")
        project_analyses = pd.read_excel(sheets, "Analises_Projeto")
        project_citations = pd.read_excel(sheets, "Citacoes_Analise_Projeto")
        qualities = pd.read_excel(sheets, "Verificacoes_Qualidade")
        checks = pd.read_excel(sheets, "Checks_Qualidade")
        coverage = pd.read_excel(sheets, "Cobertura_Perguntas")
        activations = pd.read_excel(sheets, "Momentos_Alta_Ativacao")
        raw_artifacts = pd.read_excel(sheets, "Dados_Brutos_Entrevistas")
        raw_artifact_chunks = pd.read_excel(sheets, "Chunks_Dados_Brutos_Entrevistas")

        self.assertEqual(project.loc[0, "id"], project_id)
        self.assertEqual(project.loc[0, "briefing_text"], "Contexto completo do briefing.")
        self.assertEqual(interviews.loc[0, "id"], audio_id)
        self.assertEqual(interviews.loc[0, "whatsapp_message_id"], "message-123")
        self.assertEqual(interviews.loc[0, "duration_seconds"], 3.0)
        self.assertEqual(interviews.loc[0, "quality_status"], "pass")
        self.assertEqual(interviews.loc[0, "n_analyses"], 2)
        self.assertTrue((vad["audio_id"] == audio_id).all())
        self.assertEqual(len(vad), 2)
        self.assertEqual(transcripts.loc[0, "Text"], "Atendimento muito bom")
        self.assertEqual(synchronized.loc[0, "pitch"], 110.0)

        self.assertEqual(len(analyses), 2)
        self.assertIn(first_analysis_id, analyses["id"].tolist())
        self.assertEqual(len(interview_citations), 2)
        self.assertTrue(
            interview_citations["analysis_id"].isin(analyses["id"]).all()
        )
        self.assertEqual(project_analyses.loc[0, "id"], project_analysis_id)
        self.assertEqual(project_citations.loc[0, "project_analysis_id"], project_analysis_id)

        self.assertEqual(len(qualities), 2)
        self.assertTrue(checks["quality_check_id"].isin(qualities["id"]).all())
        self.assertTrue(
            coverage["quality_check_id"].isin(qualities["id"]).all()
        )
        self.assertEqual(len(activations), 2)
        self.assertTrue((activations["audio_id"] == audio_id).all())
        self.assertEqual(len(raw_artifacts), 3)
        self.assertEqual(len(raw_artifact_chunks), 3)
        self.assertIn("transcricao_csv", raw_artifacts["artifact_type"].tolist())
        self.assertTrue(
            raw_artifact_chunks["chunk_text"].astype(str).str.contains("Atendimento muito bom").any()
        )

    def test_export_of_empty_project_keeps_all_table_schemas(self):
        project_id = prosodia_db.create_project(name="Projeto sem entrevistas")

        workbook, _ = export_project_to_powerbi_excel(project_id)
        sheets = self._sheets(workbook)

        interviews = pd.read_excel(sheets, "Entrevistas")
        vad = pd.read_excel(sheets, "Segmentos_VAD")
        self.assertTrue(interviews.empty)
        self.assertTrue(vad.empty)
        self.assertEqual(
            list(vad.columns),
            ["audio_id", "project_id", "session_id", "start", "end", "duration"],
        )

    def test_export_rejects_project_from_another_organization(self):
        with patch.object(
            prosodia_db,
            "_active_organization_id",
            return_value=self.organization_two.id,
        ):
            foreign_project_id = prosodia_db.create_project("Projeto externo")

        with self.assertRaisesRegex(
            ValueError,
            "Projeto não encontrado para a organização ativa",
        ):
            export_project_to_powerbi_excel(foreign_project_id)

    def test_export_splits_large_tables_without_dropping_rows(self):
        project_id, _, _, _ = self._create_populated_project()

        workbook, _ = export_project_to_powerbi_excel(
            project_id,
            max_rows_per_sheet=1,
        )
        sheets = self._sheets(workbook)

        self.assertIn("Segmentos_VAD_2", sheets.sheet_names)
        first_page = pd.read_excel(sheets, "Segmentos_VAD")
        second_page = pd.read_excel(sheets, "Segmentos_VAD_2")
        self.assertEqual(len(first_page) + len(second_page), 2)


if __name__ == "__main__":
    unittest.main()
