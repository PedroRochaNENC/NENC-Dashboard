"""Leitura dos arquivos da Jornada de Compra, com arquivos sinteticos.

Os formatos reproduzem os exports reais (Blickshift e Kexxu) nos detalhes que
quebravam o leitor antigo: separador `;`, decimal com virgula, BOM, os
literais NaN/Infinity, porcentagem e espacos de uma planilha editada a mao,
TAB num arquivo re-salvo, espaco duro nos rotulos. As marcas sao genericas.
"""

import io
import math
import unittest

import pandas as pd

from utils.jornada_ingest import (
    detect_kind,
    frames_summary,
    frames_timestamps,
    legacy_tabelas_csv,
    normalize_participant,
    parse_duration_text,
    parse_recording_filename,
    parse_upload,
    scenario_parts,
    store_key,
    task_key,
    to_number,
)

HEADER = (
    "AOI;TotalGazeDuration;NormalizedGazeDuration;AverageGazeDuration;MaximumGazeDuration;"
    "MinimumGazeDuration;GazeCount;TimeToFirstFixation;GazedAtBy;AOITransitionRate;"
    "FixationCount;SaccadeCount;GazePointValidity;AverageFixationDuration;"
    "AverageSaccadeDuration;AverageSaccadeLength;FixationRate;FixationSaccadeTimeRatio;"
    "ScanPathLength;ScanPathDuration;ScanPathArea;MeanPupilDilation;Scenario;Participant"
)


def _row(aoi, total, normalized, gazes, ttff, scenario, participant, gazed_by=1):
    return ";".join(
        [aoi, total, normalized, "1", "1", "1", gazes, ttff, str(gazed_by), "0,01", "5", "5",
         "1", "0,05", "0", "20", "1", "Infinity", "100", "10", "Infinity", "0", scenario,
         participant]
    )


def _individual_csv() -> bytes:
    lines = [
        HEADER,
        _row("Marca A Noturno p1", "106", "0,0119", "5", "1729", "Jornadas Livres", "Pt01"),
        _row("Marca B Diurno", "0", "0", "0", "NaN", "Jornadas Livres", "Pt01", gazed_by=0),
        _row("Marca A Noturno p1", "40", "0,0082", "2", "900", "Jornadas Livres", "PT2"),
    ]
    return ("﻿" + "\r\n".join(lines)).encode("utf-8")


def _pooled_csv() -> bytes:
    lines = [
        HEADER,
        _row("", "10844", "0,943", "21", "0", "Jornadas Livres", "Participants", gazed_by=2),
        _row("Marca A Noturno p1", "183", "0,7%", "13", "2141,25", "Jornadas Livres",
             "Participants", gazed_by=4).replace(";1;1;1;", "; 14,08 ;1;1;", 1),
    ]
    return ("﻿" + "\r\n".join(lines)).encode("utf-8")


def _enriched_xlsx() -> bytes:
    frame = pd.DataFrame(
        {
            "LOJA": ["LOJA-X DG1234", "ATACADO"],
            "CANAL": ["FARMA", "C&C"],
            "AOI": ["Marca A Noturno p1", "Marca B Diurno pt 1"],
            "TotalGazeDuration": ["106", "1.159"],
            "NormalizedGazeDuration": ["0.0119", "0.0275"],
            "GazeCount": ["5", "1"],
            "TimeToFirstFixation": ["1729", "11.03"],
            "Scenario": ["Jornadas Livres", "Jornadas Estimuladas-Atacado"],
            "Participant": ["Pt01", "Pt04"],
            "Perfil ": ["Shopper Farma ", "Shopper Atacado"],
            "Tempo": ["17s", "1m21s"],
        }
    )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


class ValueTests(unittest.TestCase):
    def test_numbers_from_exports_and_hand_edited_cells(self):
        self.assertEqual(to_number("0,0119"), 0.0119)
        self.assertAlmostEqual(to_number("0,7%"), 0.007)
        self.assertEqual(to_number(" 14,08 "), 14.08)
        self.assertEqual(to_number("1.234,5"), 1234.5)
        self.assertEqual(to_number(" 12"), 12.0)
        self.assertEqual(to_number(3), 3.0)
        for empty in ("NaN", "Infinity", "", None, float("inf"), "texto"):
            with self.subTest(value=empty):
                self.assertTrue(math.isnan(to_number(empty)))

    def test_keys(self):
        self.assertEqual(normalize_participant("PT4"), "Pt04")
        self.assertEqual(normalize_participant("P 12"), "Pt12")
        self.assertEqual(normalize_participant("Participants"), "Participants")
        self.assertEqual(task_key("JLivre"), "livre")
        self.assertEqual(task_key("JEstimulada"), "estimulada")
        self.assertEqual(task_key("Emb"), "embalagens")
        self.assertIsNone(task_key("Gondola"))
        for token in ("DSP1234", "DGSP1234", "DG1234", "DSP-1234"):
            self.assertEqual(store_key(token), "1234")
        self.assertEqual(store_key("Atacadão"), "atacadao")
        self.assertEqual(
            scenario_parts("Jornadas Estimuladas-Atacado"),
            {"task": "estimulada", "store": "atacado", "store_label": "Atacado"},
        )

    def test_recording_names(self):
        self.assertEqual(
            parse_recording_filename("Pt04-JEstimulada-ATACADO.csv"),
            {"participant": "Pt04", "task": "estimulada", "task_token": "JEstimulada",
             "store": "atacado", "store_label": "ATACADO"},
        )
        video = parse_recording_filename("Pt01-Emb-DSP1234-out.mp4")
        self.assertEqual((video["participant"], video["task"], video["store"]),
                         ("Pt01", "embalagens", "1234"))
        self.assertIsNone(parse_recording_filename("resumo.csv"))

    def test_durations(self):
        self.assertEqual(parse_duration_text("1m21s"), 81.0)
        self.assertEqual(parse_duration_text("17s"), 17.0)
        self.assertEqual(parse_duration_text("1:21"), 81.0)
        self.assertEqual(parse_duration_text("45"), 45.0)
        self.assertTrue(math.isnan(parse_duration_text("rapido")))


class DetectionTests(unittest.TestCase):
    def test_each_format_is_recognised(self):
        frames = b"frame,timestamp,x,y\n0,0.000,1,2\n1,0.043,1,2\n"
        self.assertEqual(detect_kind("Pt01-JLivre-DSP1234.csv", frames), "gaze_frames")
        self.assertEqual(detect_kind("x.csv", _individual_csv()), "bs_individual_or_pooled")
        self.assertEqual(detect_kind("x.xlsx", _enriched_xlsx()), "bs_enriched_xlsx")
        self.assertEqual(detect_kind("gondola.JPG", b"\xff\xd8"), "image")
        self.assertEqual(detect_kind("v.mp4", b"x"), "video")
        interviews = "arquivo;ep;identificacao;texto\na.txt;1;Pt01;Vi a marca\n".encode()
        self.assertEqual(detect_kind("entrevistas.csv", interviews), "interviews")
        self.assertIsNone(detect_kind("notas.csv", b"coluna\nvalor\n"))


class LegacyTests(unittest.TestCase):
    def test_tables_derived_from_the_old_format_are_refused_with_a_way_out(self):
        content = "Marca;Soma de TotalGazeDuration\nA;1,5\n".encode("utf-8")
        for name in ("Banco_PorMarca.csv", "Banco_Médias.csv", "Banco_TBVisualShare.csv", "Banco_ANOVA.csv"):
            parsed = parse_upload(name, content)
            self.assertFalse(parsed.ok, name)
            self.assertIn("Banco_Tabelas", parsed.issues[0]["message"])
        consolidated = parse_upload("Banco_Consolidado.xlsx", b"PK")
        self.assertIn("repete o Banco_Tabelas", consolidated.issues[0]["message"])

    def test_the_old_saved_table_goes_back_through_the_upload(self):
        # Como a versao antiga gravava: participante renomeado e decimais ja convertidos.
        saved = pd.DataFrame({
            "Participante": ["01-P1.csv", "01-P1.csv"],
            "AOI": ["Marca A p1", "Marca B"],
            "TotalGazeDuration": [2.5, 1.0],
            "NormalizedGazeDuration": [0.1, 0.04],
            "GazeCount": [2, 1],
            "TimeToFirstFixation": [3.0, 5.0],
        })
        parsed = parse_upload("Banco_Tabelas.csv", legacy_tabelas_csv(saved),
                              {"task": "livre", "store": "1234"})
        self.assertEqual(parsed.kind, "legacy_tabelas")
        self.assertEqual(set(parsed.table["participant"]), {normalize_participant("01-P1")})
        self.assertEqual(parsed.table["TotalGazeDuration"].tolist(), [2.5, 1.0])


class ParseTests(unittest.TestCase):
    def test_individual_export_takes_the_store_from_the_file_name(self):
        parsed = parse_upload("DGSP1234-INDIVIDUAL2.csv", _individual_csv())
        self.assertEqual(parsed.kind, "bs_individual")
        table = parsed.table
        self.assertEqual(sorted(table["participant"].unique()), ["Pt01", "Pt02"])
        self.assertEqual(set(table["task"]), {"livre"})
        self.assertEqual(set(table["store"]), {"1234"})
        self.assertEqual(table.loc[0, "NormalizedGazeDuration"], 0.0119)
        self.assertTrue(math.isnan(table.loc[1, "TimeToFirstFixation"]))
        self.assertTrue(math.isnan(table.loc[0, "ScanPathArea"]))

    def test_pooled_export_keeps_the_group_and_the_outside_row(self):
        parsed = parse_upload("DGSP1234_Gaze Statistics_PERFIL 1.csv", _pooled_csv())
        self.assertEqual(parsed.kind, "bs_pooled")
        self.assertEqual(parsed.meta["group"], "PERFIL 1")
        self.assertTrue(parsed.meta["outside_row"])
        row = parsed.table[parsed.table["aoi"] == "Marca A Noturno p1"].iloc[0]
        self.assertAlmostEqual(row["NormalizedGazeDuration"], 0.007)
        self.assertEqual(row["AverageGazeDuration"], 14.08)
        self.assertEqual(row["TimeToFirstFixation"], 2141.25)

    def test_pooled_tab_file_without_scenario_asks_for_the_task(self):
        frame = pd.DataFrame({"AOI": ["Marca A_FIG"], "TotalGazeDuration": ["1,5"], "GazedAtBy": ["3"]})
        content = frame.to_csv(sep="\t", index=False).encode("ascii")
        parsed = parse_upload("Loja_Gaze Statistics_TODOS.csv", content)
        self.assertEqual(parsed.kind, "bs_pooled")
        self.assertIn("task", parsed.meta["needs"])
        fixed = parse_upload(
            "Loja_Gaze Statistics_TODOS.csv", content, {"task": "embalagens", "store": ""}
        )
        self.assertEqual(fixed.meta["task"], "embalagens")
        self.assertNotIn("needs", fixed.meta)

    def test_enriched_sheet_seeds_participants_and_stores(self):
        parsed = parse_upload("todos.xlsx", _enriched_xlsx())
        self.assertEqual(parsed.kind, "bs_enriched_xlsx")
        info = {item["code"]: item for item in parsed.meta["participant_info"]}
        self.assertEqual(info["Pt01"]["profile"], "Shopper Farma")
        self.assertEqual(info["Pt04"]["tempo_informado"], "1m21s")
        self.assertEqual(parsed.meta["store_info"]["1234"]["channel"], "FARMA")
        self.assertEqual(set(parsed.table["store"]), {"1234", "atacado"})
        self.assertEqual(set(parsed.table["task"]), {"livre", "estimulada"})

    def test_frames_file_and_its_summary(self):
        content = b"frame,timestamp,x,y\n0,0.0,1,1\n1,0.05,1,1\n2,0.10,1,1\n3,0.60,1,1\n"
        parsed = parse_upload("Pt07-JLivre-DSP1234.csv", content)
        self.assertEqual(parsed.kind, "gaze_frames")
        self.assertEqual(
            (parsed.meta["participant"], parsed.meta["task"], parsed.meta["store"]),
            ("Pt07", "livre", "1234"),
        )
        summary = parsed.meta["frames"]
        self.assertEqual(summary["n_frames"], 4)
        self.assertAlmostEqual(summary["duration_s"], 0.6)
        self.assertAlmostEqual(summary["hz"], 20.0)
        self.assertAlmostEqual(summary["max_gap_s"], 0.5)
        # 0,5 s de buraco menos um intervalo normal (0,05 s) sobre 0,6 s.
        self.assertAlmostEqual(summary["loss_pct"], 75.0)
        stamps = frames_timestamps(content)
        self.assertAlmostEqual(stamps[3], 0.60)

    def test_frames_summary_needs_two_rows(self):
        summary = frames_summary(pd.DataFrame({"timestamp": ["0"]}))
        self.assertTrue(math.isnan(summary["hz"]))
        self.assertEqual(frames_timestamps(b"frame,timestamp\n").size, 0)

    def test_what_cannot_be_read_is_reported(self):
        self.assertIn("vídeos", parse_upload("v.mp4", b"x").issues[0]["message"].lower())
        self.assertFalse(parse_upload("~$planilha.xlsx", b"x").ok)
        self.assertFalse(parse_upload("vazio.csv", b"").ok)
        broken = parse_upload("quebrado.xlsx", b"nao e excel")
        self.assertFalse(broken.ok)


if __name__ == "__main__":
    unittest.main()
