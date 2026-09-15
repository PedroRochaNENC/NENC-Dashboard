"""QR Code das entrevistas: gravado na importacao e lido pela tabela.

A coluna existiu, sumiu num refactor e o banco de producao ficou com
qr_code_name vazio em todas as entrevistas. Estes testes seguram as tres pontas:
a migracao cria a coluna, create_audio grava e a leitura da tabela devolve.
"""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from utils import auth
from utils import prosodia_db


class InterviewQrCodeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        prosodia_db.init_db()
        # O alvo aqui e o dado gravado, nao papel nem auditoria: as guardas de
        # escrita e o audit_log dependem da sessao Streamlit.
        for attribute, kwargs in (
            ("_require_write", {}),
            ("_audit", {}),
            ("_active_organization_id", {"return_value": self.organization.id}),
        ):
            guard = patch.object(prosodia_db, attribute, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        self.project_id = prosodia_db.create_project("Projeto com QR")

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _audio_columns(self):
        # `with sqlite3.connect()` so encerra a transacao; sem fechar, o Windows
        # mantem o arquivo travado e o tearDown nao consegue apagar o diretorio.
        with closing(sqlite3.connect(self.database_path)) as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(audios)")}

    def _interview(self, audio_id):
        rows = prosodia_db.get_audios_for_interviews(self.project_id)
        return next(row for row in rows if row["id"] == audio_id)

    def test_new_database_has_the_column(self):
        self.assertIn("qr_code_name", self._audio_columns())

    def test_migration_adds_the_column_to_a_database_created_without_it(self):
        """O banco local de desenvolvimento nasceu depois do refactor, sem a coluna."""
        with closing(sqlite3.connect(self.database_path)) as conn:
            conn.execute("ALTER TABLE audios DROP COLUMN qr_code_name")
            conn.commit()
        self.assertNotIn("qr_code_name", self._audio_columns())

        prosodia_db.init_db()

        self.assertIn("qr_code_name", self._audio_columns())

    def test_imported_interview_keeps_its_qr_code(self):
        imported_id = prosodia_db.create_audio(
            self.project_id,
            "wa_+5511999999999_7",
            whatsapp_message_id="MSG-7",
            qr_code_name="Banner Homs",
        )
        uploaded_id = prosodia_db.create_audio(self.project_id, "entrevista-manual")

        self.assertEqual(self._interview(imported_id)["qr_code_name"], "Banner Homs")
        self.assertIsNone(self._interview(uploaded_id)["qr_code_name"])

    def test_empty_qr_code_is_stored_as_null(self):
        audio_id = prosodia_db.create_audio(self.project_id, "wa_x_2", qr_code_name="")

        self.assertIsNone(self._interview(audio_id)["qr_code_name"])

    def test_all_organizations_mode_also_reads_the_qr_code(self):
        audio_id = prosodia_db.create_audio(
            self.project_id, "wa_x_1", qr_code_name="Cartaz A"
        )

        with patch.object(prosodia_db, "_active_organization_id", return_value=0):
            self.assertEqual(self._interview(audio_id)["qr_code_name"], "Cartaz A")


if __name__ == "__main__":
    unittest.main()
