"""Matriz de papeis x verbos: quem le, quem escreve e quem gerencia contas.

O isolamento por organizacao ja e coberto em test_organization_isolation.py.
Aqui o alvo e a outra dimensao: conta comum e somente leitura, e um
administrador de organizacao nao atua sobre a conta de outro administrador.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import auth
from utils import prosodia_db


def _user(
    user_id: int = 1,
    *,
    is_organization_admin: bool = False,
    is_platform_admin: bool = False,
    modules=("prosodia",),
) -> auth.User:
    return auth.User(
        id=user_id,
        name="Conta {}".format(user_id),
        email="conta{}@example.com".format(user_id),
        phone="5511999999999",
        organization_id=1,
        organization_name="Organization One",
        is_organization_admin=is_organization_admin,
        is_platform_admin=is_platform_admin,
        is_active=True,
        modules=tuple(modules),
    )


class WritePredicateTests(unittest.TestCase):
    def test_only_administrators_may_write(self):
        self.assertFalse(auth.can_write(_user()))
        self.assertTrue(auth.can_write(_user(is_organization_admin=True)))
        self.assertTrue(auth.can_write(_user(is_platform_admin=True)))

    def test_module_write_needs_both_the_grant_and_the_role(self):
        reader = _user()
        admin_without_module = _user(is_organization_admin=True, modules=())

        self.assertTrue(auth.can_access_module(reader, "prosodia"))
        self.assertFalse(auth.can_write_module(reader, "prosodia"))
        # Administradores recebem todo modulo por can_access_module.
        self.assertTrue(auth.can_write_module(admin_without_module, "prosodia"))
        self.assertFalse(auth.can_write_module(reader, "teste_sensorial"))


class AssertModuleWriteTests(unittest.TestCase):
    def test_anonymous_session_is_rejected(self):
        with patch.object(auth, "current_user", return_value=None):
            with self.assertRaises(auth.AuthorizationError):
                auth.assert_module_write("prosodia")

    def test_reader_with_the_module_grant_still_cannot_write(self):
        with patch.object(auth, "current_user", return_value=_user()):
            with self.assertRaises(auth.AuthorizationError):
                auth.assert_module_write("prosodia")

    def test_account_without_the_module_cannot_write_it(self):
        reader = _user(modules=("teste_sensorial",))
        with patch.object(auth, "current_user", return_value=reader):
            with self.assertRaises(auth.AuthorizationError):
                auth.assert_module_write("prosodia")

    def test_administrators_are_allowed(self):
        for actor in (
            _user(is_organization_admin=True),
            _user(is_platform_admin=True),
        ):
            with patch.object(auth, "current_user", return_value=actor):
                self.assertEqual(auth.assert_module_write("prosodia").id, actor.id)


class ProsodiaWriteGuardTests(unittest.TestCase):
    """Toda funcao que altera estado no LEX passa pela guarda de escrita."""

    WRITE_CALLS = (
        ("create_project", ("Projeto",)),
        ("update_project", (1, "Projeto")),
        ("delete_project", (1,)),
        ("create_audio", (1, "sessao")),
        ("delete_audio", (1,)),
        ("update_audio_openai_ids", (1, None, None)),
        ("update_audio_content", (1, b"", b"", b"")),
        ("save_analysis", (1, "modelo", "texto")),
        ("save_project_analysis", (1, "modelo", "texto")),
        ("save_quality_check", (1, "pass", [], [])),
        ("save_high_activations", (1, [])),
    )

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
        audit = patch.object(prosodia_db, "_audit")
        audit.start()
        self.addCleanup(audit.stop)
        organization = patch.object(
            prosodia_db,
            "_active_organization_id",
            return_value=self.organization.id,
        )
        organization.start()
        self.addCleanup(organization.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_every_write_is_refused_when_the_guard_denies(self):
        denial = auth.AuthorizationError("somente leitura")
        with patch.object(prosodia_db, "_require_write", side_effect=denial):
            for function_name, arguments in self.WRITE_CALLS:
                with self.subTest(function=function_name):
                    with self.assertRaises(auth.AuthorizationError):
                        getattr(prosodia_db, function_name)(*arguments)

    def test_a_denied_write_leaves_no_row_behind(self):
        with patch.object(
            prosodia_db, "_require_write", side_effect=auth.AuthorizationError("x")
        ):
            with self.assertRaises(auth.AuthorizationError):
                prosodia_db.create_project("Projeto Recusado")

        with patch.object(prosodia_db, "_require_write"):
            self.assertEqual(prosodia_db.get_projects(), [])

    def test_reads_stay_available_to_a_read_only_account(self):
        with patch.object(prosodia_db, "_require_write"):
            project_id = prosodia_db.create_project("Projeto Visivel")

        with patch.object(
            prosodia_db, "_require_write", side_effect=auth.AuthorizationError("x")
        ):
            self.assertEqual(
                [project["id"] for project in prosodia_db.get_projects()],
                [project_id],
            )
            self.assertIsNotNone(prosodia_db.get_project(project_id))
            self.assertEqual(prosodia_db.get_audios(project_id), [])
            self.assertIsNone(prosodia_db.get_latest_analysis(project_id))


class ProjectAuthorshipTests(unittest.TestCase):
    """Um administrador de organizacao nao edita projeto de outro administrador."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.platform_admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=self.organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.first_admin = auth.create_user(
            name="First Organization Admin",
            email="first-admin@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password="first-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.second_admin = auth.create_user(
            name="Second Organization Admin",
            email="second-admin@example.com",
            phone="5511977777777",
            organization_id=self.organization.id,
            password="second-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        prosodia_db.init_db()
        audit = patch.object(prosodia_db, "_audit")
        audit.start()
        self.addCleanup(audit.stop)
        organization = patch.object(
            prosodia_db,
            "_active_organization_id",
            return_value=self.organization.id,
        )
        organization.start()
        self.addCleanup(organization.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _as(self, actor):
        return patch.object(prosodia_db, "_require_write", return_value=actor)

    def _create_project(self, actor, name):
        with self._as(actor):
            return prosodia_db.create_project(name)

    def test_the_author_is_recorded_on_creation(self):
        project_id = self._create_project(self.first_admin, "Projeto do Primeiro")
        with self._as(self.first_admin):
            project = prosodia_db.get_project(project_id)
        self.assertEqual(project["created_by_user_id"], self.first_admin.id)

    def test_another_organization_administrator_cannot_edit_or_delete(self):
        project_id = self._create_project(self.first_admin, "Projeto do Primeiro")

        with self._as(self.second_admin):
            with self.assertRaises(auth.AuthorizationError):
                prosodia_db.update_project(project_id, "Renomeado")
            with self.assertRaises(auth.AuthorizationError):
                prosodia_db.delete_project(project_id)

            project = prosodia_db.get_project(project_id)
        self.assertEqual(project["name"], "Projeto do Primeiro")

    def test_the_author_keeps_control(self):
        project_id = self._create_project(self.first_admin, "Projeto do Primeiro")
        with self._as(self.first_admin):
            prosodia_db.update_project(project_id, "Renomeado pelo autor")
            self.assertEqual(
                prosodia_db.get_project(project_id)["name"], "Renomeado pelo autor"
            )
            prosodia_db.delete_project(project_id)
            self.assertIsNone(prosodia_db.get_project(project_id))

    def test_the_global_administrator_reaches_every_project(self):
        project_id = self._create_project(self.first_admin, "Projeto do Primeiro")
        with self._as(self.platform_admin):
            prosodia_db.update_project(project_id, "Renomeado pelo global")
            prosodia_db.delete_project(project_id)
            self.assertIsNone(prosodia_db.get_project(project_id))

    def test_a_project_created_by_the_global_administrator_stays_open(self):
        project_id = self._create_project(self.platform_admin, "Projeto provisionado")
        with self._as(self.second_admin):
            prosodia_db.update_project(project_id, "Ajustado pela organizacao")
            self.assertEqual(
                prosodia_db.get_project(project_id)["name"],
                "Ajustado pela organizacao",
            )

    def test_a_legacy_project_without_an_author_stays_open(self):
        project_id = self._create_project(self.first_admin, "Projeto legado")
        with auth.connection(self.database_path) as database:
            database.execute(
                "UPDATE projects SET created_by_user_id = NULL WHERE id = ?",
                (project_id,),
            )
        with self._as(self.second_admin):
            prosodia_db.update_project(project_id, "Editado como legado")
            self.assertEqual(
                prosodia_db.get_project(project_id)["name"], "Editado como legado"
            )

    def test_removing_the_author_releases_the_project(self):
        project_id = self._create_project(self.first_admin, "Projeto orfao")
        auth.delete_user(
            self.platform_admin,
            self.first_admin.id,
            database_path=self.database_path,
        )
        with self._as(self.second_admin):
            project = prosodia_db.get_project(project_id)
            self.assertIsNone(project["created_by_user_id"])
            prosodia_db.update_project(project_id, "Adotado")
            self.assertEqual(prosodia_db.get_project(project_id)["name"], "Adotado")

    def test_child_content_is_not_restricted_by_project_authorship(self):
        """Escopo declarado: a autoria trava o projeto, nao o conteudo dele."""

        project_id = self._create_project(self.first_admin, "Projeto do Primeiro")
        with self._as(self.second_admin):
            audio_id = prosodia_db.create_audio(project_id, "sessao-1")
            prosodia_db.save_analysis(audio_id, "modelo", "texto")
        self.assertIsNotNone(audio_id)

    def test_the_interface_predicate_matches_the_server(self):
        own_id = self._create_project(self.first_admin, "Projeto do Primeiro")
        provisioned_id = self._create_project(self.platform_admin, "Projeto provisionado")

        with self._as(self.second_admin):
            projects = {
                project["id"]: project for project in prosodia_db.get_projects()
            }

        self.assertFalse(
            prosodia_db.user_can_modify_project(projects[own_id], self.second_admin)
        )
        self.assertTrue(
            prosodia_db.user_can_modify_project(projects[own_id], self.first_admin)
        )
        self.assertTrue(
            prosodia_db.user_can_modify_project(projects[own_id], self.platform_admin)
        )
        self.assertTrue(
            prosodia_db.user_can_modify_project(
                projects[provisioned_id], self.second_admin
            )
        )
        self.assertFalse(
            prosodia_db.user_can_modify_project(
                projects[own_id],
                _user(user_id=self.second_admin.id),
            )
        )


class PeerAdministratorScopeTests(unittest.TestCase):
    """Um administrador de organizacao nao mexe na conta de outro administrador."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.platform_admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=self.organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.first_admin = auth.create_user(
            name="First Organization Admin",
            email="first-admin@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password="first-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.second_admin = auth.create_user(
            name="Second Organization Admin",
            email="second-admin@example.com",
            phone="5511977777777",
            organization_id=self.organization.id,
            password="second-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.reader = auth.create_user(
            name="Regular Reader",
            email="reader@example.com",
            phone="5511966666666",
            organization_id=self.organization.id,
            password="regular-reader-password",
            module_keys=("prosodia",),
            actor=self.platform_admin,
            database_path=self.database_path,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_peer_administrator_password_cannot_be_reset(self):
        with self.assertRaises(auth.AuthorizationError):
            auth.update_user(
                self.first_admin,
                self.second_admin.id,
                password="tomada-de-conta-1234",
                database_path=self.database_path,
            )
        # A senha original continua valendo.
        user, _ = auth.authenticate(
            "second-admin@example.com",
            "second-admin-password",
            self.database_path,
        )
        self.assertEqual(user.id, self.second_admin.id)

    def test_peer_administrator_cannot_be_deactivated_or_removed(self):
        with self.assertRaises(auth.AuthorizationError):
            auth.update_user(
                self.first_admin,
                self.second_admin.id,
                is_active=False,
                database_path=self.database_path,
            )
        with self.assertRaises(auth.AuthorizationError):
            auth.delete_user(
                self.first_admin,
                self.second_admin.id,
                database_path=self.database_path,
            )
        self.assertIsNotNone(
            auth.get_user(self.second_admin.id, self.database_path)
        )

    def test_platform_administrator_in_the_same_organization_is_protected(self):
        with self.assertRaises(auth.AuthorizationError):
            auth.update_user(
                self.first_admin,
                self.platform_admin.id,
                password="tomada-de-conta-1234",
                database_path=self.database_path,
            )

    def test_common_accounts_remain_manageable(self):
        updated = auth.update_user(
            self.first_admin,
            self.reader.id,
            phone="5511955555555",
            database_path=self.database_path,
        )
        self.assertEqual(updated.phone, "5511955555555")

        auth.delete_user(
            self.first_admin,
            self.reader.id,
            database_path=self.database_path,
        )
        self.assertIsNone(auth.get_user(self.reader.id, self.database_path))

    def test_an_administrator_still_edits_their_own_account(self):
        updated = auth.update_user(
            self.first_admin,
            self.first_admin.id,
            phone="5511944444444",
            database_path=self.database_path,
        )
        self.assertEqual(updated.phone, "5511944444444")

    def test_the_global_administrator_keeps_full_reach(self):
        updated = auth.update_user(
            self.platform_admin,
            self.second_admin.id,
            phone="5511933333333",
            database_path=self.database_path,
        )
        self.assertEqual(updated.phone, "5511933333333")

        auth.delete_user(
            self.platform_admin,
            self.second_admin.id,
            database_path=self.database_path,
        )
        self.assertIsNone(auth.get_user(self.second_admin.id, self.database_path))

    def test_account_created_by_another_administrator_is_off_limits(self):
        other_reader = auth.create_user(
            name="Reader Of The Second Admin",
            email="other-reader@example.com",
            phone="5511922222222",
            organization_id=self.organization.id,
            password="other-reader-password",
            module_keys=("prosodia",),
            actor=self.second_admin,
            database_path=self.database_path,
        )
        self.assertEqual(other_reader.created_by_user_id, self.second_admin.id)

        with self.assertRaises(auth.AuthorizationError):
            auth.update_user(
                self.first_admin,
                other_reader.id,
                phone="5511911111111",
                database_path=self.database_path,
            )
        with self.assertRaises(auth.AuthorizationError):
            auth.delete_user(
                self.first_admin,
                other_reader.id,
                database_path=self.database_path,
            )

        # O autor continua no comando da conta que criou.
        updated = auth.update_user(
            self.second_admin,
            other_reader.id,
            phone="5511911111111",
            database_path=self.database_path,
        )
        self.assertEqual(updated.phone, "5511911111111")

    def test_legacy_account_without_an_author_stays_manageable(self):
        with auth.connection(self.database_path) as database:
            database.execute(
                "UPDATE users SET created_by_user_id = NULL WHERE id = ?",
                (self.reader.id,),
            )
        legacy_reader = auth.get_user(self.reader.id, self.database_path)
        self.assertIsNone(legacy_reader.created_by_user_id)

        updated = auth.update_user(
            self.first_admin,
            self.reader.id,
            phone="5511900000000",
            database_path=self.database_path,
        )
        self.assertEqual(updated.phone, "5511900000000")

    def test_removing_the_author_releases_the_accounts_it_created(self):
        orphan = auth.create_user(
            name="Orphan Reader",
            email="orphan@example.com",
            phone="5511923232323",
            organization_id=self.organization.id,
            password="orphan-reader-password",
            module_keys=("prosodia",),
            actor=self.second_admin,
            database_path=self.database_path,
        )
        auth.delete_user(
            self.platform_admin,
            self.second_admin.id,
            database_path=self.database_path,
        )

        released = auth.get_user(orphan.id, self.database_path)
        self.assertIsNone(released.created_by_user_id)
        auth.update_user(
            self.first_admin,
            orphan.id,
            phone="5511924242424",
            database_path=self.database_path,
        )

    def test_can_manage_user_matches_the_server_guards(self):
        other_reader = auth.create_user(
            name="Reader Of The Second Admin",
            email="other-reader@example.com",
            phone="5511922222222",
            organization_id=self.organization.id,
            password="other-reader-password",
            module_keys=("prosodia",),
            actor=self.second_admin,
            database_path=self.database_path,
        )
        self.assertFalse(auth.can_manage_user(self.first_admin, other_reader))
        self.assertFalse(auth.can_manage_user(self.first_admin, self.second_admin))
        self.assertTrue(auth.can_manage_user(self.second_admin, other_reader))
        self.assertTrue(auth.can_manage_user(self.first_admin, self.reader))
        self.assertTrue(auth.can_manage_user(self.platform_admin, other_reader))

    def test_promotion_is_allowed_but_closes_the_door_afterwards(self):
        promoted = auth.update_user(
            self.first_admin,
            self.reader.id,
            is_organization_admin=True,
            database_path=self.database_path,
        )
        self.assertTrue(promoted.is_organization_admin)

        with self.assertRaises(auth.AuthorizationError):
            auth.update_user(
                self.first_admin,
                self.reader.id,
                password="tomada-de-conta-1234",
                database_path=self.database_path,
            )


class GlobalAdministratorReachTests(unittest.TestCase):
    """O administrador global enxerga as contas de todas as organizacoes."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
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
        self.organization_admin = auth.create_user(
            name="Organization One Admin",
            email="one-admin@example.com",
            phone="5511988888888",
            organization_id=self.organization_one.id,
            password="one-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.second_organization_user = auth.create_user(
            name="Organization Two User",
            email="two-user@example.com",
            phone="5511977777777",
            organization_id=self.organization_two.id,
            password="two-user-password",
            module_keys=("prosodia",),
            actor=self.platform_admin,
            database_path=self.database_path,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_organization_zero_lists_every_account(self):
        emails = {
            user.email
            for user in auth.list_users(
                self.platform_admin,
                organization_id=0,
                database_path=self.database_path,
            )
        }
        self.assertEqual(
            emails,
            {"platform@example.com", "one-admin@example.com", "two-user@example.com"},
        )

    def test_a_specific_organization_still_narrows_the_listing(self):
        emails = {
            user.email
            for user in auth.list_users(
                self.platform_admin,
                organization_id=self.organization_two.id,
                database_path=self.database_path,
            )
        }
        self.assertEqual(emails, {"two-user@example.com"})

    def test_organization_zero_does_not_widen_an_organization_administrator(self):
        emails = {
            user.email
            for user in auth.list_users(
                self.organization_admin,
                organization_id=0,
                database_path=self.database_path,
            )
        }
        self.assertEqual(emails, {"platform@example.com", "one-admin@example.com"})

    def test_search_and_inactive_filters_apply_across_organizations(self):
        auth.update_user(
            self.platform_admin,
            self.second_organization_user.id,
            is_active=False,
            database_path=self.database_path,
        )
        active_emails = {
            user.email
            for user in auth.list_users(
                self.platform_admin,
                organization_id=0,
                include_inactive=False,
                database_path=self.database_path,
            )
        }
        self.assertNotIn("two-user@example.com", active_emails)

        found = auth.list_users(
            self.platform_admin,
            organization_id=0,
            search="Organization Two",
            database_path=self.database_path,
        )
        self.assertEqual([user.email for user in found], ["two-user@example.com"])


class AuditTrailTests(unittest.TestCase):
    """Escritas e negativas precisam deixar rastro no audit_log."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.platform_admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=self.organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.first_admin = auth.create_user(
            name="First Organization Admin",
            email="first-admin@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password="first-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.second_admin = auth.create_user(
            name="Second Organization Admin",
            email="second-admin@example.com",
            phone="5511977777777",
            organization_id=self.organization.id,
            password="second-admin-password",
            module_keys=("prosodia",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        prosodia_db.init_db()
        organization = patch.object(
            prosodia_db,
            "_active_organization_id",
            return_value=self.organization.id,
        )
        organization.start()
        self.addCleanup(organization.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _actions(self):
        with auth.connection(self.database_path) as database:
            return [
                row["action"]
                for row in database.execute("SELECT action FROM audit_log ORDER BY id")
            ]

    def test_an_organization_write_is_recorded(self):
        with patch.object(prosodia_db, "_require_write", return_value=self.first_admin), \
                patch.object(auth, "current_user", return_value=self.first_admin):
            prosodia_db.create_project("Projeto auditado")

        self.assertIn("prosodia.project.create", self._actions())

    def test_reads_inside_the_organization_stay_out_of_the_log(self):
        with patch.object(auth, "current_user", return_value=self.first_admin):
            prosodia_db.get_projects()

        self.assertNotIn("prosodia.project.list", self._actions())

    def test_a_refused_project_edit_is_recorded(self):
        with patch.object(prosodia_db, "_require_write", return_value=self.first_admin), \
                patch.object(auth, "current_user", return_value=self.first_admin):
            project_id = prosodia_db.create_project("Projeto do Primeiro")

        with patch.object(prosodia_db, "_require_write", return_value=self.second_admin), \
                patch.object(auth, "current_user", return_value=self.second_admin):
            with self.assertRaises(auth.AuthorizationError):
                prosodia_db.delete_project(project_id)

        self.assertIn("denied.project.delete", self._actions())

    def test_a_read_only_account_leaves_a_denial_behind(self):
        reader = auth.create_user(
            name="Regular Reader",
            email="reader@example.com",
            phone="5511966666666",
            organization_id=self.organization.id,
            password="regular-reader-password",
            module_keys=("prosodia",),
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        with patch.object(auth, "current_user", return_value=reader):
            with self.assertRaises(auth.AuthorizationError):
                prosodia_db.create_project("Projeto recusado")

        self.assertIn("denied.module.write", self._actions())

    def test_a_refused_account_edit_survives_the_rollback(self):
        with self.assertRaises(auth.AuthorizationError):
            auth.update_user(
                self.first_admin,
                self.second_admin.id,
                password="tomada-de-conta-1234",
                database_path=self.database_path,
            )

        self.assertIn("denied.user.update", self._actions())


if __name__ == "__main__":
    unittest.main()
