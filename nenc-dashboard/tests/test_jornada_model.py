"""Modelo da Jornada: unidade por gravacao, conversao, status e agregados.

Os arquivos sao sinteticos, com as armadilhas dos dados reais: uma loja
exportada em amostras e outra em segundos, uma gravacao com todas as AOIs
zeradas (nao codificada), o mesmo dado em dois arquivos, agregados por grupo.
"""

import math
import unittest

import pandas as pd

from utils.jornada_model import build_model, detect_unit, recording_key

COLUMNS = [
    "AOI", "TotalGazeDuration", "NormalizedGazeDuration", "AverageGazeDuration",
    "MaximumGazeDuration", "MinimumGazeDuration", "GazeCount", "TimeToFirstFixation",
    "GazedAtBy", "Scenario", "Participant",
]


def _export(rows, sep=";") -> bytes:
    frame = pd.DataFrame(rows, columns=COLUMNS)
    text = frame.to_csv(sep=sep, index=False)
    return ("﻿" + text.replace(".", ",")).encode("utf-8")


def _frames(n: int, dt: float, start: float = 0.0) -> bytes:
    lines = ["frame,timestamp,x,y"] + [
        "{},{:.4f},100,100".format(i, start + i * dt) for i in range(n)
    ]
    return "\n".join(lines).encode("ascii")


def _file(file_id, filename, content, overrides=None):
    return {"id": file_id, "filename": filename, "kind": None,
            "meta": {"overrides": overrides or {}}, "content": content}


def _bundle(files, **extra):
    bundle = {
        "project": {"id": 1, "name": "Estudo", "marcas": "Marca A\nMarca B",
                    "marca_foco": "Marca A", "data_version": 3},
        "settings": {"dimensions": {"tipo": ["Diurno", "Noturno"]}},
        "files": files,
        "participants": [],
        "recordings": [],
        "aoi_overrides": [],
        "interviews": [],
    }
    bundle.update(extra)
    return bundle


# Loja 1234 em amostras: 200 quadros a 0,05 s -> 9,95 s de gravacao.
SAMPLES = _export([
    ["Marca A Noturno p1", 40, 40 / 200, 20, 30, 10, 2, 50, 1, "Jornadas Livres", "Pt01"],
    ["Marca B Diurno", 10, 10 / 200, 10, 10, 10, 1, 120, 1, "Jornadas Livres", "Pt01"],
    ["Marca A Noturno p1", 0, 0, 0, 0, 0, 0, "NaN", 0, "Jornadas Livres", "Pt02"],
    ["Marca B Diurno", 0, 0, 0, 0, 0, 0, "NaN", 0, "Jornadas Livres", "Pt02"],
])
# Loja "atacado" em segundos: gravacao de 30 s.
SECONDS = _export([
    ["Marca A Noturno p1", 3.0, 0.1, 1.5, 2.0, 1.0, 2, 4.5, 1, "Jornadas Estimuladas-Atacado", "Pt03"],
    ["Marca B Diurno", 1.2, 0.04, 1.2, 1.2, 1.2, 1, 9.0, 1, "Jornadas Estimuladas-Atacado", "Pt03"],
])


class UnitTests(unittest.TestCase):
    def test_detect_unit_compares_the_export_length_with_the_frames(self):
        rows = pd.DataFrame({"TotalGazeDuration": [40.0], "NormalizedGazeDuration": [0.2],
                             "MaximumGazeDuration": [30.0], "MinimumGazeDuration": [10.0],
                             "TimeToFirstFixation": [50.0]})
        self.assertEqual(detect_unit(rows, {"n_frames": 200, "duration_s": 9.95})[0], "amostras")
        self.assertEqual(detect_unit(rows, {"n_frames": 900, "duration_s": 200.0})[0], "segundos")
        # Sem quadros: inteiros com comprimento >= 50 indicam amostras.
        self.assertEqual(detect_unit(rows)[0], "amostras")
        seconds = rows.assign(TotalGazeDuration=[3.2], NormalizedGazeDuration=[0.1])
        self.assertEqual(detect_unit(seconds)[0], "segundos")


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.files = [
            _file(1, "DSP1234-INDIVIDUAL2.csv", SAMPLES),
            _file(2, "ATACADO-INDIVIDUAL2.csv", SECONDS),
            _file(3, "Pt01-JLivre-DSP1234.csv", _frames(200, 0.05)),
            _file(4, "Pt03-JEstimulada-ATACADO.csv", _frames(601, 0.05)),
            _file(5, "Pt02-JLivre-DSP1234.csv", _frames(100, 0.05)),
        ]

    def _recording(self, model, key):
        return model["recordings"].set_index("recording_key").loc[key]

    def test_samples_become_seconds_and_ttff_uses_the_frame_timestamp(self):
        model = build_model(_bundle(self.files))
        record = self._recording(model, "Pt01|livre|1234")
        self.assertEqual(record["unit"], "amostras")
        self.assertEqual(record["status"], "incluida")
        gaze = model["gaze"].set_index(["participant", "aoi"])
        row = gaze.loc[("Pt01", "Marca A Noturno p1")]
        self.assertAlmostEqual(row["dwell_s"], 40 * 0.05, places=6)
        self.assertAlmostEqual(row["ttff_s"], 50 * 0.05, places=6)
        self.assertEqual(row["brand"], "Marca A")
        self.assertTrue(row["is_focus"])
        self.assertEqual(row["attr_tipo"], "Noturno")

    def test_seconds_pass_through(self):
        model = build_model(_bundle(self.files))
        record = self._recording(model, "Pt03|estimulada|atacado")
        self.assertEqual(record["unit"], "segundos")
        row = model["gaze"].set_index(["participant", "aoi"]).loc[("Pt03", "Marca B Diurno")]
        self.assertEqual((row["dwell_s"], row["ttff_s"]), (1.2, 9.0))

    def test_all_zero_rows_are_not_coded_until_someone_decides(self):
        model = build_model(_bundle(self.files))
        self.assertEqual(self._recording(model, "Pt02|livre|1234")["status"], "nao_codificada")
        self.assertTrue(any(i["code"] == "nao_codificada" for i in model["issues"]))

        included = build_model(_bundle(self.files, recordings=[
            {"participant_code": "Pt02", "task": "livre", "store": "1234", "status": "incluida",
             "reason": None, "unit_override": None},
        ]))
        self.assertEqual(self._recording(included, "Pt02|livre|1234")["status"], "incluida")

        excluded = build_model(_bundle(self.files, recordings=[
            {"participant_code": "Pt01", "task": "livre", "store": "1234", "status": "excluida",
             "reason": "calibração ruim", "unit_override": None},
        ]))
        record = self._recording(excluded, "Pt01|livre|1234")
        self.assertEqual((record["status"], record["status_reason"]), ("excluida", "calibração ruim"))

    def test_without_frames_the_nominal_rate_converts_or_seconds_stay_empty(self):
        files = [self.files[0]]
        bare = build_model(_bundle(files))
        row = bare["gaze"].set_index(["participant", "aoi"]).loc[("Pt01", "Marca A Noturno p1")]
        self.assertTrue(math.isnan(row["dwell_s"]))
        self.assertTrue(any(i["code"] == "sem_conversao" for i in bare["issues"]))

        nominal = build_model(_bundle(files, settings={"hz_nominal": 20}))
        row = nominal["gaze"].set_index(["participant", "aoi"]).loc[("Pt01", "Marca A Noturno p1")]
        self.assertAlmostEqual(row["dwell_s"], 2.0)

    def test_unit_override_wins(self):
        model = build_model(_bundle(self.files, recordings=[
            {"participant_code": "Pt03", "task": "estimulada", "store": "atacado",
             "status": "auto", "reason": None, "unit_override": "amostras"},
        ]))
        self.assertEqual(self._recording(model, "Pt03|estimulada|atacado")["unit"], "amostras")

    def test_the_latest_file_wins_on_repeated_rows(self):
        newer = SECONDS.replace(b"3,0;", b"6,0;", 1)
        model = build_model(_bundle(self.files + [_file(9, "ATACADO-v2.csv", newer)]))
        row = model["gaze"].set_index(["participant", "aoi"]).loc[("Pt03", "Marca A Noturno p1")]
        self.assertEqual(row["dwell_s"], 6.0)
        self.assertTrue(any(i["code"] == "linhas_repetidas" for i in model["issues"]))

    def test_coverage_and_recordings_without_aois(self):
        files = self.files + [_file(6, "Pt03-Emb-ATACADO.csv", _frames(50, 0.05))]
        model = build_model(_bundle(files))
        self.assertEqual(self._recording(model, "Pt03|embalagens|atacado")["status"], "sem_aoi")
        coverage = model["coverage"].set_index(["participant", "task"])["status"]
        self.assertEqual(coverage[("Pt01", "estimulada")], "ausente")
        self.assertEqual(coverage[("Pt02", "livre")], "nao_codificada")


class PooledTests(unittest.TestCase):
    def _pooled(self, group, rows, filename=None):
        content = _export([row + ["Embalagens", "Participants"] for row in rows])
        return filename or "Embalagens_Gaze Statistics_{}.csv".format(group), content

    def test_group_size_comes_from_config_members_or_lookers(self):
        name, content = self._pooled("PERFIL 1", [
            ["", 100.5, 0.9, 1, 1, 1, 10, 0, 3],
            ["Marca A_FIG", 5.25, 0.05, 1, 2, 0.5, 6, 12.5, 3],
        ])
        frames = [_file(i + 10, "Pt0{}-Emb-DSP1234.csv".format(i), _frames(20, 0.05)) for i in (1, 2)]
        participants = [{"code": "Pt01", "profile": "Farma"}, {"code": "Pt02", "profile": "Farma"}]

        by_lookers = build_model(_bundle([_file(1, name, content)]))
        by_members = build_model(_bundle(
            [_file(1, name, content)] + frames, participants=participants,
            settings={"groups": {"PERFIL 1": {"profile": "Farma"}}},
        ))
        configured = build_model(_bundle(
            [_file(1, name, content)], settings={"groups": {"PERFIL 1": {"profile": "Farma", "size": 4}}},
        ))

        first = lambda model: model["pooled"].iloc[0]
        self.assertEqual((first(by_lookers)["n_group"], first(by_lookers)["n_group_source"]), (3, "maior GazedAtBy"))
        self.assertEqual(first(by_members)["n_group"], 2)
        self.assertEqual(first(configured)["n_group"], 4)
        self.assertEqual(first(configured)["profile"], "Farma")
        element = by_lookers["pooled"].set_index("aoi").loc["Marca A_FIG"]
        self.assertEqual((element["kind"], element["element"], element["unit"]), ("embalagem", "FIG", "segundos"))
        self.assertTrue(by_lookers["pooled"].set_index("aoi").loc[""]["is_outside"])
        # Mais olhadores que o grupo configurado e erro de configuracao.
        self.assertTrue(any(i["code"] == "grupo_menor_que_gazedatby" for i in by_members["issues"]))

    def test_pooled_samples_with_a_mean_ttff_are_still_samples(self):
        name, content = self._pooled("PERFIL 3", [
            ["Marca A Noturno p1", 256, 256 / 11499, 32, 60, 4, 8, 2724.5, 2],
        ], filename="DSP1234_Gaze Statistics_PERFIL 3.csv")
        model = build_model(_bundle([_file(1, name, content.replace(b"Embalagens", b"Jornadas Livres"))]))
        self.assertEqual(model["pooled"].iloc[0]["unit"], "amostras")

    def test_pooled_is_only_used_where_there_is_no_individual_data(self):
        individual = _file(1, "DSP1234-INDIVIDUAL2.csv", SAMPLES)
        wrong_total = _export([
            ["Marca A Noturno p1", 99, 0.1, 1, 1, 1, 2, 50, 1, "Jornadas Livres", "Participants"],
        ])
        model = build_model(_bundle([individual, _file(2, "DSP1234_Gaze Statistics_TODOS.csv", wrong_total)]))
        self.assertFalse(model["pooled"]["used"].any())
        self.assertTrue(any(i["code"] == "agregado_nao_confere" for i in model["issues"]))


if __name__ == "__main__":
    unittest.main()
