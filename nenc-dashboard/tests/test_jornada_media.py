import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import jornada_media, static_media


class VideoStorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        environment = patch.dict(os.environ, {"NENC_MEDIA_DIR": self.folder.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_the_video_is_named_by_its_content(self):
        saved = jornada_media.save_video(3, 7, "Pt01-JLivre-X1-out.mp4", io.BytesIO(b"quadros"))
        again = jornada_media.save_video(3, 7, "copia.MP4", b"quadros")

        self.assertEqual(saved["rel_path"], again["rel_path"])
        self.assertTrue(saved["rel_path"].startswith("3/7/"))
        self.assertNotIn("Pt01", saved["rel_path"])
        self.assertEqual(saved["size_bytes"], len(b"quadros"))
        self.assertEqual(jornada_media.resolve(saved["rel_path"]).read_bytes(), b"quadros")
        # Sem sobras de escrita pela metade.
        self.assertEqual(
            [p.suffix for p in jornada_media.project_dir(3, 7).iterdir()], [".mp4"]
        )

    def test_only_video_formats_are_accepted(self):
        with self.assertRaises(ValueError):
            jornada_media.save_video(3, 7, "planilha.csv", b"x")
        with self.assertRaises(ValueError):
            jornada_media.save_video(3, 7, "vazio.mp4", b"")

    def test_paths_cannot_escape_the_media_folder(self):
        with self.assertRaises(ValueError):
            jornada_media.resolve("../../fora.mp4")

    def test_deleting_files_and_the_project_folder(self):
        saved = jornada_media.save_video(3, 7, "v.mp4", b"abc")
        self.assertEqual(jornada_media.delete_files([saved["rel_path"], "", "3/7/nao.mp4"]), 1)
        jornada_media.save_video(3, 7, "w.mp4", b"def")
        jornada_media.delete_project_dir(3, 7)
        self.assertFalse(jornada_media.project_dir(3, 7).exists())

    def test_default_folder_sits_next_to_the_database(self):
        with patch.dict(
            os.environ, {"NENC_MEDIA_DIR": "", "NENC_DB_PATH": "/dados/banco.db"}
        ):
            self.assertEqual(
                jornada_media.media_root(), Path("/dados/banco.db").parent / "jornada_media"
            )


class StaticPublishTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.static = Path(self.folder.name) / "static"
        self.source = Path(self.folder.name) / "origem.mp4"
        self.source.write_bytes(b"video")

    def test_publish_copies_once_and_returns_the_relative_url(self):
        name = static_media.new_name("video_", ".mp4")
        url = static_media.publish(self.source, name, static_dir=self.static)
        self.assertEqual(url, "app/static/" + name)
        self.assertEqual((self.static / name).read_bytes(), b"video")
        self.assertEqual(static_media.publish(self.source, name, static_dir=self.static), url)
        self.assertNotEqual(name, static_media.new_name("video_", ".mp4"))

    def test_only_old_copies_of_the_prefix_are_purged(self):
        self.static.mkdir()
        old = self.static / "video_velho.mp4"
        fresh = self.static / "video_novo.mp4"
        other = self.static / "audio_velho.wav"
        for path in (old, fresh, other):
            path.write_bytes(b"x")
        past = time.time() - 3 * 60 * 60
        os.utime(old, (past, past))
        os.utime(other, (past, past))

        removed = static_media.purge_stale("video_", (".mp4",), static_dir=self.static)

        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
