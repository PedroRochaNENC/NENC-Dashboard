import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from utils import auth


APP_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_SCRIPT = APP_ROOT / "scripts" / "migrate_legacy_data.py"


class LegacyMigrationToolTests(unittest.TestCase):
    def test_dry_run_and_apply_assign_legacy_projects_to_the_declared_organization(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            database_path = temporary_path / "nenc-insights.db"
            backup_directory = temporary_path / "backups"

            organization = auth.create_organization(
                "Legacy Owner", database_path=database_path, _bootstrap=True
            )
            database = sqlite3.connect(database_path)
            try:
                database.execute(
                    "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                database.execute(
                    "INSERT INTO projects (name) VALUES ('Legacy Project')"
                )
                database.commit()
            finally:
                database.close()

            command = [
                sys.executable,
                str(MIGRATION_SCRIPT),
                "--database",
                str(database_path),
                "--organization-id",
                str(organization.id),
            ]
            dry_run = subprocess.run(
                command, cwd=APP_ROOT, capture_output=True, text=True, check=False
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertIn("Dry-run complete", dry_run.stdout)

            migration = subprocess.run(
                [*command, "--backup-dir", str(backup_directory), "--apply"],
                cwd=APP_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(migration.returncode, 0, migration.stderr)
            self.assertIn("Migration completed and verified", migration.stdout)
            self.assertEqual(len(list(backup_directory.glob("*.db"))), 1)

            database = sqlite3.connect(database_path)
            try:
                organization_id = database.execute(
                    "SELECT organization_id FROM projects WHERE id = 1"
                ).fetchone()[0]
            finally:
                database.close()

        self.assertEqual(organization_id, organization.id)