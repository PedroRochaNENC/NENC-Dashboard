"""Camada de dados do Teste Sensorial: guarda, autoria, organizacao, versao e disco.

Espelha test_jornada_db: o modulo passou a ter projetos e herdou as mesmas
regras. As tabelas `ts_*` de uma versao antiga ficam intocadas.
"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils import auth, sensorial_db, sensorial_store

# A funcao real, antes do patch que os testes de base aplicam.
_REMOVE_FROM_KNOWLEDGE_BASE = sensorial_db._remove_from_knowledge_base


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
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "nenc-insights.db"
        self.organization, self.platform_admin = _identity(self.database_path)
        self.first_admin = auth.create_user(
            name="First Admin",
            email="first@example.com",
            phone="5511988888888",
            organization_id=self.organization.id,
            password="first-admin-password",
            module_keys=("teste_sensorial",),
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
            module_keys=("teste_sensorial",),
            is_organization_admin=True,
            actor=self.platform_admin,
            database_path=self.database_path,
        )
        environment = patch.dict(
            os.environ,
            {
                "NENC_DB_PATH": str(self.database_path),
                "NENC_SENSORIAL_DIR": str(self.root / "sensorial"),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        sensorial_db.init_db()
        for patcher in (
            patch.object(sensorial_db, "_audit"),
            patch.object(sensorial_db, "_remove_from_knowledge_base"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.org_patch = patch.object(
            sensorial_db, "_active_organization_id", return_value=self.organization.id
        )
        self.org_patch.start()
        self.addCleanup(self.org_patch.stop)

    def _as(self, actor):
        return patch.object(sensorial_db, "_require_write", return_value=actor)

    def _project(self, actor=None, name="Estudo"):
        with self._as(actor or self.first_admin):
            return sensorial_db.create_project(name)

    def _version(self, project_id):
        return sensorial_db.get_project(project_id)["data_version"]


class WriteGuardTests(_Base):
    def _write_calls(self, project_id):
        return (
            ("create_project", ("Projeto",), {}),
            ("update_project", (project_id,), {"name": "Novo"}),
            ("delete_project", (project_id,), {}),
            ("add_files", (project_id, [{"filename": "a.png", "role": "imagem", "content": b"x"}]), {}),
            ("set_file_active", (project_id, 1, False), {}),
            ("delete_file", (project_id, 1), {}),
            ("upsert_participants", (project_id, [{"code": "P01"}]), {}),
            ("set_session_status", (project_id, "S1"), {"status": "excluida", "reason": "x"}),
            ("save_analysis", (project_id,), {"model": "m", "mode": "rapida", "analysis_text": "t"}),
            ("set_analysis_kb_file", (project_id, 1, "file-1"), {}),
            ("delete_analyses", (project_id, [1]), {}),
        )

    def test_every_write_is_refused_when_the_guard_denies(self):
        project_id = self._project()
        denial = auth.AuthorizationError("somente leitura")
        with patch.object(sensorial_db, "_require_write", side_effect=denial):
            for name, args, kwargs in self._write_calls(project_id):
                with self.subTest(function=name):
                    with self.assertRaises(auth.AuthorizationError):
                        getattr(sensorial_db, name)(*args, **kwargs)

    def test_a_denied_write_leaves_no_row_behind(self):
        with patch.object(sensorial_db, "_require_write", side_effect=auth.AuthorizationError("x")):
            with self.assertRaises(auth.AuthorizationError):
                sensorial_db.create_project("Recusado")
        self.assertEqual(sensorial_db.list_projects(), [])

    def test_reads_stay_available_to_a_read_only_account(self):
        project_id = self._project()
        with patch.object(sensorial_db, "_require_write", side_effect=auth.AuthorizationError("x")):
            self.assertEqual([p["id"] for p in sensorial_db.list_projects()], [project_id])
            self.assertIsNotNone(sensorial_db.get_project(project_id))
            self.assertEqual(sensorial_db.list_files(project_id), [])
            self.assertEqual(sensorial_db.list_participants(project_id), [])
            self.assertEqual(sensorial_db.list_session_overrides(project_id), [])
            self.assertEqual(sensorial_db.list_analyses(project_id), [])

    def test_the_guard_is_the_one_of_this_module(self):
        with patch.object(auth, "assert_module_write", return_value=self.first_admin) as guard:
            sensorial_db.create_project("Pela guarda")
        guard.assert_called_once_with("teste_sensorial")


class AuthorshipTests(_Base):
    def test_the_author_is_recorded(self):
        project_id = self._project(self.first_admin)
        self.assertEqual(sensorial_db.get_project(project_id)["created_by_user_id"], self.first_admin.id)

    def test_another_organization_admin_cannot_edit_or_delete(self):
        project_id = self._project(self.first_admin)
        with self._as(self.second_admin):
            with self.assertRaises(auth.AuthorizationError):
                sensorial_db.update_project(project_id, name="Renomeado")
            with self.assertRaises(auth.AuthorizationError):
                sensorial_db.delete_project(project_id)
        self.assertEqual(sensorial_db.get_project(project_id)["name"], "Estudo")

    def test_content_is_not_locked_by_authorship(self):
        project_id = self._project(self.first_admin)
        with self._as(self.second_admin):
            sensorial_db.upsert_participants(project_id, [{"code": "P01"}])
        self.assertEqual(len(sensorial_db.list_participants(project_id)), 1)

    def test_global_admin_and_legacy_projects_stay_open(self):
        project_id = self._project(self.first_admin)
        with self._as(self.platform_admin):
            self.assertTrue(sensorial_db.update_project(project_id, name="Pelo global"))
        with auth.connection(self.database_path) as database:
            database.execute("UPDATE sens_projects SET created_by_user_id = NULL WHERE id = ?", (project_id,))
        with self._as(self.second_admin):
            self.assertTrue(sensorial_db.update_project(project_id, name="Legado"))

    def test_the_interface_predicate_matches_the_server(self):
        project_id = self._project(self.first_admin)
        project = sensorial_db.get_project(project_id)
        self.assertTrue(sensorial_db.user_can_modify_project(project, self.first_admin))
        self.assertFalse(sensorial_db.user_can_modify_project(project, self.second_admin))
        self.assertTrue(sensorial_db.user_can_modify_project(project, self.platform_admin))


class OrganizationTests(_Base):
    def setUp(self):
        super().setUp()
        self.organization_two = auth.create_organization(
            "Organization Two", actor=self.platform_admin, database_path=self.database_path
        )
        self.project_one = self._project(self.first_admin, "Da Um")

    def test_another_organization_does_not_see_the_project(self):
        with patch.object(sensorial_db, "_active_organization_id", return_value=self.organization_two.id):
            self.assertEqual(sensorial_db.list_projects(), [])
            self.assertIsNone(sensorial_db.get_project(self.project_one))
            with self.assertRaises(ValueError):
                sensorial_db.list_files(self.project_one)
            with self._as(self.platform_admin), self.assertRaises(ValueError):
                sensorial_db.upsert_participants(self.project_one, [{"code": "P01"}])
            with self._as(self.platform_admin), self.assertRaises(ValueError):
                sensorial_db.set_session_status(self.project_one, "S1", status="incluida")

    def test_all_organizations_reads_everything_and_children_keep_the_project_org(self):
        with patch.object(sensorial_db, "_active_organization_id", return_value=0):
            self.assertEqual(len(sensorial_db.list_projects()), 1)
            with self._as(self.platform_admin):
                sensorial_db.upsert_participants(self.project_one, [{"code": "P01"}])
                sensorial_db.add_files(self.project_one, [
                    {"filename": "t.csv", "role": "eeg_indicadores", "content": b"a,b\n1,2\n",
                     "table": pd.DataFrame({"a": [1]})},
                ])
                new_id = sensorial_db.create_project("Criado em Todas")
        with auth.connection(self.database_path) as database:
            child_org = database.execute("SELECT organization_id FROM sens_participants").fetchone()[0]
            file_org = database.execute("SELECT organization_id FROM sens_files").fetchone()[0]
            new_org = database.execute(
                "SELECT organization_id FROM sens_projects WHERE id = ?", (new_id,)
            ).fetchone()[0]
        self.assertEqual(child_org, self.organization.id)
        self.assertEqual(file_org, self.organization.id)
        # O disco tambem usa a organizacao do projeto, nunca o 0 de "Todas".
        self.assertTrue(sensorial_store.project_dir(self.organization.id, self.project_one).is_dir())
        self.assertFalse(sensorial_store.project_dir(0, self.project_one).exists())
        self.assertEqual(new_org, self.platform_admin.organization_id)

    def test_an_export_is_audited_for_any_role_once_the_project_is_visible(self):
        with patch.object(sensorial_db, "_require_write", side_effect=auth.AuthorizationError("x")):
            sensorial_db.audit_export(self.project_one, "excel")
        sensorial_db._audit.assert_called_with(
            "sensorial.export.excel", "sens_project", self.project_one, self.organization.id, write=True
        )
        with patch.object(sensorial_db, "_active_organization_id", return_value=self.organization_two.id), \
                self.assertRaises(ValueError):
            sensorial_db.audit_export(self.project_one, "excel")

    def test_the_bundle_only_opens_with_the_project_organization(self):
        with self.assertRaises(ValueError):
            sensorial_db.load_project_bundle(self.project_one, self.organization_two.id)
        bundle = sensorial_db.load_project_bundle(self.project_one, self.organization.id)
        self.assertEqual(bundle["project"]["name"], "Da Um")
        self.assertEqual(bundle["settings"], {})


class FileTests(_Base):
    def test_small_files_stay_in_the_database_deduplicated_and_listed_without_the_blob(self):
        project_id = self._project()
        item = {"filename": "topomapa.png", "role": "eeg_topomapa", "content": b"\x89PNG imagem",
                "meta": {"banda": "Alpha"}}
        with self._as(self.first_admin):
            first = sensorial_db.add_files(project_id, [item])
            again = sensorial_db.add_files(project_id, [dict(item, filename="copia.png")])
        self.assertEqual(len(first["added"]), 1)
        self.assertEqual(again["duplicates"], ["copia.png"])
        listed = sensorial_db.list_files(project_id)
        self.assertNotIn("content", listed[0])
        self.assertEqual((listed[0]["storage"], listed[0]["meta"]), ("blob", {"banda": "Alpha"}))
        self.assertEqual(sensorial_db.get_file_content(project_id, listed[0]["id"]), item["content"])

    def test_tables_go_to_disk_named_by_the_hash_and_reach_the_bundle(self):
        project_id = self._project()
        content = b"sessao_id;valor\nS1;1,5\n"
        table = pd.DataFrame({"sessao_id": ["S1"], "valor": [1.5]})
        with self._as(self.first_admin):
            sensorial_db.add_files(project_id, [
                {"filename": "Nome Pessoa_indicadores.csv", "role": "eeg_indicadores",
                 "content": content, "table": table, "run_id": "run_2"},
            ])
        stored = sensorial_db.list_files(project_id)[0]
        sha = hashlib.sha256(content).hexdigest()
        self.assertEqual((stored["storage"], stored["table_version"], stored["run_id"]), ("disk", 1, "run_2"))
        # O nome do arquivo nunca vai para o caminho no disco.
        self.assertEqual(Path(stored["orig_path"]).name, sha + ".csv")
        self.assertNotIn("Pessoa", stored["orig_path"])
        self.assertEqual(sensorial_db.get_file_content(project_id, stored["id"]), content)
        bundle = sensorial_db.load_project_bundle(project_id, self.organization.id)
        pd.testing.assert_frame_equal(sensorial_db.read_file_table(bundle["files"][0]), table)

    def test_a_big_file_goes_to_disk_and_a_recompressed_original_is_a_duplicate(self):
        project_id = self._project()
        original = hashlib.sha256(b"csv original").hexdigest()
        with self._as(self.first_admin), patch.object(sensorial_db, "MAX_BLOB_BYTES", 3):
            sensorial_db.add_files(project_id, [
                {"filename": "psd.csv.gz", "role": "documento", "content": b"gzip-1", "source_sha256": original},
            ])
            # O mesmo CSV comprimido de novo (bytes diferentes) nao entra outra vez.
            again = sensorial_db.add_files(project_id, [
                {"filename": "psd-de-novo.csv.gz", "role": "documento", "content": b"gzip-2",
                 "source_sha256": original.upper()},
            ])
        self.assertEqual(sensorial_db.list_files(project_id)[0]["storage"], "disk")
        self.assertEqual(again["duplicates"], ["psd-de-novo.csv.gz"])

    def test_empty_files_are_refused_before_the_guard(self):
        project_id = self._project()
        with patch.object(sensorial_db, "_require_write", side_effect=AssertionError("guarda chamada")):
            with self.assertRaises(ValueError):
                sensorial_db.add_files(project_id, [{"filename": "v.csv", "role": "x", "content": b""}])

    def test_a_new_pipeline_run_supersedes_the_old_one_of_the_same_role(self):
        project_id = self._project()
        with self._as(self.first_admin):
            sensorial_db.add_files(project_id, [
                {"filename": "a.csv", "role": "eeg_indicadores", "content": b"1", "table": pd.DataFrame({"a": [1]})},
                {"filename": "q.csv", "role": "eeg_qualidade", "content": b"2", "table": pd.DataFrame({"a": [2]})},
            ])
            sensorial_db.add_files(project_id, [
                {"filename": "b.csv", "role": "eeg_indicadores", "content": b"3",
                 "table": pd.DataFrame({"a": [3]}), "supersede": True},
            ])
        active = {f["filename"]: f["is_active"] for f in sensorial_db.list_files(project_id)}
        self.assertEqual(active, {"a.csv": False, "q.csv": True, "b.csv": True})
        bundle = sensorial_db.load_project_bundle(project_id, self.organization.id)
        self.assertEqual(sorted(f["filename"] for f in bundle["files"]), ["b.csv", "q.csv"])

    def test_deleting_a_file_removes_the_original_and_its_table(self):
        project_id = self._project()
        with self._as(self.first_admin):
            sensorial_db.add_files(project_id, [
                {"filename": "a.csv", "role": "eeg_psd", "content": b"x", "table": pd.DataFrame({"a": [1]})},
            ])
        stored = sensorial_db.list_files(project_id)[0]
        bundle_file = sensorial_db.load_project_bundle(project_id, self.organization.id)["files"][0]
        self.assertTrue(Path(bundle_file["table_path"]).is_file())
        with self._as(self.first_admin):
            self.assertTrue(sensorial_db.delete_file(project_id, stored["id"]))
        self.assertFalse(Path(bundle_file["table_path"]).exists())
        self.assertFalse(sensorial_store.resolve(stored["orig_path"]).exists())
        self.assertEqual(sensorial_db.list_files(project_id), [])

    def test_the_store_refuses_paths_outside_its_root(self):
        with self.assertRaises(ValueError):
            sensorial_store.resolve("../fora.txt")
        with self.assertRaises(ValueError):
            sensorial_store.save_original(1, 1, "nao-e-hash", ".csv", b"x")


class DataTests(_Base):
    def test_data_changes_bump_the_version_and_renames_do_not(self):
        project_id = self._project()
        start = self._version(project_id)
        with self._as(self.first_admin):
            sensorial_db.update_project(project_id, name="Outro nome", objetivo="Outro objetivo")
            self.assertEqual(self._version(project_id), start)
            sensorial_db.update_project(project_id, settings_json={"limpeza": {"ativa": True}})
            after_settings = self._version(project_id)
            sensorial_db.set_session_status(project_id, "S1", layer="eeg", status="excluida", reason="sinal ruim")
            after_status = self._version(project_id)
            sensorial_db.upsert_participants(project_id, [{"code": "P01", "profile": {"sexo": "F"}}])
            after_profile = self._version(project_id)
        self.assertGreater(after_settings, start)
        self.assertGreater(after_status, after_settings)
        self.assertGreater(after_profile, after_status)
        settings = sensorial_db.project_settings(sensorial_db.get_project(project_id))
        self.assertEqual(settings, {"limpeza": {"ativa": True}})

    def test_seeding_never_overwrites_a_manual_edit(self):
        project_id = self._project()
        with self._as(self.first_admin):
            sensorial_db.upsert_participants(project_id, [{"code": "P01", "profile": {"grupo": "Manual"}}])
            sensorial_db.upsert_participants(
                project_id,
                [{"code": "P01", "profile": {"grupo": "Da planilha", "sexo": "F"}}],
                only_missing=True,
                source="planilha",
            )
        row = sensorial_db.list_participants(project_id)[0]
        self.assertEqual(row["profile"], {"grupo": "Manual", "sexo": "F"})
        self.assertEqual(row["source"], "manual")

    def test_session_decisions_need_a_reason_to_exclude_and_upsert_by_layer(self):
        project_id = self._project()
        with self._as(self.first_admin):
            with self.assertRaises(ValueError):
                sensorial_db.set_session_status(project_id, "S1", status="excluida")
            with self.assertRaises(ValueError):
                sensorial_db.set_session_status(project_id, "S1", layer="prosodia", status="incluida")
            sensorial_db.set_session_status(project_id, "S1", layer="eeg", status="excluida", reason="ruido")
            sensorial_db.set_session_status(project_id, "S1", layer="eeg", status="auto")
            sensorial_db.set_session_status(project_id, "S1", status="incluida", participant_code_override="P09")
        overrides = {(o["layer"], o["status"], o["reason"], o["participant_code_override"])
                     for o in sensorial_db.list_session_overrides(project_id)}
        self.assertEqual(overrides, {("eeg", "auto", None, None), ("todas", "incluida", None, "P09")})
        bundle = sensorial_db.load_project_bundle(project_id, self.organization.id)
        self.assertEqual(len(bundle["sessions"]), 2)

    def test_analyses_keep_search_and_leave_the_knowledge_base_on_delete(self):
        project_id = self._project()
        with self._as(self.first_admin):
            analysis_id = sensorial_db.save_analysis(
                project_id, model="m", mode="rapida", analysis_text="texto",
                search={"searched": True}, data_version=3,
            )
            sensorial_db.set_analysis_kb_file(project_id, analysis_id, "file-kb")
        latest = sensorial_db.list_analyses(project_id)[0]
        self.assertEqual((latest["search"], latest["data_version"], latest["kb_file_id"]),
                         ({"searched": True}, 3, "file-kb"))
        with self._as(self.first_admin):
            self.assertEqual(sensorial_db.delete_analyses(project_id, [analysis_id]), 1)
        sensorial_db._remove_from_knowledge_base.assert_called_with(0, ["file-kb"])

    def test_deleting_the_project_removes_its_folder_and_knowledge(self):
        project_id = self._project()
        with self._as(self.first_admin):
            sensorial_db.add_files(project_id, [
                {"filename": "a.csv", "role": "eeg_psd", "content": b"x", "table": pd.DataFrame({"a": [1]})},
            ])
        folder = sensorial_store.project_dir(self.organization.id, project_id)
        self.assertTrue(folder.is_dir())
        with self._as(self.first_admin):
            self.assertTrue(sensorial_db.delete_project(project_id))
        self.assertFalse(folder.exists())
        sensorial_db._remove_from_knowledge_base.assert_called_with(project_id)
        self.assertIsNone(sensorial_db.get_project(project_id))

    def test_the_knowledge_base_cleanup_uses_this_modules_store(self):
        with patch("utils.kb_cleanup.remove_documents_for_project") as by_project, \
                patch("utils.kb_cleanup.remove_files") as by_file:
            _REMOVE_FROM_KNOWLEDGE_BASE(7, ["f"])
            _REMOVE_FROM_KNOWLEDGE_BASE(0, ["g"])
        by_project.assert_called_once_with(7, ("f",), module_key="teste_sensorial")
        by_file.assert_called_once_with(("g",), module_key="teste_sensorial")


class LegacyTablesTests(unittest.TestCase):
    """As tabelas `ts_*` da versao antiga ficam como estao: nada e importado nem apagado."""

    def test_the_old_tables_are_left_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            database_path = Path(folder) / "nenc-insights.db"
            organization, admin = _identity(database_path)
            # `with sqlite3.connect` so faz commit; sem fechar, o Windows nao
            # deixa apagar a pasta temporaria.
            with closing(sqlite3.connect(database_path)) as database, database:
                database.executescript(
                    """
                    CREATE TABLE ts_projects (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT);
                    CREATE TABLE ts_analyses (id INTEGER PRIMARY KEY, project_id INTEGER, analysis_text TEXT);
                    """
                )
                database.execute("INSERT INTO ts_projects VALUES (1, ?, 'Antigo')", (organization.id,))
                database.execute("INSERT INTO ts_analyses VALUES (1, 1, 'velha')")
            environment = {"NENC_DB_PATH": str(database_path), "NENC_SENSORIAL_DIR": str(Path(folder) / "s")}
            with patch.dict(os.environ, environment), \
                    patch.object(sensorial_db, "_active_organization_id", return_value=organization.id), \
                    patch.object(sensorial_db, "_require_write", return_value=admin), \
                    patch.object(sensorial_db, "_audit"), \
                    patch.object(sensorial_db, "_remove_from_knowledge_base"):
                sensorial_db.init_db()
                sensorial_db.init_db()
                project_id = sensorial_db.create_project("Novo")
                self.assertEqual(project_id, 1)
                self.assertTrue(sensorial_db.delete_project(project_id))
            with closing(sqlite3.connect(database_path)) as database:
                old_project = database.execute("SELECT name FROM ts_projects").fetchall()
                old_analysis = database.execute("SELECT analysis_text FROM ts_analyses").fetchall()
            self.assertEqual(old_project, [("Antigo",)])
            self.assertEqual(old_analysis, [("velha",)])


if __name__ == "__main__":
    unittest.main()
