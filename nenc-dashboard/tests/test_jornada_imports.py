"""Importações pendentes: o que a API recebe e o que a revisão grava.

A metade da API trabalha com a organização explícita (token) e nunca grava
dados de análise; a metade da tela grava como o revisor, com as guardas de
escrita da Jornada.
"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tests.test_jornada_choice import _field_log
from tests.test_jornada_db import _Base
from tests.test_jornada_model import SAMPLES
from utils import auth, jornada_db, jornada_imports, jornada_media
from utils.jornada_imports import ImportRefused


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _ImportBase(_Base):
    def setUp(self):
        super().setUp()
        inbox = patch.dict(os.environ, {"NENC_IMPORT_INBOX": str(Path(self.temporary_directory.name) / "inbox")})
        inbox.start()
        self.addCleanup(inbox.stop)
        self.project_id = self._project()
        self.org = self.organization.id

    def _send(self, batch_id, rel_path, role, data, meta=None, chunk=None, source_sha256=None):
        chunk = chunk or len(data)
        result = None
        for offset in range(0, len(data), chunk):
            result = jornada_imports.receive_chunk(
                self.org, batch_id, rel_path=rel_path, role=role, sha256=_sha(data), size=len(data),
                offset=offset, data=data[offset:offset + chunk], meta=meta, source_sha256=source_sha256,
            )
        return result


class ReceiveTests(_ImportBase):
    def test_a_batch_only_opens_on_a_project_of_the_token_organization(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id, "X:/Estudo", {"versao": "1"})
        self.assertGreater(batch_id, 0)
        other = auth.create_organization("Outra", actor=self.platform_admin, database_path=self.database_path)
        with self.assertRaises(ImportRefused):
            jornada_imports.create_batch(other.id, self.project_id)
        self.assertEqual([p["id"] for p in jornada_imports.list_projects(self.org)], [self.project_id])
        self.assertEqual(jornada_imports.list_projects(other.id), [])

    def test_chunks_arrive_in_order_resume_and_are_checked_at_the_end(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        data = b"0123456789" * 3
        first = jornada_imports.receive_chunk(
            self.org, batch_id, rel_path="a/b.csv", role="dados", sha256=_sha(data), size=len(data),
            offset=0, data=data[:10])
        self.assertEqual((first["received"], first["complete"]), (10, False))
        # O mesmo bloco de novo (retentativa) não grava duas vezes.
        again = jornada_imports.receive_chunk(
            self.org, batch_id, rel_path="a/b.csv", role="dados", sha256=_sha(data), size=len(data),
            offset=0, data=data[:10])
        self.assertEqual(again["received"], 10)
        with self.assertRaises(ImportRefused):  # pulou um bloco
            jornada_imports.receive_chunk(
                self.org, batch_id, rel_path="a/b.csv", role="dados", sha256=_sha(data), size=len(data),
                offset=20, data=data[20:])
        self.assertEqual(jornada_imports.file_status(self.org, batch_id, "a/b.csv")["received"], 10)
        last = jornada_imports.receive_chunk(
            self.org, batch_id, rel_path="a/b.csv", role="dados", sha256=_sha(data), size=len(data),
            offset=10, data=data[10:])
        self.assertTrue(last["complete"])

    def test_a_wrong_hash_or_size_is_refused(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        with self.assertRaises(ImportRefused):
            jornada_imports.receive_chunk(self.org, batch_id, rel_path="x.csv", role="dados", sha256="0" * 64,
                                          size=3, offset=0, data=b"abc")
        self.assertFalse(jornada_imports.file_status(self.org, batch_id, "x.csv")["complete"])
        with self.assertRaises(ImportRefused):
            jornada_imports.receive_chunk(self.org, batch_id, rel_path="y.csv", role="dados",
                                          sha256=_sha(b"abc"), size=jornada_db.MAX_FILE_BYTES + 1, offset=0,
                                          data=b"abc")
        with self.assertRaises(ImportRefused):
            jornada_imports.receive_chunk(self.org, batch_id, rel_path="z.csv", role="ignorado",
                                          sha256=_sha(b"abc"), size=3, offset=0, data=b"abc")

    def test_the_path_is_only_metadata(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        self._send(batch_id, "../../etc/passwd.csv", "dados", b"abc")
        with closing(sqlite3.connect(self.database_path)) as database:
            rel_path = database.execute("SELECT rel_path FROM jc_import_files").fetchone()[0]
        self.assertEqual(rel_path, "etc/passwd.csv")
        files = list((jornada_imports.inbox_root()).rglob("*"))
        self.assertTrue(all(jornada_imports.inbox_root() in f.parents for f in files))

    def test_close_needs_every_file_complete_and_known_hashes_include_pending(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        data = b"conteudo"
        jornada_imports.receive_chunk(self.org, batch_id, rel_path="a.csv", role="dados", sha256=_sha(data),
                                      size=len(data), offset=0, data=data[:3])
        with self.assertRaises(ImportRefused):
            jornada_imports.close_batch(self.org, batch_id)
        jornada_imports.receive_chunk(self.org, batch_id, rel_path="a.csv", role="dados", sha256=_sha(data),
                                      size=len(data), offset=3, data=data[3:])
        self.assertIn(_sha(data), jornada_imports.known_hashes(self.org, self.project_id)["pending"])
        closed = jornada_imports.close_batch(self.org, batch_id, {"dados": 1}, [{"rel_path": "x", "reason": "y"}])
        self.assertEqual(closed["status"], "pronta")
        with self.assertRaises(ImportRefused):  # fechado não recebe mais
            self._send(batch_id, "b.csv", "dados", b"novo")
        with closing(sqlite3.connect(self.database_path)) as database:
            actions = [row[0] for row in database.execute(
                "SELECT action FROM audit_log WHERE actor_user_id IS NULL ORDER BY id")]
        self.assertEqual(actions[-2:], ["jornada.import.received", "jornada.import.closed"])


class ApplyTests(_ImportBase):
    def _ready_batch(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        self._send(batch_id, "2.DADOS/2.3/DSP1234/DSP1234-INDIVIDUAL2.csv", "dados", SAMPLES, chunk=50)
        self._send(batch_id, "1.GESTAO/Relação Coletas.xlsx", "registro_campo", _field_log())
        self._send(batch_id, "4.AUX/Fotos pacotes/Marca A/Frente.jpeg", "imagem", b"\xff\xd8imagem",
                   meta={"category": "embalagem", "brand": "Marca A", "view": "frente"})
        self._send(batch_id, "2.DADOS/2.2/Videos Processados Heatmap/Pt01-JLivre-DSP1234.mp4", "video_heatmap",
                   b"video-compactado", meta={"participant": "Pt01", "task": "livre", "store": "1234"},
                   source_sha256="b" * 64)
        self._send(batch_id, "1.GESTAO/1.1 Documentação/Briefing.docx.txt", "documento",
                   "Objetivo do estudo: entender a gôndola.".encode("utf-8"),
                   meta={"doc_type": "briefing", "original_name": "Briefing.docx"})
        jornada_imports.close_batch(self.org, batch_id)
        return batch_id

    def test_the_review_writes_everything_as_the_reviewer(self):
        batch_id = self._ready_batch()
        sent = []
        with self._as(self.first_admin):
            report = jornada_imports.apply_batch(
                self.project_id, batch_id, send_document=lambda name, text, meta: sent.append((name, text)))
        self.assertEqual((report["files"], report["videos"], report["documents"]), (3, 1, 1))
        kinds = sorted(f["kind"] for f in jornada_db.list_files(self.project_id))
        self.assertEqual(kinds, ["bs_individual", "field_log", "image"])
        image = [f for f in jornada_db.list_files(self.project_id) if f["kind"] == "image"][0]
        self.assertEqual((image["meta"]["brand"], image["meta"]["category"]), ("Marca A", "embalagem"))
        media = jornada_db.list_media(self.project_id)
        self.assertEqual((media[0]["kind"], media[0]["source_sha256"], media[0]["participant_code"]),
                         ("heatmap", "b" * 64, "Pt01"))
        self.assertTrue(jornada_media.resolve(media[0]["rel_path"]).exists())
        self.assertEqual(jornada_db.get_project(self.project_id)["briefing_filename"], "Briefing.docx")
        self.assertEqual(sent[0][0], "Briefing.docx")
        batch = jornada_imports.list_batches(self.project_id, ("gravada",))[0]
        self.assertEqual(batch["decided_by_user_id"], self.first_admin.id)
        self.assertFalse(any(jornada_imports.inbox_root().rglob("*.bin")))

    def test_a_second_send_of_the_same_original_is_known_and_does_not_duplicate(self):
        batch_id = self._ready_batch()
        with self._as(self.first_admin):
            jornada_imports.apply_batch(self.project_id, batch_id)
        known = jornada_imports.known_hashes(self.org, self.project_id)
        self.assertIn("b" * 64, known["media"])
        self.assertIn(_sha(SAMPLES), known["files"])

    def test_a_read_only_account_cannot_write_and_the_batch_stays_ready(self):
        batch_id = self._ready_batch()
        with patch.object(jornada_db, "_require_write", side_effect=auth.AuthorizationError("x")):
            with self.assertRaises(auth.AuthorizationError):
                jornada_imports.apply_batch(self.project_id, batch_id)
        self.assertEqual(jornada_imports.list_batches(self.project_id)[0]["status"], "pronta")

    def test_a_failure_in_the_middle_puts_the_batch_back_to_ready(self):
        batch_id = self._ready_batch()
        with self._as(self.first_admin), patch.object(jornada_db, "add_media", side_effect=RuntimeError("disco")):
            with self.assertRaises(RuntimeError):
                jornada_imports.apply_batch(self.project_id, batch_id)
        self.assertEqual(jornada_imports.list_batches(self.project_id)[0]["status"], "pronta")

    def test_discard_removes_the_files_and_only_ready_batches_are_written(self):
        batch_id = self._ready_batch()
        with self._as(self.first_admin):
            jornada_imports.discard_batch(self.project_id, batch_id)
            with self.assertRaises(ValueError):
                jornada_imports.apply_batch(self.project_id, batch_id)
        self.assertEqual(jornada_imports.list_batches(self.project_id), [])
        self.assertFalse(any(jornada_imports.inbox_root().rglob("*.bin")))

    def test_stale_batches_expire(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        self._send(batch_id, "a.csv", "dados", b"abc")
        future = datetime.now() + timedelta(days=31)
        self.assertEqual(jornada_imports.expire_stale(now=future), 1)
        self.assertEqual(jornada_imports.list_batches(self.project_id, ("expirada",))[0]["id"], batch_id)
        self.assertFalse(any(jornada_imports.inbox_root().rglob("*.bin")))

    def test_deleting_the_project_clears_its_inbox(self):
        batch_id = jornada_imports.create_batch(self.org, self.project_id)
        self._send(batch_id, "a.csv", "dados", b"abc")
        with self._as(self.first_admin):
            self.assertTrue(jornada_db.delete_project(self.project_id))
        self.assertFalse(any(jornada_imports.inbox_root().rglob("*.bin")))


if __name__ == "__main__":
    unittest.main()
