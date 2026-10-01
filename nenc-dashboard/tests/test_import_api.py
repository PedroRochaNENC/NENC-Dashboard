"""API de importação: token por organização, blocos e nada além de pendências."""

import hashlib
import json
import os
import sqlite3
import unittest
from contextlib import closing
from urllib.parse import quote
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import main
from tests.test_jornada_db import _Base
from utils import auth, jornada_imports

TOKEN = "t" * 40
OTHER_TOKEN = "o" * 40


class ApiTests(_Base):
    def setUp(self):
        super().setUp()
        self.project_id = self._project()
        self.other = auth.create_organization("Outra", actor=self.platform_admin, database_path=self.database_path)
        environment = patch.dict(os.environ, {
            "NENC_IMPORT_TOKEN_{}".format(self.organization.id): TOKEN,
            "NENC_IMPORT_TOKEN_{}".format(self.other.id): OTHER_TOKEN,
            "NENC_IMPORT_TOKEN_999": "curto",  # menos de 32 caracteres: ignorado
            "NENC_IMPORT_INBOX": os.path.join(self.temporary_directory.name, "inbox"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(main.app)

    def _auth(self, token=TOKEN):
        return {"Authorization": "Bearer {}".format(token)}

    def _put(self, batch_id, path, data, offset=0, chunk=None, role="dados", meta=None, token=TOKEN):
        body = data[offset:offset + (chunk or len(data))]
        headers = dict(self._auth(token), **{
            "X-File-Path": quote(path),
            "X-File-Role": role,
            "X-File-Sha256": hashlib.sha256(data).hexdigest(),
            "X-File-Size": str(len(data)),
            "X-Chunk-Offset": str(offset),
            "X-File-Meta": quote(json.dumps(meta or {}, ensure_ascii=False)),
        })
        return self.client.put("/api/jornada/imports/{}/files".format(batch_id), content=body, headers=headers)

    def test_health_is_open_and_everything_else_needs_a_token(self):
        self.assertEqual(self.client.get("/api/jornada/health").json(), {"ok": True})
        self.assertEqual(self.client.get("/api/jornada/projects").status_code, 401)
        self.assertEqual(self.client.get("/api/jornada/projects", headers=self._auth("x" * 40)).status_code, 401)
        self.assertEqual(self.client.get("/api/jornada/projects", headers=self._auth("curto")).status_code, 401)
        wrong_scheme = {"Authorization": "Basic {}".format(TOKEN)}
        self.assertEqual(self.client.get("/api/jornada/projects", headers=wrong_scheme).status_code, 401)

    def test_a_token_only_sees_its_own_organization(self):
        mine = self.client.get("/api/jornada/projects", headers=self._auth()).json()
        self.assertEqual([p["id"] for p in mine], [self.project_id])
        self.assertEqual(self.client.get("/api/jornada/projects", headers=self._auth(OTHER_TOKEN)).json(), [])
        response = self.client.post("/api/jornada/imports", json={"project_id": self.project_id},
                                    headers=self._auth(OTHER_TOKEN))
        self.assertEqual(response.status_code, 404)

    def test_the_full_flow_leaves_only_a_pending_import(self):
        opened = self.client.post("/api/jornada/imports", headers=self._auth(),
                                  json={"project_id": self.project_id, "source_label": "X:/Estudo"}).json()
        batch_id = opened["batch_id"]
        self.assertEqual(opened["known"], {"files": [], "media": [], "pending": []})
        data = "frame,timestamp,x,y\n0,0.0,1,1\n1,0.05,1,1\n".encode("utf-8") * 10
        path = "2.DADOS/Pasta com acentuação/Pt01-JLivre-DSP1234.csv"
        first = self._put(batch_id, path, data, 0, 100, role="quadros")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["complete"])
        skipped = self._put(batch_id, path, data, 300, 100, role="quadros")
        self.assertEqual(skipped.status_code, 409)
        self.assertEqual(skipped.json()["expected_offset"], 100)
        status = self.client.get("/api/jornada/imports/{}/files/status".format(batch_id),
                                 params={"path": path}, headers=self._auth()).json()
        self.assertEqual(status["received"], 100)
        offset = 100
        while offset < len(data):
            response = self._put(batch_id, path, data, offset, 100, role="quadros")
            offset += 100
        self.assertTrue(response.json()["complete"])
        closed = self.client.post("/api/jornada/imports/{}/close".format(batch_id), headers=self._auth(),
                                  json={"summary": {"quadros": 1}, "ignored": []})
        self.assertEqual(closed.json()["status"], "pronta")
        with closing(sqlite3.connect(self.database_path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM jc_files").fetchone()[0], 0)
            stored = database.execute("SELECT rel_path, role FROM jc_import_files").fetchone()
        self.assertEqual(stored, (path, "quadros"))

    def test_bad_headers_and_big_chunks_are_refused(self):
        batch_id = self.client.post("/api/jornada/imports", headers=self._auth(),
                                    json={"project_id": self.project_id}).json()["batch_id"]
        response = self.client.put("/api/jornada/imports/{}/files".format(batch_id), content=b"abc",
                                   headers=dict(self._auth(), **{"X-File-Path": "a.csv"}))
        self.assertEqual(response.status_code, 400)
        with patch.object(main, "CHUNK_MAX_BYTES", 2):
            self.assertEqual(self._put(batch_id, "a.csv", b"abcdef").status_code, 413)
        self.assertEqual(self._put(batch_id, "a.csv", b"abc", role="ignorado").status_code, 400)
        self.assertEqual(self._put(batch_id, "a.csv", b"abc", token=OTHER_TOKEN).status_code, 404)


if __name__ == "__main__":
    unittest.main()
