"""Leitura dos arquivos do Teste Sensorial, com um nome-canario que nunca pode sair.

As fixtures sao sinteticas: imitam o formato das saidas do pipeline (nome do
participante em `participante`, `participante_original`, `filename` e
`Participant Name`) sem dado de estudo nenhum.
"""

import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path

import openpyxl
import pandas as pd

from utils import sensorial_ingest, sensorial_store, xlsx_keys

CANARY_WORDS = ("canario", "sentinela")
CANARY = "07-Canario Sentinela"

_SESSION = ("filename,sessao_id,participante,participante_original,amostra,experimento,data,hora,computador,"
            "avisos_sessao,dispositivo,Codigo")
_SESSIONS = (
    ("EEG_Exp_07-Canario Sentinela_2026-01-01_10-00_2001A", "a1b2c3d4e5f60718", CANARY, "x07-Canario Sentinela",
     "A", "2001A", "2026-01-01", "10:00", "DESK-01", "conferir Canario no campo"),
    ("EEG_Exp_Sem-Nome_2026-01-01_11-00_2001Basal", "0f0e0d0c0b0a0908", "", "", "", "2001Basal", "2026-01-01",
     "11:00", "DESK-01", "participante vazio no BaseReport"),
)


def _indicadores_csv() -> bytes:
    lines = [_SESSION + ",Etapa,Etapa_variante,Etapa_original,Bloco,Tempo,primeira_olfacao_s,atencao,Alpha/Beta"]
    for session in _SESSIONS:
        for step, (etapa, tempo) in enumerate((("Basal", 0.0), ("Olfacao", 0.25), ("PosOlfacao", 0.5))):
            lines.append(",".join(session) + ",bluetooth_16ch,,{},A,{}A,1,{},3.5,{},{}".format(
                etapa, etapa, tempo, 0.1 * (step + 1), 1.0 + step))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _texts(table=None, meta=None) -> str:
    parts = []
    if table is not None:
        parts.extend(str(column) for column in table.columns)
        for column in table.columns:
            parts.extend(str(value) for value in table[column].dropna().unique())
    if meta is not None:
        parts.append(json.dumps(meta, ensure_ascii=False, default=str))
    return " ".join(parts).lower()


def _workbook(sheets) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class CanaryMixin:
    def assertNoCanary(self, table=None, meta=None):
        text = _texts(table, meta)
        for word in CANARY_WORDS:
            self.assertNotIn(word, text)

    def assertStoredWithoutCanary(self, table):
        with tempfile.TemporaryDirectory() as folder:
            path = sensorial_store.write_table(table, Path(folder) / "t.parquet")
            self.assertNoCanary(sensorial_store.read_table(path))


class PipelineTableTests(CanaryMixin, unittest.TestCase):
    def test_codes_replace_names_and_types_fit_the_windows(self):
        parsed = sensorial_ingest.parse_file("indicadores.csv", _indicadores_csv(),
                                             rel_path="2.2/2.EEG Bruto/run_20260915-171636/indicadores.csv")
        self.assertTrue(parsed.ok, parsed.issues)
        self.assertEqual(parsed.role, "eeg_indicadores")
        table = parsed.table
        self.assertEqual(table["participant_code"].tolist()[::3], ["P07", None])
        for column in ("filename", "participante", "participante_original", "computador", "Codigo"):
            self.assertNotIn(column, table)
        self.assertEqual(list(table.columns[:2]), ["sessao_id", "participant_code"])
        self.assertEqual(str(table["atencao"].dtype), "float32")
        self.assertEqual(str(table["Alpha/Beta"].dtype), "float32")
        self.assertEqual(str(table["Tempo"].dtype), "float64")
        self.assertEqual(str(table["Bloco"].dtype), "Int64")
        self.assertIn("[participante]", table["avisos_sessao"].iloc[0])
        self.assertEqual(parsed.meta["run_id"], "run_20260915-171636")
        summary = parsed.meta["resumo"]
        self.assertEqual((summary["sessoes"], summary["participantes"], summary["linhas_sem_codigo"]),
                         (2, ["P07"], 3))
        self.assertEqual(summary["etapas"], ["Basal", "Olfacao", "PosOlfacao"])
        self.assertNoCanary(table, parsed.meta)
        self.assertStoredWithoutCanary(table)

    def test_gzip_and_plain_text_give_the_same_table(self):
        plain = sensorial_ingest.parse_file("indicadores.csv", _indicadores_csv())
        packed = sensorial_ingest.parse_file("indicadores.csv.gz", gzip.compress(_indicadores_csv()))
        self.assertTrue(packed.ok, packed.issues)
        pd.testing.assert_frame_equal(plain.table, packed.table)

    def test_a_file_on_disk_reads_like_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "indicadores.csv"
            path.write_bytes(_indicadores_csv())
            from_disk = sensorial_ingest.parse_file("indicadores.csv", path)
        pd.testing.assert_frame_equal(from_disk.table, sensorial_ingest.parse_file("indicadores.csv",
                                                                                   _indicadores_csv()).table)

    def test_spss_style_csv_with_semicolon_decimal_comma_and_null(self):
        content = ("sessao_id;participante;Etapa;Bloco;Tempo;Fp1_Alpha;Fp1_Beta\n"
                   "a1;07-Canario Sentinela;Basal;1;0,25;1,5;#NULO!\n"
                   "a1;07-Canario Sentinela;Basal;1;0,5;2,5;0,75\n").encode("cp1252")
        parsed = sensorial_ingest.parse_file("psd_results.csv", content)
        self.assertTrue(parsed.ok, parsed.issues)
        table = parsed.table
        self.assertEqual(table["Fp1_Alpha"].tolist(), [1.5, 2.5])
        self.assertTrue(pd.isna(table["Fp1_Beta"].iloc[0]))
        self.assertEqual(table["Tempo"].tolist(), [0.25, 0.5])
        self.assertNoCanary(table)

    def test_association_trials_get_plain_column_names(self):
        content = (
            "filename,sessao_id,participante,participante_original,amostra,experimento,Participant Name,Amostra,"
            "Trial Number,Word,Yes Side,Triggered Button,R T,Trial Response\n"
            "f_07-Canario Sentinela_x,s1,07-Canario Sentinela,07-Canario Sentinela,A,2001A,07-Canario Sentinela,A,"
            "1,Fresco,Left,Button 5,0.812,Sim\n"
            "f_07-Canario Sentinela_x,s1,07-Canario Sentinela,07-Canario Sentinela,A,2001A,07-Canario Sentinela,A,"
            "2,,Right,Button 6,1.2,\n"
        ).encode("utf-8")
        parsed = sensorial_ingest.parse_file("IAT_consolidado.csv", content)
        self.assertTrue(parsed.ok, parsed.issues)
        table = parsed.table
        self.assertEqual(table["palavra"].tolist()[0], "Fresco")
        self.assertTrue(pd.isna(table["palavra"].iloc[1]))
        self.assertEqual(table["rt"].tolist(), [0.812, 1.2])
        self.assertEqual(str(table["tentativa"].dtype), "Int64")
        self.assertEqual(table["participant_code"].unique().tolist(), ["P07"])
        self.assertNoCanary(table, parsed.meta)

    def test_a_table_without_session_id_is_refused(self):
        parsed = sensorial_ingest.parse_file("indicadores.csv", b"participante,atencao\n07-X,1\n")
        self.assertFalse(parsed.ok)


class ManifestTests(CanaryMixin, unittest.TestCase):
    def test_only_allowed_keys_survive(self):
        manifest = {
            "gerado_em": "2026-09-15T17:16:36",
            "projeto": "\\\\servidor\\Cliente\\Canario",
            "codigo": {"pacote": "pipeline", "commit": "abc123", "branch": "main"},
            "ambiente": {"python": "3.11", "computador": "DESK-CANARIO", "usuario": "canario.sentinela",
                         "pacotes": {"mne": "1.8"}},
            "configuracao": {
                "EEG_CONFIG": {"sfreq": 125, "ch_names": ["Fp1", "Fp2"]},
                "AUDIO_CONFIG": {"api_key_configurada": True},
                "PROJETO": {"participantes": {"padrao": "{codigo:02d}-{nome}", "excluir": ["Canario"],
                                              "renomear": {"Canario Sentinela": "07"}},
                            "etapas": {"padronizar": {"^Basal$": "Basal"}}},
            },
            "tipo": "eeg",
            "entrada": "X:\\Cliente\\Canario",
            "entradas": [{"arquivo": "EEG_07-Canario Sentinela.csv", "sha256": "x"}],
            "saidas": ["indicadores.csv"],
            "observacoes": [{"filename": "07-Canario Sentinela.csv", "observacao": "x"}],
            "erros": [],
            "falha": "Canario quebrou",
        }
        parsed = sensorial_ingest.parse_file("manifest.json", json.dumps(manifest).encode("utf-8"),
                                             rel_path="2.2/2.EEG Bruto/run_20260915-171636/manifest.json")
        self.assertTrue(parsed.ok, parsed.issues)
        kept = parsed.meta["manifesto"]
        self.assertIsNone(parsed.table)
        self.assertEqual(kept["codigo"]["commit"], "abc123")
        self.assertEqual(kept["configuracao"]["EEG_CONFIG"]["sfreq"], 125)
        self.assertEqual(kept["configuracao"]["PROJETO"]["participantes"], {"padrao": "{codigo:02d}-{nome}"})
        self.assertNotIn("AUDIO_CONFIG", kept["configuracao"])
        self.assertEqual((kept["n_entradas"], kept["falhou"], kept["interrompido"]), (1, True, False))
        self.assertEqual(parsed.meta["run_id"], "run_20260915-171636")
        self.assertNoCanary(meta=parsed.meta)

    def test_broken_json_is_an_error_without_the_content(self):
        parsed = sensorial_ingest.parse_file("manifest.json", b"{ Canario Sentinela")
        self.assertFalse(parsed.ok)
        self.assertNoCanary(meta={"issues": parsed.issues})


class InventoryTests(CanaryMixin, unittest.TestCase):
    def test_sessions_keep_codes_and_status_and_lose_names(self):
        content = _workbook({
            "resumo": [("item", "valor"), ("projeto", "\\\\servidor\\Cliente\\Canario"), ("participantes", "35")],
            "sessoes": [
                ("participante", "participante_original", "slot", "data", "hora", "computador", "amostra",
                 "variante", "EEG", "periféricos", "áudio", "IAT", "BaseReport", "duracao_s", "ultima_etapa",
                 "problemas", "avisos_sessao", "sessao_id"),
                (CANARY, "x07-Canario Sentinela", "2001A", "2026-01-01", "10:00", "DESK", "A", "A",
                 "ok (238 janelas)", "fluxo corrompido", "ok", "ok (7 tentativas)", "ok", 812.5, "FIM",
                 "Canario sem pulso no PPG", None, "a1b2c3d4e5f60718"),
            ],
            "sem_participante": [
                ("slot", "data", "hora", "computador", "arquivos", "duracao_s", "ultima_etapa", "problemas",
                 "sugestao", "sessao_id"),
                ("2001Basal", "2026-01-01", "11:00", "DESK", "EEG_x.csv", 30, "Basal", "só marcadores", CANARY,
                 "0f0e0d0c0b0a0908"),
            ],
            "excluidas": [
                ("participante", "experimento", "data", "hora", "computador", "motivo", "arquivos", "sessao_id"),
                (CANARY, "2001B", "2026-01-02", "09:00", "DESK", "Canario excluído no projeto.toml", "y.csv",
                 "ffffffffffffffff"),
            ],
        })
        parsed = sensorial_ingest.parse_file("inventario_20260924-160520.xlsx", content)
        self.assertTrue(parsed.ok, parsed.issues)
        table = parsed.table
        self.assertEqual(table["origem"].tolist(), ["sessao", "sem_participante", "excluida"])
        self.assertEqual(table["participant_code"].tolist(), ["P07", None, "P07"])
        self.assertEqual(table["slot"].tolist(), ["2001A", "2001Basal", "2001B"])
        self.assertTrue({"eeg", "perifericos", "audio", "iat", "basereport"} <= set(table.columns))
        self.assertEqual(table["duracao_s"].iloc[0], 812.5)
        self.assertNotIn("projeto", parsed.meta["inventario"])
        self.assertEqual(parsed.meta["inventario"]["participantes"], "35")
        self.assertNoCanary(table, parsed.meta)
        self.assertStoredWithoutCanary(table)


class FieldLogTests(CanaryMixin, unittest.TestCase):
    def test_channels_with_problems_per_participant(self):
        content = _workbook({"Planilha1": [
            (None, None, None, None, "CANAIS QUE APRESENTARAM  PROBLEMAS"),
            ("ITEM", "ID\nPARTICIPANTE", "QUALIDADE DO SINAL", "OBSERVAÇÕES E DETALHAMENTOS", "CANAL 1 ",
             "CANAL 2 ", "CANAL 3", "CANAL 4"),
            (1, "Participante 7", None, "ok", True, False, False, True),
            (2, "Participante 12", "boa", None, False, False, False, False),
            (3, None, None, None, None, None, None, None),
        ]})
        parsed = sensorial_ingest.parse_file("registro.xlsx", content, role="campo_qualidade")
        self.assertTrue(parsed.ok, parsed.issues)
        table = parsed.table
        self.assertEqual(table["participant_code"].tolist(), ["P07", "P12"])
        self.assertEqual(table["canais_problema"].tolist(), ["1,4", ""])
        self.assertEqual(table["n_canais_problema"].tolist(), [2, 0])
        self.assertTrue(table["canal_01"].iloc[0])
        self.assertEqual(table["qualidade_sinal"].tolist(), [None, "boa"])


BASE_LIMPA_ROWS = [
    ("filename", "participante", "sessao_id", "Etapa", "Bloco", "Tempo", "FAI"),
    ("EEG_07-Canario Sentinela_x", CANARY, "a1", "Basal", 1, 0.25, 0.1),
    ("EEG_07-Canario Sentinela_x", CANARY, "a1", "Olfacao", 1, 0.5, 0.2),
    ("EEG_07-Canario Sentinela_x", CANARY, "a1", "Olfacao", 1, 0.5, 0.2),
]


class BaseLimpaTests(CanaryMixin, unittest.TestCase):
    _ROWS = BASE_LIMPA_ROWS

    def test_only_the_keys_come_out_of_csv_and_xlsx(self):
        csv = "\n".join(";".join(str(v) for v in row) for row in self._ROWS).replace("0.", "0,").encode("utf-8")
        from_csv = sensorial_ingest.parse_file("Estudo BASE.base_limpa_eeg.csv", csv)
        self.assertEqual(from_csv.role, "base_limpa_eeg")
        self.assertTrue(from_csv.ok, from_csv.issues)
        workbook = _workbook({"BASE ORIGINAL": [("x",)], "BASE LIMPA": [("titulo",)] + self._ROWS})
        from_xlsx = sensorial_ingest.parse_file("estudo BASE.xlsx", workbook, role="base_limpa_eeg")
        self.assertTrue(from_xlsx.ok, from_xlsx.issues)
        for parsed in (from_csv, from_xlsx):
            self.assertEqual(list(parsed.table.columns), ["sessao_id", "Etapa", "Bloco", "Tempo"])
            self.assertEqual(parsed.table["Tempo"].tolist(), [0.25, 0.5])  # repetida sai
            self.assertNoCanary(parsed.table, parsed.meta)

    def test_the_association_layer_keeps_trials(self):
        rows = [("participante", "sessao_id", "Trial Number", "Word"), (CANARY, "s1", 1, "Fresco"),
                (CANARY, "s1", 3, "Leve")]
        workbook = _workbook({"BASE LIMPA": rows})
        parsed = sensorial_ingest.parse_file("IAT.xlsx", workbook, role="base_limpa_associacao")
        self.assertTrue(parsed.ok, parsed.issues)
        self.assertEqual(list(parsed.table.columns), ["sessao_id", "tentativa"])
        self.assertEqual(parsed.table["tentativa"].tolist(), [1, 3])
        from_app = sensorial_ingest.parse_file("x.base_limpa_associacao.csv", b"sessao_id,tentativa\ns1,2\n")
        self.assertEqual((from_app.role, from_app.table["tentativa"].tolist()), ("base_limpa_associacao", [2]))
        self.assertNoCanary(parsed.table, parsed.meta)

    def test_the_streaming_reader_names_what_is_missing(self):
        workbook = io.BytesIO(_workbook({"BASE LIMPA": [("titulo",), ("sessao_id", "Etapa"), ("a1", "Basal")]}))
        self.assertEqual(xlsx_keys.sheet_names(workbook), ["BASE LIMPA"])
        self.assertEqual(xlsx_keys.header_row(workbook, "BASE LIMPA", "SESSAO_ID"), ["sessao_id", "Etapa"])
        with self.assertRaisesRegex(ValueError, "Bloco, Tempo"):
            xlsx_keys.read_columns(workbook, "base limpa", sensorial_ingest.BASE_LIMPA_KEYS["base_limpa_eeg"])
        with self.assertRaisesRegex(ValueError, "Aba"):
            xlsx_keys.read_columns(workbook, "OUTRA", ["sessao_id"])
        frame = xlsx_keys.read_columns(workbook, "BASE LIMPA", ["Etapa", "sessao_id"])
        self.assertEqual(frame.to_dict("records"), [{"Etapa": "Basal", "sessao_id": "a1"}])


class ProfileTests(CanaryMixin, unittest.TestCase):
    def test_only_the_code_and_the_attributes_stay(self):
        content = ("Nome;Código;Sexo;Grupo;E-mail;Telefone\n"
                   "Canario Sentinela;P7;F;Usuária;canario@exemplo.com;11 99999-0000\n"
                   "Outra Pessoa;12;M;Não usuária;;\n").encode("utf-8")
        parsed = sensorial_ingest.parse_file("perfil.csv", content, role="perfil")
        self.assertTrue(parsed.ok, parsed.issues)
        self.assertEqual(list(parsed.table.columns), ["participant_code", "sexo", "grupo"])
        self.assertEqual(parsed.table["participant_code"].tolist(), ["P07", "P12"])
        self.assertEqual(parsed.meta["atributos"], ["sexo", "grupo"])
        self.assertTrue(any(issue["level"] == "warn" for issue in parsed.issues))
        self.assertNoCanary(parsed.table, parsed.meta)

    def test_a_profile_without_a_code_column_is_refused(self):
        parsed = sensorial_ingest.parse_file("perfil.csv", b"Nome;Sexo\nX;F\n", role="perfil")
        self.assertFalse(parsed.ok)


class RoleTests(unittest.TestCase):
    def test_pipeline_outputs_are_recognized_by_name(self):
        cases = {
            "indicadores.csv": "eeg_indicadores",
            "psd_results.csv.gz": "eeg_psd",
            "psd_mean_results.csv": "eeg_psd_medio",
            "data_quality_assessment.csv": "eeg_qualidade",
            "topomap_2001A_Basal.png": "eeg_topomapa",
            "perifericos_metrics.csv": "perifericos_metricas",
            "perifericos_dqa.csv": "perifericos_qualidade",
            "IAT_consolidado.csv": "associacao_tentativas",
            "inventario_20260924-160520.xlsx": "inventario",
            "manifest.json": "manifesto",
            "observacoes.csv": None,
            "IAT_2001A.csv": None,
        }
        for filename, role in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(sensorial_ingest.detect_role(filename), role)

    def test_participant_codes(self):
        cases = {"07-Nome": "P07", "x17-Nome Sobrenome": "P17", "Participante 7": "P07", "P7": "P07",
                 12: "P12", 3.0: "P03", "Nome": None, None: None, float("nan"): None}
        for value, code in cases.items():
            with self.subTest(value=value):
                self.assertEqual(sensorial_ingest.participant_code(value), code)

    def test_run_folder(self):
        self.assertEqual(sensorial_ingest.run_id_from_path("2.2/EEG/run_20260915-171636/x.csv"),
                         "run_20260915-171636")
        self.assertIsNone(sensorial_ingest.run_id_from_path("2.2/EEG/x.csv"))

    def test_images_are_checked_and_topomaps_named(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, format="PNG")
        parsed = sensorial_ingest.parse_file("topomap_2001A_CR_Stimulus_Wait.png", buffer.getvalue())
        self.assertTrue(parsed.ok, parsed.issues)
        self.assertEqual((parsed.meta["formato"], parsed.meta["experimento"], parsed.meta["etapa_arquivo"]),
                         ("png", "2001A", "CR_Stimulus_Wait"))
        fake = sensorial_ingest.parse_file("topomap_x_y.png", b"not an image")
        self.assertFalse(fake.ok)

    def test_unknown_files_and_wrong_formats_are_refused(self):
        unknown = sensorial_ingest.parse_file("dados.bin", b"x")
        self.assertFalse(unknown.ok)
        self.assertIsNone(unknown.role)
        wrong = sensorial_ingest.parse_file("indicadores.json", b"{}", role="eeg_indicadores")
        self.assertFalse(wrong.ok)
        document = sensorial_ingest.parse_file("briefing.pdf", b"%PDF-1.4", role="documento")
        self.assertTrue(document.ok, document.issues)


if __name__ == "__main__":
    unittest.main()
