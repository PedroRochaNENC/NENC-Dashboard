"""Importacoes do Teste Sensorial: motor comum, tabelas e inbox proprios, gravacao pela revisao."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import main
from tests.test_sensorial_db import _Base
from tests.test_sensorial_ingest import CANARY_WORDS, _indicadores_csv, _texts
from utils import auth, jornada_db, jornada_imports, sensorial_db, sensorial_folder, sensorial_imports
from utils.import_inbox import ImportRefused

TOKEN = "s" * 40
RUN_A = "2.DADOS/2.2/2.EEG Bruto/run_20260915-171636/"
RUN_B = "2.DADOS/2.2/2.EEG Bruto/run_20260920-090000/"
PROFILE = "Nome;Código;Sexo\nCanario Sentinela;7;F\n".encode("utf-8")
MANIFEST = json.dumps({"tipo": "eeg", "ambiente": {"usuario": "canario"},
                       "entradas": [{"arquivo": "07-Canario Sentinela.csv"}]}).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _ImportBase(_Base):
    def setUp(self):
        super().setUp()
        self.inbox = Path(self.temporary_directory.name) / "inbox"
        environment = patch.dict(os.environ, {"NENC_IMPORT_INBOX": str(self.inbox)})
        environment.start()
        self.addCleanup(environment.stop)
        self.org = self.organization.id
        self.project_id = self._project()

    def _send(self, batch_id, rel_path, data, role, chunk=300, **extra):
        result = None
        for offset in range(0, len(data), chunk):
            result = sensorial_imports.receive_chunk(
                self.org, batch_id, rel_path=rel_path, role=role, sha256=_sha(data), size=len(data),
                offset=offset, data=data[offset:offset + chunk], **extra)
        return result

    def _ready_batch(self, files):
        batch_id = sensorial_imports.create_batch(self.org, self.project_id, "X:/Estudo")
        for rel_path, data, role in files:
            self.assertTrue(self._send(batch_id, rel_path, data, role)["complete"])
        self.assertEqual(sensorial_imports.close_batch(self.org, batch_id)["status"], "pronta")
        return batch_id


class FlowTests(_ImportBase):
    def test_the_review_records_tables_on_disk_without_names(self):
        broken = b"participante,atencao\n07-Canario Sentinela,1\n"
        batch_id = self._ready_batch([
            (RUN_A + "indicadores.csv", _indicadores_csv(), "eeg_indicadores"),
            (RUN_A + "manifest.json", MANIFEST, "manifesto"),
            ("perfil/perfil.csv", PROFILE, "perfil"),
            (RUN_A + "data_quality_assessment.csv", broken, "eeg_qualidade"),
        ])
        self.assertIn(_sha(_indicadores_csv()), sensorial_imports.known_hashes(self.org, self.project_id)["pending"])
        with self._as(self.first_admin):
            report = sensorial_imports.apply_batch(self.project_id, batch_id)
        self.assertEqual((report["files"], report["duplicates"], report["participants"]), (3, 0, 1))
        self.assertEqual(len(report["skipped"]), 1)
        self.assertIn("data_quality_assessment.csv", report["skipped"][0])

        files = {f["role"]: f for f in sensorial_db.list_files(self.project_id)}
        self.assertEqual(set(files), {"eeg_indicadores", "manifesto", "perfil"})
        self.assertEqual((files["eeg_indicadores"]["storage"], files["eeg_indicadores"]["run_id"]),
                         ("disk", "run_20260915-171636"))
        bundle = sensorial_db.load_project_bundle(self.project_id, self.org)
        indicators = next(f for f in bundle["files"] if f["role"] == "eeg_indicadores")
        table = sensorial_db.read_file_table(indicators)
        self.assertEqual(table["participant_code"].dropna().unique().tolist(), ["P07"])
        text = _texts(table, [f["meta"] for f in bundle["files"]]) + json.dumps(bundle["participants"])
        for word in CANARY_WORDS:
            self.assertNotIn(word, text.lower())
        self.assertEqual(bundle["participants"][0]["profile"], {"sexo": "F"})

        statuses = {Path(f["rel_path"]).name: f["status"]
                    for f in sensorial_imports.batch_files(self.project_id, batch_id)}
        self.assertEqual(statuses["data_quality_assessment.csv"], "descartado")
        self.assertEqual(statuses["indicadores.csv"], "gravado")
        self.assertFalse(any(self.inbox.rglob("*.bin")))
        known = sensorial_imports.known_hashes(self.org, self.project_id)
        self.assertIn(_sha(_indicadores_csv()), known["files"])
        self.assertEqual(known["pending"], [])

    def test_a_new_pipeline_run_replaces_the_old_table(self):
        first = self._ready_batch([(RUN_A + "indicadores.csv", _indicadores_csv(), "eeg_indicadores")])
        with self._as(self.first_admin):
            sensorial_imports.apply_batch(self.project_id, first)
        newer = _indicadores_csv().replace(b"3.5", b"4.5")
        second = self._ready_batch([(RUN_B + "indicadores.csv", newer, "eeg_indicadores")])
        with self._as(self.first_admin):
            sensorial_imports.apply_batch(self.project_id, second)
        active = {f["run_id"]: f["is_active"] for f in sensorial_db.list_files(self.project_id)}
        self.assertEqual(active, {"run_20260915-171636": False, "run_20260920-090000": True})

    def test_two_runs_in_one_batch_keep_the_newest(self):
        newer = _indicadores_csv().replace(b"3.5", b"4.5")
        batch_id = self._ready_batch([
            (RUN_B + "indicadores.csv", newer, "eeg_indicadores"),
            (RUN_A + "indicadores.csv", _indicadores_csv(), "eeg_indicadores"),
        ])
        with self._as(self.first_admin):
            sensorial_imports.apply_batch(self.project_id, batch_id)
        active = [f["run_id"] for f in sensorial_db.list_files(self.project_id) if f["is_active"]]
        self.assertEqual(active, ["run_20260920-090000"])

    def test_the_review_can_fix_a_role(self):
        batch_id = self._ready_batch([("perfil.csv", PROFILE, "documento")])
        [entry] = sensorial_imports.batch_files(self.project_id, batch_id)
        with self._as(self.first_admin):
            report = sensorial_imports.apply_batch(self.project_id, batch_id, {entry["id"]: {"role": "perfil"}})
        self.assertEqual(report["participants"], 1)
        self.assertEqual(sensorial_db.list_files(self.project_id)[0]["role"], "perfil")

    def test_a_denied_review_keeps_the_batch_ready(self):
        batch_id = self._ready_batch([(RUN_A + "manifest.json", MANIFEST, "manifesto")])
        with patch.object(sensorial_db, "_require_write", side_effect=auth.AuthorizationError("x")):
            with self.assertRaises(auth.AuthorizationError):
                sensorial_imports.apply_batch(self.project_id, batch_id)
        self.assertEqual(sensorial_imports.list_batches(self.project_id)[0]["status"], "pronta")
        with self._as(self.first_admin):
            sensorial_imports.discard_batch(self.project_id, batch_id)
        self.assertEqual(sensorial_imports.list_batches(self.project_id), [])
        self.assertFalse(any(self.inbox.rglob("*.bin")))

    def test_roles_and_sizes_are_checked_on_arrival(self):
        batch_id = sensorial_imports.create_batch(self.org, self.project_id)
        with self.assertRaises(ImportRefused):
            self._send(batch_id, "x.csv", b"abc", "quadros")  # papel da Jornada
        with patch.dict(sensorial_folder.FILE_LIMITS, {"eeg_psd": 2}), self.assertRaises(ImportRefused):
            self._send(batch_id, "psd_results.csv", b"abc", "eeg_psd")
        self.assertEqual(sensorial_imports.file_limit("eeg_topomapa"), sensorial_folder.IMAGE_FILE_LIMIT)

    def test_deleting_the_project_removes_its_inbox(self):
        batch_id = sensorial_imports.create_batch(self.org, self.project_id)
        self._send(batch_id, RUN_A + "manifest.json", MANIFEST, "manifesto")
        self.assertTrue(any(self.inbox.rglob("*.bin")))
        with self._as(self.first_admin):
            self.assertTrue(sensorial_db.delete_project(self.project_id))
        self.assertFalse(any(self.inbox.rglob("*.bin")))


class _BothModules(_ImportBase):
    """Um projeto em cada modulo, na mesma organizacao."""

    def setUp(self):
        super().setUp()
        for patcher in (
            patch.object(jornada_db, "_require_write", return_value=self.first_admin),
            patch.object(jornada_db, "_active_organization_id", return_value=self.org),
            patch.object(jornada_db, "_audit"),
            patch.object(jornada_db, "_remove_from_knowledge_base"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        jornada_db.init_db()
        self.jornada_project = jornada_db.create_project("Da Jornada")


class ModuleIsolationTests(_BothModules):
    def test_projects_with_the_same_id_never_cross(self):
        self.assertEqual(self.jornada_project, self.project_id)  # mesmo id nos dois módulos
        self.assertEqual([p["name"] for p in sensorial_imports.list_projects(self.org)], ["Estudo"])
        self.assertEqual([p["name"] for p in jornada_imports.list_projects(self.org)], ["Da Jornada"])
        batch_id = sensorial_imports.create_batch(self.org, self.project_id)
        self._send(batch_id, RUN_A + "manifest.json", MANIFEST, "manifesto")
        stored = list(self.inbox.rglob("*.bin"))
        self.assertTrue(stored and all("teste_sensorial" in path.parts for path in stored))
        self.assertEqual(jornada_imports.known_hashes(self.org, self.jornada_project)["pending"], [])
        self.assertEqual(jornada_imports.list_batches(self.jornada_project), [])
        # Excluir o inbox do projeto da Jornada não toca no do sensorial, de mesmo id.
        jornada_imports.remove_project_inbox(self.org, self.jornada_project)
        self.assertEqual(list(self.inbox.rglob("*.bin")), stored)


class ApiRoutesTests(_BothModules):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, {"NENC_IMPORT_TOKEN_{}".format(self.org): TOKEN})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(main.app)
        self.headers = {"Authorization": "Bearer {}".format(TOKEN)}

    def test_each_prefix_reaches_its_own_module(self):
        names = {}
        for prefix in main.MODULES:
            response = self.client.get(prefix + "/projects", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            names[prefix] = [p["name"] for p in response.json()]
        self.assertEqual(names, {"/api/jornada": ["Da Jornada"], "/api/importacao/jornada_compra": ["Da Jornada"],
                                 "/api/importacao/teste_sensorial": ["Estudo"]})
        self.assertEqual(self.client.get("/api/importacao/teste_sensorial/projects").status_code, 401)

    def test_a_sensory_upload_lands_in_its_tables(self):
        base = "/api/importacao/teste_sensorial"
        opened = self.client.post(base + "/imports", headers=self.headers,
                                  json={"project_id": self.project_id}).json()
        self.assertEqual(opened["known"], {"files": [], "media": [], "pending": [], "documents": []})
        response = self.client.put(base + "/imports/{}/files".format(opened["batch_id"]), content=MANIFEST,
                                   headers=dict(self.headers, **{
                                       "X-File-Path": "run_20260915-171636/manifest.json",
                                       "X-File-Role": "manifesto", "X-File-Sha256": _sha(MANIFEST),
                                       "X-File-Size": str(len(MANIFEST)), "X-Chunk-Offset": "0"}))
        self.assertTrue(response.json()["complete"], response.text)
        self.assertEqual(self.client.post(base + "/imports/{}/close".format(opened["batch_id"]),
                                          headers=self.headers, json={}).json()["status"], "pronta")
        with closing(sqlite3.connect(self.database_path)) as database:
            sensory = database.execute("SELECT COUNT(*) FROM sens_import_files").fetchone()[0]
            journey = database.execute("SELECT COUNT(*) FROM jc_import_files").fetchone()[0]
        self.assertEqual((sensory, journey), (1, 0))
