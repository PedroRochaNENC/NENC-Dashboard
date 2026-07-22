import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import auth
from utils import organization_data
from utils import prosodia_db
from utils import whatsapp_api_client


class AuthSecurityTests(unittest.TestCase):
    def test_scrypt_hash_verifies_only_the_original_password(self):
        password = "correct-horse-battery-staple"

        password_hash = auth.hash_password(password)

        self.assertTrue(password_hash.startswith("scrypt$"))
        self.assertTrue(auth.verify_password(password, password_hash))
        self.assertFalse(auth.verify_password("wrong-password", password_hash))

    def test_password_policy_rejects_short_passwords(self):
        with self.assertRaises(ValueError):
            auth.hash_password("too-short")

    def test_auth_and_prosodia_initialize_the_configured_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            previous_database_path = os.environ.get("NENC_DB_PATH")
            os.environ["NENC_DB_PATH"] = str(database_path)
            try:
                auth.initialize_auth_schema()
                prosodia_db.init_db()
            finally:
                if previous_database_path is None:
                    os.environ.pop("NENC_DB_PATH", None)
                else:
                    os.environ["NENC_DB_PATH"] = previous_database_path

            database = sqlite3.connect(database_path)
            try:
                table_names = {
                    row[0]
                    for row in database.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                database.close()

        self.assertIn("organizations", table_names)
        self.assertIn("auth_sessions", table_names)
        self.assertIn("projects", table_names)
        self.assertIn("audios", table_names)

    def test_external_resource_claim_requires_an_explicit_creation_flow(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            previous_database_path = os.environ.get("NENC_DB_PATH")
            os.environ["NENC_DB_PATH"] = str(database_path)
            try:
                auth.initialize_auth_schema()
                with auth.connection() as database:
                    cursor = database.execute(
                        """
                        INSERT INTO organizations (name, is_active, created_at, updated_at)
                        VALUES (?, 1, ?, ?)
                        """,
                        ("Organization One", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
                    )
                    organization_id = cursor.lastrowid

                user = auth.User(
                    id=1,
                    name="Test User",
                    email="test@example.com",
                    phone="5511999999999",
                    organization_id=organization_id,
                    organization_name="Organization One",
                    is_organization_admin=False,
                    is_platform_admin=False,
                    is_active=True,
                    modules=("prosodia",),
                )
                with patch.object(
                    organization_data,
                    "_access_context",
                    return_value=(user, organization_id),
                ), patch.object(auth, "audit_business_access"):
                    with self.assertRaises(auth.AuthorizationError):
                        organization_data.claim_external_resource("whatsapp_contact", "123")

                    organization_data.claim_external_resource(
                        "whatsapp_contact",
                        "123",
                        {"phone": "5511999999999"},
                        created=True,
                    )

                with auth.connection() as database:
                    resource = database.execute(
                        """
                        SELECT organization_id
                        FROM organization_external_resources
                        WHERE resource_type = ? AND resource_id = ?
                        """,
                        ("whatsapp_contact", "123"),
                    ).fetchone()
            finally:
                if previous_database_path is None:
                    os.environ.pop("NENC_DB_PATH", None)
                else:
                    os.environ["NENC_DB_PATH"] = previous_database_path

        self.assertEqual(resource["organization_id"], organization_id)

    def test_whatsapp_audio_listing_rejects_an_unscoped_request(self):
        with self.assertRaises(auth.AuthorizationError):
            whatsapp_api_client.get_all_audios()


if __name__ == "__main__":
    unittest.main()