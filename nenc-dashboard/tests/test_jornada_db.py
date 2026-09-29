"""Camada de dados da Jornada de Compra: guarda, autoria, organizacao e versao.

Os casos seguem os do NencBoost (test_role_based_access, test_organization_
isolation): a Jornada passou a ter projetos e herdou as mesmas regras.
"""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from utils import auth, jornada_db, jornada_media


def _identity(database_path):
    organization = auth.create_organization(
        "Organization One", database_path=database_path, _bootstrap=True
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


class _Base(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization, self.platform_admin = _identity(self.database_path)
        self.first_admin = auth.create_user(
            name="First Admin",
            email="first@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password="first-admin-password",
            module_keys=("jornada_compra",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        self.second_admin = auth.create_user(
            name="Second Admin",
            email="second@example.com",
            phone="5511977777777",
            organization_id=self.organization.id,
            password="second-admin-password",
            module_keys=("jornada_compra",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        environment = patch.dict(
            os.environ,
            {
                "NENC_DB_PATH": str(self.database_path),
                "NENC_MEDIA_DIR": str(Path(self.temporary_directory.name) / "media"),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        jornada_db.init_db()
        for patcher in (
            patch.object(jornada_db, "_audit"),
            patch.object(jornada_db, "_remove_from_knowledge_base"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.org_patch = patch.object(
            jornada_db, "_active_organization_id", return_value=self.organization.id
        )
        self.org_patch.start()
        self.addCleanup(self.org_patch.stop)

    def _as(self, actor):
        return patch.object(jornada_db, "_require_write", return_value=actor)

    def _project(self, actor=None, name="Estudo"):
        with self._as(actor or self.first_admin):
            return jornada_db.create_project(name)

    def _version(self, project_id):
        return jornada_db.get_project(project_id)["data_version"]


class WriteGuardTests(_Base):
    def _write_calls(self, project_id):
        return (
            ("create_project", ("Projeto",), {}),
            ("update_project", (project_id,), {"name": "Novo"}),
            ("delete_project", (project_id,), {}),
            (
                "add_files",
                (project_id, [{"filename": "a.csv", "kind": "gaze_frames", "content": b"x"}]),
                {},
            ),
            ("set_file_active", (project_id, 1, False), {}),
            ("update_file_meta", (project_id, 1, {"store": "1"}), {}),
            ("delete_file", (project_id, 1), {}),
            ("upsert_participants", (project_id, [{"code": "Pt01"}]), {}),
            (
                "set_recording_status",
                (project_id, "Pt01", "livre", "1"),
                {"status": "excluida", "reason": "x"},
            ),
            ("save_aoi_overrides", (project_id, [{"aoi": "A"}]), {}),
            ("delete_aoi_overrides", (project_id, [("", "A")]), {}),
            (
                "add_media",
                (project_id,),
                {
                    "participant_code": "Pt01",
                    "task": "livre",
                    "store": "1",
                    "filename": "v.mp4",
                    "sha256": "abc",
                    "size_bytes": 1,
                    "rel_path": "1/1/abc.mp4",
                },
            ),
            ("delete_media", (project_id, 1), {}),
            ("add_interview", (project_id, "Titulo", "Texto"), {}),
            ("delete_interview", (project_id, 1), {}),
            (
                "save_analysis",
                (project_id,),
                {"model": "m", "mode": "rapida", "analysis_text": "t"},
            ),
            ("set_analysis_kb_file", (project_id, 1, "file-1"), {}),
            ("delete_analyses", (project_id, [1]), {}),
        )

    def test_every_write_is_refused_when_the_guard_denies(self):
        project_id = self._project()
        denial = auth.AuthorizationError("somente leitura")
        with patch.object(jornada_db, "_require_write", side_effect=denial):
            for name, args, kwargs in self._write_calls(project_id):
                with self.subTest(function=name):
                    with self.assertRaises(auth.AuthorizationError):
                        getattr(jornada_db, name)(*args, **kwargs)

    def test_a_denied_write_leaves_no_row_behind(self):
        with patch.object(
            jornada_db, "_require_write", side_effect=auth.AuthorizationError("x")
        ):
            with self.assertRaises(auth.AuthorizationError):
                jornada_db.create_project("Recusado")
        self.assertEqual(jornada_db.list_projects(), [])

    def test_reads_stay_available_to_a_read_only_account(self):
        project_id = self._project()
        with patch.object(
            jornada_db, "_require_write", side_effect=auth.AuthorizationError("x")
        ):
            self.assertEqual([p["id"] for p in jornada_db.list_projects()], [project_id])
            self.assertIsNotNone(jornada_db.get_project(project_id))
            self.assertEqual(jornada_db.list_files(project_id), [])
            self.assertEqual(jornada_db.list_participants(project_id), [])
            self.assertIsNone(jornada_db.get_latest_analysis(project_id))

    def test_old_callers_find_get_projects(self):
        self.assertIs(jornada_db.get_projects, jornada_db.list_projects)


class AuthorshipTests(_Base):
    def test_the_author_is_recorded(self):
        project_id = self._project(self.first_admin)
        self.assertEqual(
            jornada_db.get_project(project_id)["created_by_user_id"], self.first_admin.id
        )

    def test_another_organization_admin_cannot_edit_or_delete(self):
        project_id = self._project(self.first_admin)
        with self._as(self.second_admin):
            with self.assertRaises(auth.AuthorizationError):
                jornada_db.update_project(project_id, name="Renomeado")
            with self.assertRaises(auth.AuthorizationError):
                jornada_db.delete_project(project_id)
        self.assertEqual(jornada_db.get_project(project_id)["name"], "Estudo")

    def test_content_is_not_locked_by_authorship(self):
        project_id = self._project(self.first_admin)
        with self._as(self.second_admin):
            jornada_db.upsert_participants(project_id, [{"code": "Pt01"}])
        self.assertEqual(len(jornada_db.list_participants(project_id)), 1)

    def test_global_admin_and_legacy_projects_stay_open(self):
        project_id = self._project(self.first_admin)
        with self._as(self.platform_admin):
            self.assertTrue(jornada_db.update_project(project_id, name="Pelo global"))
        with auth.connection(self.database_path) as database:
            database.execute(
                "UPDATE jc_projects SET created_by_user_id = NULL WHERE id = ?", (project_id,)
            )
        with self._as(self.second_admin):
            self.assertTrue(jornada_db.update_project(project_id, name="Legado"))

    def test_the_interface_predicate_matches_the_server(self):
        project_id = self._project(self.first_admin)
        project = jornada_db.get_project(project_id)
        self.assertTrue(jornada_db.user_can_modify_project(project, self.first_admin))
        self.assertFalse(jornada_db.user_can_modify_project(project, self.second_admin))
        self.assertTrue(jornada_db.user_can_modify_project(project, self.platform_admin))


class OrganizationTests(_Base):
    def setUp(self):
        super().setUp()
        self.organization_two = auth.create_organization(
            "Organization Two", actor=self.platform_admin, database_path=self.database_path
        )
        self.project_one = self._project(self.first_admin, "Da Um")

    def test_another_organization_does_not_see_the_project(self):
        with patch.object(
            jornada_db, "_active_organization_id", return_value=self.organization_two.id
        ):
            self.assertEqual(jornada_db.list_projects(), [])
            self.assertIsNone(jornada_db.get_project(self.project_one))
            with self.assertRaises(ValueError):
                jornada_db.list_files(self.project_one)
            with self._as(self.platform_admin), self.assertRaises(ValueError):
                jornada_db.upsert_participants(self.project_one, [{"code": "Pt01"}])

    def test_all_organizations_reads_everything_and_children_keep_the_project_org(self):
        with patch.object(jornada_db, "_active_organization_id", return_value=0):
            self.assertEqual(len(jornada_db.list_projects()), 1)
            with self._as(self.platform_admin):
                jornada_db.upsert_participants(self.project_one, [{"code": "Pt01"}])
                new_id = jornada_db.create_project("Criado em Todas")
        with auth.connection(self.database_path) as database:
            child_org = database.execute(
                "SELECT organization_id FROM jc_participants"
            ).fetchone()[0]
            new_org = database.execute(
                "SELECT organization_id FROM jc_projects WHERE id = ?", (new_id,)
            ).fetchone()[0]
        self.assertEqual(child_org, self.organization.id)
        # Em "Todas" o projeto nasce na organizacao da conta que criou.
        self.assertEqual(new_org, self.platform_admin.organization_id)

    def test_an_export_is_audited_for_any_role_once_the_project_is_visible(self):
        with patch.object(
            jornada_db, "_require_write", side_effect=auth.AuthorizationError("x")
        ):
            jornada_db.audit_export(self.project_one, "excel")
        jornada_db._audit.assert_called_with(
            "jornada.export.excel", "jc_project", self.project_one, self.organization.id, write=True
        )
        with patch.object(
            jornada_db, "_active_organization_id", return_value=self.organization_two.id
        ), self.assertRaises(ValueError):
            jornada_db.audit_export(self.project_one, "excel")

    def test_the_bundle_only_opens_with_the_project_organization(self):
        with self.assertRaises(ValueError):
            jornada_db.load_project_bundle(self.project_one, self.organization_two.id)
        bundle = jornada_db.load_project_bundle(self.project_one, self.organization.id)
        self.assertEqual(bundle["project"]["name"], "Da Um")


class DataTests(_Base):
    def test_files_are_deduplicated_and_listed_without_the_blob(self):
        project_id = self._project()
        item = {"filename": "Pt01-JLivre-X1.csv", "kind": "gaze_frames",
                "content": b"frame,timestamp,x,y\n0,0,1,1\n", "meta": {"task": "livre"}}
        with self._as(self.first_admin):
            first = jornada_db.add_files(project_id, [item])
            again = jornada_db.add_files(project_id, [dict(item, filename="copia.csv")])
        self.assertEqual(len(first["added"]), 1)
        self.assertEqual(again["duplicates"], ["copia.csv"])
        listed = jornada_db.list_files(project_id)
        self.assertNotIn("content", listed[0])
        self.assertEqual(listed[0]["meta"], {"task": "livre"})
        self.assertEqual(
            jornada_db.get_file_content(project_id, listed[0]["id"]), item["content"]
        )

    def test_oversized_and_empty_files_are_refused_before_the_guard(self):
        project_id = self._project()
        with self._as(self.first_admin):
            with self.assertRaises(ValueError):
                jornada_db.add_files(project_id, [{"filename": "v.csv", "kind": "x", "content": b""}])
            with patch.object(jornada_db, "MAX_FILE_BYTES", 3):
                with self.assertRaises(ValueError):
                    jornada_db.add_files(
                        project_id, [{"filename": "g.csv", "kind": "x", "content": b"1234"}]
                    )

    def test_data_changes_bump_the_version_and_renames_do_not(self):
        project_id = self._project()
        start = self._version(project_id)
        with self._as(self.first_admin):
            jornada_db.update_project(project_id, name="Outro nome")
            self.assertEqual(self._version(project_id), start)
            jornada_db.update_project(project_id, settings_json={"examined_threshold_s": 2})
            after_settings = self._version(project_id)
            jornada_db.set_recording_status(
                project_id, "Pt01", "livre", "2250", status="excluida", reason="teste"
            )
            after_status = self._version(project_id)
        self.assertGreater(after_settings, start)
        self.assertGreater(after_status, after_settings)

    def test_seeding_never_overwrites_a_manual_edit(self):
        project_id = self._project()
        with self._as(self.first_admin):
            jornada_db.upsert_participants(project_id, [{"code": "Pt01", "profile": "Manual"}])
            jornada_db.upsert_participants(
                project_id,
                [{"code": "Pt01", "profile": "Da planilha", "tempo_informado": "17s"}],
                only_missing=True,
                source="upload",
            )
        row = jornada_db.list_participants(project_id)[0]
        self.assertEqual(row["profile"], "Manual")
        self.assertEqual(row["tempo_informado"], "17s")

    def test_excluding_a_recording_needs_a_reason(self):
        project_id = self._project()
        with self._as(self.first_admin), self.assertRaises(ValueError):
            jornada_db.set_recording_status(project_id, "Pt01", "livre", status="excluida")

    def test_catalog_overrides_round_trip(self):
        project_id = self._project()
        with self._as(self.first_admin):
            jornada_db.save_aoi_overrides(
                project_id,
                [{"store": "2250", "aoi": "Marca A Noturno p1", "kind": "produto",
                  "brand": "Marca A", "attrs": {"tipo": "Noturno"}, "include": False}],
            )
        override = jornada_db.list_aoi_overrides(project_id)[0]
        self.assertEqual(override["attrs"], {"tipo": "Noturno"})
        self.assertFalse(override["include"])
        with self._as(self.first_admin):
            self.assertEqual(
                jornada_db.delete_aoi_overrides(project_id, [("2250", "Marca A Noturno p1")]), 1
            )

    def test_analyses_keep_search_and_leave_the_knowledge_base_on_delete(self):
        project_id = self._project()
        with self._as(self.first_admin):
            analysis_id = jornada_db.save_analysis(
                project_id,
                model="m",
                mode="rapida",
                analysis_text="texto",
                search={"searched": True},
                data_version=3,
            )
            jornada_db.set_analysis_kb_file(project_id, analysis_id, "file-kb")
        latest = jornada_db.get_latest_analysis(project_id)
        self.assertEqual(latest["search"], {"searched": True})
        self.assertEqual(latest["data_version"], 3)
        with self._as(self.first_admin):
            self.assertEqual(jornada_db.delete_analyses(project_id, [analysis_id]), 1)
        jornada_db._remove_from_knowledge_base.assert_called_with(0, ["file-kb"])

    def test_deleting_the_project_removes_its_videos_and_knowledge(self):
        project_id = self._project()
        saved = jornada_media.save_video(self.organization.id, project_id, "v.mp4", b"video")
        with self._as(self.first_admin):
            jornada_db.add_media(
                project_id, participant_code="Pt01", task="livre", store="2250",
                filename="v.mp4", **saved,
            )
            self.assertTrue(jornada_db.delete_project(project_id))
        self.assertFalse(jornada_media.resolve(saved["rel_path"]).exists())
        jornada_db._remove_from_knowledge_base.assert_called_with(project_id)
        self.assertIsNone(jornada_db.get_project(project_id))


class MigrationTests(unittest.TestCase):
    """Um banco da versao de 0dc853f abre sem perder nada."""

    def test_the_old_schema_is_migrated_in_place(self):
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "nenc-insights.db"
            organization, admin = _identity(database_path)
            other = auth.create_organization(
                "Organization Two", actor=admin, database_path=database_path
            )
            # `with sqlite3.connect` so faz commit; sem fechar, o Windows nao
            # deixa apagar a pasta temporaria.
            with closing(sqlite3.connect(database_path)) as database, database:
                database.executescript(
                    """
                    CREATE TABLE jc_projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        organization_id INTEGER NOT NULL,
                        name TEXT NOT NULL, categoria TEXT, historico TEXT,
                        problemas TEXT, questions TEXT, marcas TEXT, briefing_text TEXT,
                        created_at TEXT, updated_at TEXT
                    );
                    CREATE TABLE jc_analyses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        organization_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
                        model TEXT, analysis_text TEXT, citations TEXT, created_at TEXT
                    );
                    """
                )
                database.execute(
                    "INSERT INTO jc_projects (organization_id, name) VALUES (?, 'Antigo')",
                    (organization.id,),
                )
                # Filho gravado com a organizacao errada pela versao antiga.
                database.execute(
                    "INSERT INTO jc_analyses (organization_id, project_id, analysis_text) "
                    "VALUES (?, 1, 'velha')",
                    (other.id,),
                )
            with patch.dict(os.environ, {"NENC_DB_PATH": str(database_path)}):
                jornada_db.init_db()
                jornada_db.init_db()
            with closing(sqlite3.connect(database_path)) as database:
                columns = {row[1] for row in database.execute("PRAGMA table_info(jc_projects)")}
                analysis_org = database.execute(
                    "SELECT organization_id FROM jc_analyses"
                ).fetchone()[0]
                version = database.execute("SELECT data_version FROM jc_projects").fetchone()[0]
            self.assertTrue({"settings_json", "data_version", "created_by_user_id"} <= columns)
            self.assertEqual(analysis_org, organization.id)
            self.assertEqual(version, 0)


if __name__ == "__main__":
    unittest.main()
