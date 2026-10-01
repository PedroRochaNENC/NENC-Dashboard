"""Evento do servidor sem conta logada: fica no audit_log com autor vazio."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from utils import auth


class SystemEventTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One", database_path=self.database_path, _bootstrap=True
        )

    def test_the_event_is_recorded_without_an_actor(self):
        auth.audit_system_event(
            self.organization.id, "jornada.import.received", "jc_import_batch", 7,
            {"origem": "api", "arquivos": 3}, database_path=self.database_path,
        )
        with closing(sqlite3.connect(self.database_path)) as database:
            row = database.execute(
                "SELECT organization_id, actor_user_id, action, target_type, target_id, metadata_json "
                "FROM audit_log WHERE action = 'jornada.import.received'"
            ).fetchone()
        self.assertEqual(row[0], self.organization.id)
        self.assertIsNone(row[1])
        self.assertEqual(row[2:5], ("jornada.import.received", "jc_import_batch", "7"))
        self.assertIn('"origem": "api"', row[5])

    def test_secret_metadata_is_dropped(self):
        auth.audit_system_event(
            self.organization.id, "jornada.import.received", "jc_import_batch", 8,
            {"token": "nao-gravar", "arquivos": 1}, database_path=self.database_path,
        )
        with closing(sqlite3.connect(self.database_path)) as database:
            metadata = database.execute(
                "SELECT metadata_json FROM audit_log WHERE target_id = '8'"
            ).fetchone()[0]
        self.assertNotIn("nao-gravar", metadata)


if __name__ == "__main__":
    unittest.main()
