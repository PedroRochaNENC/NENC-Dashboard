import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import call, patch

import httpx

from utils import auth
from utils import organization_data
from utils import prosodia_db
from utils import whatsapp_api_client


def _create_bootstrap_identity(database_path: Path):
    organization = auth.create_organization(
        "Organization One",
        database_path=database_path,
        _bootstrap=True,
    )
    platform_admin = auth.create_user(
        name="Platform Admin",
        email="platform@example.com",
        phone="5511999999999",
        organization_id=organization.id,
        password="platform-admin-password",
        module_keys=auth.MODULE_KEYS,
        is_organization_admin=True,
        is_platform_admin=True,
        database_path=database_path,
        _bootstrap=True,
    )
    return organization, platform_admin


class BootstrapConfigurationTests(unittest.TestCase):
    def test_incomplete_bootstrap_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            environment = {
                "NENC_BOOTSTRAP_ORGANIZATION": "Organization One",
                "NENC_BOOTSTRAP_NAME": "",
                "NENC_BOOTSTRAP_EMAIL": "",
                "NENC_BOOTSTRAP_PHONE": "",
                "NENC_BOOTSTRAP_PASSWORD": "",
            }
            with patch.dict(os.environ, environment):
                with self.assertRaises(auth.BootstrapConfigurationError):
                    auth.bootstrap_from_environment(database_path)

    def test_bootstrap_creates_one_global_administrator_only_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            environment = {
                "NENC_BOOTSTRAP_ORGANIZATION": "Organization One",
                "NENC_BOOTSTRAP_NAME": "Platform Admin",
                "NENC_BOOTSTRAP_EMAIL": "platform@example.com",
                "NENC_BOOTSTRAP_PHONE": "5511999999999",
                "NENC_BOOTSTRAP_PASSWORD": "platform-admin-password",
            }
            with patch.dict(os.environ, environment):
                self.assertTrue(auth.bootstrap_from_environment(database_path))
                self.assertFalse(auth.bootstrap_from_environment(database_path))

            user = auth.get_user_by_email("platform@example.com", database_path)

        self.assertIsNotNone(user)
        self.assertTrue(user.is_platform_admin)
        self.assertTrue(user.is_organization_admin)
        self.assertEqual(user.modules, tuple(sorted(auth.MODULE_KEYS)))


class AuthenticationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization, self.platform_admin = _create_bootstrap_identity(
            self.database_path
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_expired_and_revoked_sessions_cannot_load_a_user(self):
        expired_token = auth.create_session(
            self.platform_admin.id,
            database_path=self.database_path,
            duration=timedelta(seconds=-1),
        )
        self.assertIsNone(auth.user_for_session(expired_token, self.database_path))

        active_token = auth.create_session(
            self.platform_admin.id,
            database_path=self.database_path,
        )
        self.assertEqual(
            auth.user_for_session(active_token, self.database_path).id,
            self.platform_admin.id,
        )
        auth.revoke_session(active_token, self.database_path)
        self.assertIsNone(auth.user_for_session(active_token, self.database_path))

    def test_duplicate_and_inactive_users_cannot_authenticate(self):
        password = "regular-user-password"
        user = auth.create_user(
            name="Regular User",
            email="regular@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password=password,
            module_keys=("teste_sensorial",),
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        with self.assertRaises(ValueError):
            auth.create_user(
                name="Duplicate User",
                email="regular@example.com",
                phone="5511977777777",
                organization_id=self.organization.id,
                password="another-valid-password",
                actor=self.platform_admin,
                database_path=self.database_path,
            )

        auth.update_user(
            self.platform_admin,
            user.id,
            is_active=False,
            database_path=self.database_path,
        )
        with self.assertRaises(auth.AuthenticationError):
            auth.authenticate("regular@example.com", password, self.database_path)

    def test_regular_users_need_grants_and_last_admin_is_protected(self):
        regular_user = auth.create_user(
            name="Regular User",
            email="regular@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password="regular-user-password",
            module_keys=("teste_sensorial",),
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.assertTrue(auth.can_access_module(regular_user, "teste_sensorial"))
        self.assertFalse(auth.can_access_module(regular_user, "prosodia"))
        self.assertTrue(auth.can_access_module(self.platform_admin, "prosodia"))

        with self.assertRaises(ValueError):
            auth.update_user(
                self.platform_admin,
                self.platform_admin.id,
                is_organization_admin=False,
                database_path=self.database_path,
            )


class OrganizationIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization_one, self.platform_admin = _create_bootstrap_identity(
            self.database_path
        )
        self.organization_two = auth.create_organization(
            "Organization Two",
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.organization_two_user = auth.create_user(
            name="Organization Two User",
            email="organization-two@example.com",
            phone="5511977777777",
            organization_id=self.organization_two.id,
            password="organization-two-password",
            module_keys=auth.MODULE_KEYS,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        prosodia_db.init_db()
        # Estes casos exercitam o escopo de organizacao, nao o de papel: a
        # guarda de escrita depende da sessao Streamlit e e coberta em
        # WriteAuthorizationTests.
        for target, attribute in (
            (prosodia_db, "_require_write"),
            (auth, "assert_module_write"),
        ):
            guard = patch.object(target, attribute)
            guard.start()
            self.addCleanup(guard.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_prosodia_projects_and_audios_are_scoped_to_the_active_organization(self):
        with patch.object(prosodia_db, "_audit"):
            with patch.object(
                prosodia_db,
                "_active_organization_id",
                return_value=self.organization_one.id,
            ):
                project_one_id = prosodia_db.create_project("Organization One Project")

            with patch.object(
                prosodia_db,
                "_active_organization_id",
                return_value=self.organization_two.id,
            ):
                project_two_id = prosodia_db.create_project("Organization Two Project")

            with auth.connection(self.database_path) as database:
                cursor = database.execute(
                    """
                    INSERT INTO audios (organization_id, project_id, session_id)
                    VALUES (?, ?, ?)
                    """,
                    (self.organization_two.id, project_two_id, "organization-two-audio"),
                )
                organization_two_audio_id = cursor.lastrowid

            with patch.object(
                prosodia_db,
                "_active_organization_id",
                return_value=self.organization_one.id,
            ):
                self.assertEqual(
                    [project["id"] for project in prosodia_db.get_projects()],
                    [project_one_id],
                )
                self.assertIsNone(prosodia_db.get_project(project_two_id))
                self.assertEqual(prosodia_db.get_audios(project_two_id), [])
                self.assertIsNone(prosodia_db.get_audio(organization_two_audio_id))

    def test_module_state_and_vector_stores_are_not_shared_between_organizations(self):
        with patch.object(auth, "audit_business_access"):
            with patch.object(
                organization_data,
                "_access_context",
                return_value=(self.platform_admin, self.organization_one.id),
            ):
                organization_data.save_module_state(
                    "jornada_compra",
                    {"jc_projeto": {"nome": "Organization One"}},
                )
                organization_data.save_vector_store_id(
                    "jornada_compra",
                    "vs_organization_one",
                )

            with patch.object(
                organization_data,
                "_access_context",
                return_value=(self.organization_two_user, self.organization_two.id),
            ):
                self.assertEqual(
                    organization_data.load_module_state("jornada_compra"),
                    {},
                )
                self.assertIsNone(
                    organization_data.get_vector_store_id("jornada_compra")
                )
                organization_data.save_vector_store_id(
                    "jornada_compra",
                    "vs_organization_two",
                )

            with patch.object(
                organization_data,
                "_access_context",
                return_value=(self.platform_admin, self.organization_one.id),
            ):
                self.assertEqual(
                    organization_data.load_module_state("jornada_compra"),
                    {"jc_projeto": {"nome": "Organization One"}},
                )
                self.assertEqual(
                    organization_data.get_vector_store_id("jornada_compra"),
                    "vs_organization_one",
                )

    def test_external_resource_cannot_be_claimed_by_a_second_organization(self):
        with patch.object(auth, "audit_business_access"):
            with patch.object(
                organization_data,
                "_access_context",
                return_value=(self.platform_admin, self.organization_one.id),
            ):
                organization_data.claim_external_resource(
                    "whatsapp_contact",
                    "shared-contact",
                    created=True,
                )

            with patch.object(
                organization_data,
                "_access_context",
                return_value=(self.organization_two_user, self.organization_two.id),
            ):
                with self.assertRaises(auth.AuthorizationError):
                    organization_data.claim_external_resource(
                        "whatsapp_contact",
                        "shared-contact",
                    )

    def test_legacy_resource_migration_requires_the_declared_active_organization(self):
        with patch.object(
            auth,
            "require_admin",
            return_value=self.platform_admin,
        ), patch.object(
            organization_data,
            "_external_resource_context",
            return_value=("whatsapp_contact", "legacy-contact", self.organization_one.id),
        ), patch.dict(
            os.environ,
            {"NENC_LEGACY_ORGANIZATION_ID": str(self.organization_two.id)},
        ):
            with self.assertRaises(auth.AuthorizationError):
                organization_data.migrate_legacy_external_resource(
                    "whatsapp_contact",
                    "legacy-contact",
                )

    def test_legacy_resource_migration_records_an_explicit_created_claim(self):
        with patch.object(
            auth,
            "require_admin",
            return_value=self.platform_admin,
        ) as require_admin, patch.object(
            organization_data,
            "_external_resource_context",
            return_value=("whatsapp_contact", "legacy-contact", self.organization_one.id),
        ), patch.dict(
            os.environ,
            {"NENC_LEGACY_ORGANIZATION_ID": str(self.organization_one.id)},
        ), patch.object(
            organization_data,
            "claim_external_resource",
        ) as claim, patch.object(auth, "audit_business_access"):
            organization_data.migrate_legacy_external_resource(
                "whatsapp_contact",
                "legacy-contact",
                {"phone": "5511999999999"},
            )

        require_admin.assert_called_once_with(platform_only=True)
        claim.assert_called_once_with(
            "whatsapp_contact",
            "legacy-contact",
            {"phone": "5511999999999"},
            created=True,
        )


class WhatsAppClientAuthorizationTests(unittest.TestCase):
    def setUp(self):
        # A guarda de escrita e verificada em WriteAuthorizationTests; aqui o
        # alvo e a checagem de posse do recurso externo.
        write_guard = patch.object(whatsapp_api_client, "_require_write")
        write_guard.start()
        self.addCleanup(write_guard.stop)

    def test_owned_contact_list_filters_remote_results(self):
        owned_contact = {"id": "contact-1", "phone": "5511999999999"}
        foreign_contact = {"id": "contact-2", "phone": "5511988888888"}
        with patch.object(
            whatsapp_api_client,
            "_owned_resource_ids",
            return_value={"contact-1"},
        ), patch.object(
            whatsapp_api_client,
            "_list_contacts_from_api",
            return_value=[owned_contact, foreign_contact],
        ):
            contacts = whatsapp_api_client.list_owned_contacts(limit=25)

        self.assertEqual(contacts, [owned_contact])

    def test_new_contact_is_claimed_only_when_its_id_was_not_preexisting(self):
        created_contact = {
            "id": "contact-3",
            "phone": "5511999999999",
            "name": "New Contact",
        }
        with patch.object(
            whatsapp_api_client,
            "_list_contacts_from_api",
            return_value=[],
        ), patch.object(
            whatsapp_api_client,
            "_create_contact_from_api",
            return_value=created_contact,
        ), patch.object(
            whatsapp_api_client,
            "claim_external_resource",
        ) as claim:
            contact = whatsapp_api_client.create_owned_contact(
                "5511999999999",
                "New Contact",
            )

        self.assertEqual(contact, created_contact)
        self.assertEqual(claim.call_args.kwargs["created"], True)

    def test_contact_deletion_checks_ownership_then_releases_the_mapping(self):
        with patch.object(
            whatsapp_api_client,
            "_require_owned_resource",
        ) as require, patch.object(
            whatsapp_api_client,
            "_delete_contact_from_api",
        ) as delete, patch.object(
            whatsapp_api_client,
            "release_external_resource",
        ) as release:
            whatsapp_api_client.delete_owned_contact("contact-1")

        require.assert_called_once_with("whatsapp_contact", "contact-1")
        delete.assert_called_once_with("contact-1")
        release.assert_called_once_with("whatsapp_contact", "contact-1")

    def test_import_claims_only_contact_ids_absent_before_upload(self):
        old_contact = {"id": "contact-1", "phone": "5511999999999"}
        new_contact = {"id": "contact-2", "phone": "5511999999999"}
        with patch.object(
            whatsapp_api_client,
            "_list_contacts_from_api",
            side_effect=[[old_contact], [old_contact, new_contact]],
        ), patch.object(
            whatsapp_api_client,
            "_import_contacts_csv_from_api",
            return_value={"created": 1, "skipped": 1},
        ), patch.object(
            whatsapp_api_client,
            "claim_external_resource",
        ) as claim:
            result, claimed_count, unavailable_count = (
                whatsapp_api_client.import_owned_contacts_csv(
                    b"phone,name\n5511999999999,New Contact\n",
                    ["5511999999999"],
                )
            )

        self.assertEqual(result["created"], 1)
        self.assertEqual((claimed_count, unavailable_count), (1, 0))
        self.assertEqual(claim.call_args.kwargs["created"], True)
        self.assertEqual(claim.call_args.args[:2], ("whatsapp_contact", "contact-2"))

    def test_campaign_creation_rejects_unowned_contact_ids_before_request(self):
        with patch.object(
            whatsapp_api_client,
            "_owned_resource_ids",
            return_value={"contact-1"},
        ), patch.object(whatsapp_api_client, "_create_campaign_from_api") as create:
            with self.assertRaises(auth.AuthorizationError):
                whatsapp_api_client.create_owned_campaign(
                    name="Campaign",
                    template_name="template",
                    language_code="pt_BR",
                    contact_ids=["contact-1", "contact-2"],
                )

        create.assert_not_called()

    def test_owned_jobs_reader_filters_and_registers_jobs(self):
        owned_job = {"id": "job-1", "audio_id": "audio-1", "status": "done"}
        foreign_job = {"id": "job-2", "audio_id": "audio-2", "status": "done"}
        with patch.object(
            whatsapp_api_client,
            "_owned_resource_ids",
            return_value={"audio-1"},
        ), patch.object(
            whatsapp_api_client,
            "_list_jobs_from_api",
            return_value=[owned_job, foreign_job],
        ), patch.object(
            whatsapp_api_client,
            "register_derived_external_resource",
        ) as register:
            jobs = whatsapp_api_client.list_owned_jobs(status="done", limit=25)

        self.assertEqual(jobs, [owned_job])
        register.assert_called_once_with(
            "whatsapp_job",
            "job-1",
            "whatsapp_audio",
            "audio-1",
        )

    def test_owned_jobs_reader_skips_the_remote_call_without_owned_audio(self):
        with patch.object(
            whatsapp_api_client,
            "_owned_resource_ids",
            return_value=set(),
        ), patch.object(whatsapp_api_client, "_list_jobs_from_api") as list_jobs:
            jobs = whatsapp_api_client.list_jobs()

        self.assertEqual(jobs, [])
        list_jobs.assert_not_called()

    def test_public_jobs_reader_delegates_to_owned_reader(self):
        expected_jobs = [{"id": "job-1", "audio_id": "audio-1"}]
        with patch.object(
            whatsapp_api_client,
            "list_owned_jobs",
            return_value=expected_jobs,
        ) as list_owned_jobs:
            jobs = whatsapp_api_client.list_jobs(
                audio_id=10,
                status="done",
                skip=5,
                limit=25,
            )

        self.assertEqual(jobs, expected_jobs)
        list_owned_jobs.assert_called_once_with(
            audio_id=10,
            status="done",
            skip=5,
            limit=25,
        )

    def test_individual_job_and_reprocess_require_owned_resources(self):
        with patch.object(
            whatsapp_api_client, "_require_owned_resource"
        ) as require_owned, patch.object(
            whatsapp_api_client, "_client"
        ) as client:
            whatsapp_api_client.get_job(12)
            whatsapp_api_client.reprocess_audio(34)

        require_owned.assert_has_calls(
            [
                call("whatsapp_job", 12),
                call("whatsapp_audio", 34),
            ]
        )
        client.assert_called()

    def test_audio_file_hook_requires_a_registered_external_audio(self):
        request = httpx.Request(
            "GET",
            "https://example.invalid/api/audios/123/file",
        )
        with patch.object(whatsapp_api_client, "_require_owned_resource") as require:
            whatsapp_api_client._authorize_audio_file_request(request)

        require.assert_called_once_with("whatsapp_audio", "123")

    def test_audio_file_hook_ignores_non_file_endpoints(self):
        request = httpx.Request(
            "GET",
            "https://example.invalid/api/audios/123/status",
        )
        with patch.object(whatsapp_api_client, "_require_owned_resource") as require:
            whatsapp_api_client._authorize_audio_file_request(request)

        require.assert_not_called()


if __name__ == "__main__":
    unittest.main()