"""Prévia das importações pendentes: o que a revisão mostra antes de gravar.

A tela é Streamlit e não roda aqui; as tabelas da prévia e o modelo de
prévia (o projeto como fica depois de gravar) são funções puras.
"""

import io
import unittest

import pandas as pd

from tests.test_jornada_choice import _field_log
from tests.test_jornada_imports import _ImportBase
from tests.test_jornada_model import SAMPLES
from utils import jornada_db, jornada_imports
from utils.jornada_import_review import (
    choice_rows,
    data_rows,
    document_rows,
    image_rows,
    preview_model,
    thumbnail,
    video_rows,
)
from utils.jornada_ingest import parse_upload


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class PreviewTests(_ImportBase):
    def setUp(self):
        super().setUp()
        with self._as(self.first_admin):
            jornada_db.update_project(self.project_id, marcas="Alfa\nBeta\nGama Livre")
        self.batch_id = jornada_imports.create_batch(self.org, self.project_id)
        self._send(self.batch_id, "2.DADOS/2.3/DSP1234/DSP1234-INDIVIDUAL2.csv", "dados", SAMPLES)
        self._send(self.batch_id, "1.GESTAO/1.4/Relação Coletas.xlsx", "registro_campo", _field_log())
        jornada_imports.close_batch(self.org, self.batch_id)
        self.files = jornada_imports.batch_files(self.project_id, self.batch_id)
        self.contents = {f["id"]: jornada_imports.read_file(self.project_id, self.batch_id, f["id"])
                         for f in self.files}
        self.parsed = {f["id"]: parse_upload(f["rel_path"].rsplit("/", 1)[-1], self.contents[f["id"]])
                       for f in self.files}

    def test_the_choices_are_read_as_the_analysis_will_read_them(self):
        model = preview_model(jornada_db.get_project(self.project_id), self.files, self.contents, self.parsed)
        table = choice_rows(model["choices"])
        stimulated = table[table["tarefa"] == "Jornada Estimulada"].set_index("participante")
        self.assertEqual(stimulated.loc["Pt03", "marca"], "Gama Livre")
        self.assertEqual(stimulated.loc["Pt02", "tempo de compra"], "0:19")
        self.assertIn("Alfa", stimulated.loc["Pt03", "consideradas"])
        free = table[table["tarefa"] == "Jornada Livre"].set_index("participante")
        self.assertEqual(free.loc["Pt02", "marca"], "")  # sem escolha anotada não é "não reconhecida"
        # A prévia não grava nada.
        self.assertEqual(jornada_db.list_files(self.project_id), [])

    def test_data_rows_flag_what_the_project_already_has(self):
        sha = {f["rel_path"]: f["sha256"] for f in self.files}
        frame = data_rows(self.files, self.parsed, {sha["2.DADOS/2.3/DSP1234/DSP1234-INDIVIDUAL2.csv"]})
        rows = frame.set_index("arquivo")
        self.assertFalse(rows.loc["DSP1234-INDIVIDUAL2.csv", "incluir"])
        self.assertIn("já está no projeto", rows.loc["DSP1234-INDIVIDUAL2.csv", "avisos"])
        self.assertTrue(rows.loc["Relação Coletas.xlsx", "incluir"])
        self.assertTrue(rows.loc["Relação Coletas.xlsx", "tipo"].startswith("Registro de campo"))


class RowTests(unittest.TestCase):
    def test_videos_already_in_the_project_by_the_original_hash_are_left_out(self):
        files = [
            {"id": 1, "rel_path": "x/Pt01-JLivre-DSP1234-out.mp4", "role": "video_cena", "sha256": "c" * 64,
             "source_sha256": "o" * 64, "size_bytes": 10 * 1024 * 1024,
             "meta": {"participant": "Pt01", "task": "livre", "store": "1234", "compactado": True,
                      "original_size": 30 * 1024 * 1024}},
            {"id": 2, "rel_path": "y/Pt02-JLivre-DSP1234.mp4", "role": "video_heatmap", "sha256": "h" * 64,
             "source_sha256": None, "size_bytes": 1024 * 1024, "meta": {"participant": "Pt02", "task": "livre"}},
        ]
        frame = video_rows(files, {"o" * 64}).set_index("id")
        self.assertEqual((frame.loc[1, "incluir"], frame.loc[1, "original (MB)"]), (False, 30.0))
        self.assertEqual((frame.loc[2, "incluir"], frame.loc[2, "tipo"]), (True, "heatmap"))

    def test_a_briefing_fills_the_empty_context_only_once(self):
        files = [{"id": i, "rel_path": "d/{}.docx".format(i), "meta": {"doc_type": "briefing", "text_chars": 900}}
                 for i in (1, 2)]
        with_base = document_rows(files, context_empty=True, kb_ready=True)
        self.assertEqual(list(with_base["destino"]),
                         ["Contexto do projeto e base de conhecimento", "base de conhecimento"])
        nowhere = document_rows(files, context_empty=False, kb_ready=False)
        self.assertFalse(nowhere["incluir"].any())

    def test_images_get_a_small_thumbnail(self):
        uri = thumbnail(_png())
        self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
        self.assertLess(len(uri), 20000)
        self.assertEqual(thumbnail(b"nao e imagem"), "")
        frame = image_rows([{"id": 7, "rel_path": "f/Frente editada.jpeg", "sha256": "i" * 64,
                             "meta": {"category": "embalagem", "brand": "Alfa", "view": "frente", "edited": True}}],
                           {7: uri}, set())
        self.assertEqual(frame.loc[0, ["marca", "vista", "editada"]].tolist(), ["Alfa", "frente", "sim"])

    def test_no_choices_give_an_empty_table(self):
        self.assertTrue(choice_rows(pd.DataFrame()).empty)


if __name__ == "__main__":
    unittest.main()
