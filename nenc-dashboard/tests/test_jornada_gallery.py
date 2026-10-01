"""Imagens do projeto: qual foto mostrar em cada seção e no relatório."""

import io
import unittest

import pandas as pd

from tests.test_jornada_import_review import _png
from utils.jornada_charts import choice_heatmap
from utils.jornada_gallery import brand_photos, downscale, report_images, store_heatmaps, store_photos

IMAGES = [
    {"file_id": 1, "filename": "Gondola1.jpeg", "category": "gôndola", "store": "2250"},
    {"file_id": 2, "filename": "Panoramica.jpeg", "category": "gôndola", "store": "2250"},
    {"file_id": 3, "filename": "Gondola2250-editada.jpeg", "category": "gôndola", "store": "2250", "edited": True},
    {"file_id": 4, "filename": "heatmap-2250.png", "category": "heatmap", "store": "2250"},
    {"file_id": 5, "filename": "Assai.jpeg", "category": "gondola", "store": "assai"},
    {"file_id": 6, "filename": "Lateral.jpeg", "category": "embalagem", "brand": "Alfa", "view": "lateral"},
    {"file_id": 7, "filename": "Frente.jpeg", "category": "embalagem", "brand": "Alfa", "view": "frente"},
    {"file_id": 8, "filename": "alfa-editada.jpeg", "category": "embalagem", "brand": "alfa", "edited": True},
    {"file_id": 9, "filename": "Beta.jpeg", "category": "embalagem", "brand": "Beta", "view": ""},
]


class GalleryTests(unittest.TestCase):
    def test_the_edited_photo_comes_first_then_the_panoramic(self):
        self.assertEqual([i["file_id"] for i in store_photos(IMAGES, "2250")], [3, 2, 1])
        self.assertEqual([i["file_id"] for i in store_photos(IMAGES, "assai")], [5])
        self.assertEqual([i["file_id"] for i in store_heatmaps(IMAGES, "2250")], [4])

    def test_brand_photos_follow_edited_then_front_back_and_sides(self):
        self.assertEqual([i["file_id"] for i in brand_photos(IMAGES, "Alfa")], [8, 7, 6])

    def test_the_report_takes_the_heatmap_of_a_store_and_one_photo_per_brand(self):
        picked = report_images(IMAGES, [("2250", "DSP 2250"), ("assai", "Assaí"), ("6901", "DSP 6901")],
                               ["Alfa", "Beta", "Gama"])
        self.assertEqual([(i["file_id"], i["title"], i["group"]) for i in picked], [
            (4, "Heatmap · DSP 2250", "loja"), (5, "Gôndola · Assaí", "loja"),
            (8, "Embalagem · Alfa", "marca"), (9, "Embalagem · Beta", "marca"),
        ])

    def test_downscale_keeps_the_proportion_and_refuses_what_is_not_an_image(self):
        from PIL import Image

        small = downscale(_png(), max_px=320)
        with Image.open(io.BytesIO(small)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (320, 240)))
        self.assertEqual(downscale(b"nada"), b"")


class ChoiceChartTests(unittest.TestCase):
    def test_the_heatmap_shows_counts_over_n(self):
        rows = pd.DataFrame([
            {"group": "Assaí", "brand": "Alfa", "n": 6, "chose_n": 4, "share": 4 / 6},
            {"group": "Assaí", "brand": "Beta", "n": 6, "chose_n": 0, "share": 0.0},
            {"group": "DSP 2250", "brand": "Alfa", "n": 3, "chose_n": 1, "share": 1 / 3},
            {"group": "DSP 2250", "brand": "Beta", "n": 3, "chose_n": 2, "share": 2 / 3},
        ])
        figure = choice_heatmap(rows, ["Beta", "Alfa"])
        trace = figure.data[0]
        self.assertEqual(list(trace.x), ["Beta", "Alfa"])
        self.assertEqual([list(row) for row in trace.text], [["0/6", "4/6"], ["2/3", "1/3"]])
        self.assertEqual(choice_heatmap(pd.DataFrame()).data, ())


if __name__ == "__main__":
    unittest.main()
