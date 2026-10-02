"""Script que envia a pasta do projeto: simulação, envio pela API e retomada.

A pasta sintética segue a estrutura NENC, com marcas e lojas genéricas. O
envio passa pela API de verdade (TestClient) até virar importação pendente.
"""

import hashlib
import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import main
from scripts import jornada_enviar
from scripts.jornada_enviar import Item, SendError, compress_video, document_text, find_project, run, upload
from tests.test_briefing import _docx_bytes
from tests.test_jornada_choice import _field_log
from tests.test_jornada_imports import _ImportBase
from tests.test_jornada_model import SAMPLES
from utils.jornada_folder import Entry

TOKEN = "k" * 40
BRIEFING = _docx_bytes(*[
    "Objetivo {}: entender como a shopper escolhe na gôndola de absorventes, comparando o atacarejo "
    "com a farma e a jornada livre com a estimulada, com atenção às embalagens.".format(n) for n in range(6)
])
TEMPLATE = _docx_bytes("TERMO DE ABERTURA", "Cliente:", "Projeto:", "Data:")
CENA = "2.DADOS/2.2.Dados Processados/Eyetracking/Jornadas Livres-1234/Pt01-JLivre-DSP1234-out.mp4"
HEATMAP = "2.DADOS/2.2.Dados Processados/Videos Processados Heatmap/Livre-1234/Pt01-JLivre-DSP1234.mp4"
FILES = {
    "1.GESTAO_PROJETOS/1.1 Documentação/Briefing do estudo.docx": BRIEFING,
    "1.GESTAO_PROJETOS/1.1 Documentação/TERMO DE ABERTURA.docx": TEMPLATE,
    "1.GESTAO_PROJETOS/1.1 Documentação/Proposta.pdf": b"%PDF-1.4",
    "1.GESTAO_PROJETOS/1.4.Campo/Relação Coletas.xlsx": _field_log(),
    "1.GESTAO_PROJETOS/1.4.Campo/Fotos participantes/Pt01.jpg": b"\xff\xd8rosto",
    "2.DADOS/2.2.Dados Processados/Eyetracking/Jornadas Livres-1234/Pt01-JLivre-DSP1234.csv":
        b"frame,timestamp,x,y\n0,0.0,1,1\n",
    CENA: b"cena" * 1000,
    HEATMAP: b"heat" * 1000,
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/DSP1234/DSP1234-INDIVIDUAL2.csv": SAMPLES,
    "4.ARQUIVOS AUXILIARES/Fotos pacotes/Marca A/Frente editada.jpeg": b"\xff\xd8frente",
}
SENT = {
    "1.GESTAO_PROJETOS/1.1 Documentação/Briefing do estudo.docx": "documento",
    "1.GESTAO_PROJETOS/1.4.Campo/Relação Coletas.xlsx": "registro_campo",
    "2.DADOS/2.2.Dados Processados/Eyetracking/Jornadas Livres-1234/Pt01-JLivre-DSP1234.csv": "quadros",
    CENA: "video_cena",
    HEATMAP: "video_heatmap",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/DSP1234/DSP1234-INDIVIDUAL2.csv": "dados",
    "4.ARQUIVOS AUXILIARES/Fotos pacotes/Marca A/Frente editada.jpeg": "imagem",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(root: Path, files) -> None:
    for rel_path, data in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _response(status_code=200, payload=None):
    return SimpleNamespace(status_code=status_code, json=lambda: payload, text=json.dumps(payload))


class _Interrupted:
    """Cliente que cai (Ctrl+C) depois de alguns blocos."""

    def __init__(self, client, after):
        self.client, self.after, self.calls = client, after, 0

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *error):
        return self.client.__exit__(*error)

    def get(self, *args, **kwargs):
        return self.client.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self.client.post(*args, **kwargs)

    def put(self, *args, **kwargs):
        self.calls += 1
        if self.calls > self.after:
            raise KeyboardInterrupt
        return self.client.put(*args, **kwargs)


class SendTests(_ImportBase):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, {
            "NENC_IMPORT_TOKEN_{}".format(self.org): TOKEN,
            "NENC_IMPORT_TOKEN": TOKEN,
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.root = Path(self.temporary_directory.name) / "Estudo"  # mesmo nome do projeto
        _write(self.root, FILES)
        self.cache = Path(self.temporary_directory.name) / "cache"
        self.lines = []

    def _client(self, base_url, token):
        return TestClient(main.app, headers={"Authorization": "Bearer {}".format(token)})

    def _run(self, *extra, client_factory=None, ask=None):
        self.lines = []
        argv = [str(self.root), "-y", "--sem-compactar", "--cache", str(self.cache)] + list(extra)
        kwargs = {"client_factory": client_factory or self._client, "out": self.lines.append}
        if ask is not None:
            kwargs["ask"] = ask
        return run(argv, **kwargs)

    def _batches(self):
        with closing(sqlite3.connect(self.database_path)) as database:
            database.row_factory = sqlite3.Row
            batches = [dict(row) for row in database.execute("SELECT * FROM jc_import_batches ORDER BY id")]
            for batch in batches:
                batch["files"] = {row["rel_path"]: dict(row) for row in database.execute(
                    "SELECT * FROM jc_import_files WHERE batch_id = ?", (batch["id"],))}
        return batches

    def test_the_simulation_shows_the_plan_without_network(self):
        def no_network(*_):
            raise AssertionError("a simulação não pode abrir conexão")

        self.assertEqual(run([str(self.root), "--simular"], client_factory=no_network, out=self.lines.append), 0)
        text = "\n".join(self.lines)
        self.assertIn("Vídeos de heatmap", text)
        self.assertIn("briefing: Briefing do estudo.docx", text)
        self.assertIn("fotos de participantes são dado pessoal", text)
        self.assertIn("modelo de documento sem preencher", text)
        self.assertIn("não extrai texto de PDF", text)
        self.assertIn("Simulação: nada foi enviado.", text)

    def test_the_folder_becomes_one_pending_import(self):
        self.assertEqual(self._run(), 0, self.lines)
        [batch] = self._batches()
        self.assertEqual(batch["status"], "pronta")
        self.assertEqual({path: f["role"] for path, f in batch["files"].items()}, SENT)
        self.assertFalse(any("Fotos participantes" in path for path in batch["files"]))
        reasons = {item["rel_path"]: item["reason"] for item in json.loads(batch["ignored_json"])}
        self.assertIn("dado pessoal", reasons["1.GESTAO_PROJETOS/1.4.Campo/Fotos participantes/Pt01.jpg"])
        document = batch["files"]["1.GESTAO_PROJETOS/1.1 Documentação/Briefing do estudo.docx"]
        text = document_text(self.root / "1.GESTAO_PROJETOS/1.1 Documentação/Briefing do estudo.docx")
        self.assertEqual(document["sha256"], _sha(text.encode("utf-8")))
        self.assertEqual(json.loads(document["meta_json"])["original_name"], "Briefing do estudo.docx")
        video = batch["files"][CENA]
        self.assertEqual((video["sha256"], video["source_sha256"]), (_sha(FILES[CENA]), None))
        self.assertEqual(json.loads(video["meta_json"])["participant"], "Pt01")
        self.assertEqual(json.loads(batch["summary_json"])["enviados"], len(SENT))
        self.assertTrue(any("Uploads > Importações pendentes" in line for line in self.lines))

    def test_sending_again_only_sends_what_is_new_and_reuses_the_hashes(self):
        self._run()
        with patch.object(jornada_enviar, "sha256_file", wraps=jornada_enviar.sha256_file) as hashed:
            self.assertEqual(self._run(), 0)
        self.assertEqual(hashed.call_count, 0)  # nada mudou: os hashes vêm do cache
        self.assertIn("Nada novo", "\n".join(self.lines))
        self.assertEqual([b["status"] for b in self._batches()], ["pronta", "descartada"])
        _write(self.root, {"4.ARQUIVOS AUXILIARES/Fotos pacotes/Marca B/Verso.jpeg": b"\xff\xd8verso"})
        self._run()
        latest = self._batches()[-1]
        self.assertEqual((latest["status"], list(latest["files"])),
                         ("pronta", ["4.ARQUIVOS AUXILIARES/Fotos pacotes/Marca B/Verso.jpeg"]))

    def test_an_interrupted_send_continues_in_the_same_import(self):
        # Blocos de 1500 bytes: o registro de campo leva 5, os quatro arquivos pequenos 1 cada e
        # o 10º bloco começa o vídeo de cena — cai no meio dele.
        with patch.object(jornada_enviar, "CHUNK_BYTES", 1500):
            first = self._run(client_factory=lambda url, token: _Interrupted(self._client(url, token), after=10))
            self.assertEqual(first, 130)
            [open_batch] = self._batches()
            self.assertEqual(open_batch["status"], "recebendo")
            self.assertEqual(open_batch["files"][CENA]["status"], "recebendo")
            self.assertEqual(self._run(), 0, self.lines)
        self.assertTrue(any("Continuando o envio interrompido" in line for line in self.lines))
        waiting = [line for line in self.lines if "já enviado, esperando revisão" in line]
        self.assertEqual(len(waiting), 5)
        self.assertTrue(any("5 já tinham sido enviados antes" in line for line in self.lines))
        [batch] = self._batches()
        self.assertEqual((batch["id"], batch["status"]), (open_batch["id"], "pronta"))
        self.assertEqual(set(batch["files"]), set(SENT))
        self.assertTrue(all(f["status"] == "completo" for f in batch["files"].values()))

    def test_videos_go_compressed_with_the_hash_of_the_original(self):
        def fake_ffmpeg(command, **_):
            Path(command[-1]).write_bytes(b"compactado:" + Path(command[command.index("-i") + 1]).name.encode())
            return SimpleNamespace(returncode=0, stderr="")

        with patch.object(jornada_enviar, "find_ffmpeg", return_value="ffmpeg"), \
                patch.object(jornada_enviar.subprocess, "run", side_effect=fake_ffmpeg):
            self.assertEqual(run([str(self.root), "-y", "--cache", str(self.cache)], client_factory=self._client,
                                 out=self.lines.append), 0, self.lines)
        [batch] = self._batches()
        for rel_path in (CENA, HEATMAP):
            video = batch["files"][rel_path]
            self.assertEqual(video["source_sha256"], _sha(FILES[rel_path]))
            self.assertNotEqual(video["sha256"], _sha(FILES[rel_path]))
            self.assertTrue(json.loads(video["meta_json"])["compactado"])
        self.assertEqual(list((self.cache / "videos").glob("*.mp4")), [])  # o cache só servia para retomar

    def test_the_compressed_cache_is_cleaned_after_a_resumed_send(self):
        def fake_ffmpeg(command, **_):
            Path(command[-1]).write_bytes(b"compactado:" + Path(command[command.index("-i") + 1]).name.encode())
            return SimpleNamespace(returncode=0, stderr="")

        argv = [str(self.root), "-y", "--cache", str(self.cache)]
        videos = self.cache / "videos"
        with patch.object(jornada_enviar, "find_ffmpeg", return_value="ffmpeg"), \
                patch.object(jornada_enviar.subprocess, "run", side_effect=fake_ffmpeg):
            # Um bloco por arquivo: o 7º envio é o heatmap, com o vídeo de cena já no servidor.
            first = run(argv, client_factory=lambda url, token: _Interrupted(self._client(url, token), after=6),
                        out=self.lines.append)
            self.assertEqual(first, 130)
            self.assertEqual(len(list(videos.glob("*.mp4"))), 2)
            self.assertEqual(run(argv, client_factory=self._client, out=self.lines.append), 0, self.lines)
        self.assertEqual(list(videos.glob("*.mp4")), [])  # o da rodada interrompida também saiu

    def test_token_problems_and_a_no_stop_before_anything_is_sent(self):
        with patch.dict(os.environ, {"NENC_IMPORT_TOKEN": ""}):
            self.assertEqual(self._run(), 2)
        self.assertIn("NENC_IMPORT_TOKEN", self.lines[-1])
        with patch.dict(os.environ, {"NENC_IMPORT_TOKEN": "x" * 40}):
            self.assertEqual(self._run(), 1)
        self.assertTrue(any("recusou o token" in line for line in self.lines))
        argv = [str(self.root), "--sem-compactar", "--cache", str(self.cache)]
        self.assertEqual(run(argv, client_factory=self._client, out=self.lines.append, ask=lambda _: "n"), 1)
        self.assertEqual(self._batches(), [])

    def test_a_project_that_does_not_exist_stops_with_the_list(self):
        self.assertEqual(self._run("--projeto", "Outro estudo"), 1)
        self.assertTrue(any("Estudo (id {})".format(self.project_id) in line for line in self.lines))
        self.assertEqual(self._batches(), [])


class PieceTests(unittest.TestCase):
    def test_the_project_is_found_by_id_name_or_name_without_punctuation(self):
        projects = [{"id": 3, "name": "1234-Estudo"}, {"id": 4, "name": "Outro"}]
        client = SimpleNamespace(get=lambda *_: _response(200, projects))
        self.assertEqual(find_project(client, "1234 estudo")["id"], 3)
        self.assertEqual(find_project(client, "4")["id"], 4)
        with self.assertRaises(SendError) as error:
            find_project(client, "1061")
        self.assertIn("1234-Estudo (id 3)", str(error.exception))

    def test_upload_retries_the_network_and_follows_the_server_offset(self):
        item = Item(Entry("a.csv", 10, "dados"), Path("a.csv"), payload=b"0123456789")
        calls = []

        def put(url, content, headers):
            calls.append((int(headers["X-Chunk-Offset"]), content))
            if len(calls) <= 2:
                raise ConnectionError("caiu")
            if len(calls) == 3:  # o servidor já tinha 4 bytes (envio retomado)
                return _response(409, {"expected_offset": 4})
            return _response(200, {"received": 10, "complete": True})

        upload(SimpleNamespace(put=put), 1, item, _sha(b"0123456789"), sleep=lambda _: None)
        self.assertEqual(calls[-1], (4, b"456789"))

    def test_an_upload_that_does_not_move_stops(self):
        item = Item(Entry("a.csv", 3, "dados"), Path("a.csv"), payload=b"abc")
        stuck = SimpleNamespace(put=lambda *a, **k: _response(409, {"expected_offset": 0}))
        with self.assertRaises(SendError):
            upload(stuck, 1, item, _sha(b"abc"), sleep=lambda _: None)

    def test_compression_keeps_the_original_when_it_does_not_shrink_or_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "video.mp4"
            source.write_bytes(b"v" * 100)
            cache = Path(folder) / "cache"

            def bigger(command, **_):
                Path(command[-1]).write_bytes(b"x" * 200)
                return SimpleNamespace(returncode=0, stderr="")

            self.assertIsNone(compress_video(source, "a" * 64, "ffmpeg", cache, run=bigger))
            self.assertEqual(list(cache.iterdir()), [])
            failed = lambda command, **_: SimpleNamespace(returncode=1, stderr="codec ausente")  # noqa: E731
            with self.assertRaises(ValueError):
                compress_video(source, "a" * 64, "ffmpeg", cache, run=failed)

            def smaller(command, **_):
                Path(command[-1]).write_bytes(b"x" * 10)
                return SimpleNamespace(returncode=0, stderr="")

            first = compress_video(source, "b" * 64, "ffmpeg", cache, run=smaller)
            again = compress_video(source, "b" * 64, "ffmpeg", cache, run=failed)  # do cache, sem ffmpeg
            self.assertEqual((first, again), (cache / ("b" * 64 + ".mp4"),) * 2)


if __name__ == "__main__":
    unittest.main()
