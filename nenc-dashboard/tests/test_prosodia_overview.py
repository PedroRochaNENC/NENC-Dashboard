"""Recortes da Análise Geral e da lista de Áudios: funções puras de prosodia_overview."""

import unittest
from datetime import date
from unittest import mock

import pandas as pd

from utils import prosodia_overview as overview
from utils import prosodia_summary as summary

AUDIOS = [
    {"session_id": "wa_5511999990412_4821", "qr_code_name": "Banner Homs", "received_at": "2026-09-14T21:12:00Z"},
    {"session_id": "wa_5511999997781_4820", "qr_code_name": "", "created_at": "2026-09-14 17:55:00"},
    {"session_id": "wa_upload_4815", "qr_code_name": None, "created_at": "2026-09-01 16:30:00"},
]


class AtributosTest(unittest.TestCase):
    def test_rotulo_do_qr_segue_a_tabela_de_audios(self):
        self.assertEqual([overview.qr_label(a) for a in AUDIOS], ["Banner Homs", overview.SEM_QR, overview.UPLOAD])
        self.assertEqual(overview.origin(AUDIOS[2]), "upload")

    def test_id_e_telefone(self):
        self.assertEqual(overview.api_audio_id("wa_5511999990412_4821"), 4821)
        self.assertIsNone(overview.api_audio_id("local_7"))
        self.assertEqual(overview.masked_phone("wa_5511999990412_4821"), "+55 11 ••••-0412")
        self.assertEqual(overview.masked_phone("wa_upload_4815"), "upload pela API")

    def test_hora_com_fuso_vira_hora_local(self):
        self.assertEqual(overview.entry_text(AUDIOS[0]), "14/09 18:12")

    def test_hora_sem_fuso_da_api_e_utc(self):
        """A API grava received_at em UTC sem sufixo: 21h12 UTC são 18h12 em São Paulo."""
        audio = {"session_id": "wa_5511999990412_4821", "received_at": "2026-09-14T21:12:00.123456"}
        with mock.patch.object(summary, "_TZ", "America/Sao_Paulo"):
            self.assertEqual(overview.entry_text(audio), "14/09 18:12")

    def test_sem_hora_de_chegada_usa_a_importacao_no_fuso_do_servidor(self):
        audio = {"session_id": "wa_upload_4815", "received_at": None, "created_at": "2026-09-01 16:30:00"}
        with mock.patch.object(summary, "_TZ", "America/Sao_Paulo"), \
                mock.patch.object(summary, "_SERVER_TZ", "UTC"):
            self.assertEqual(overview.entry_text(audio), "01/09 13:30")


class FiltroTest(unittest.TestCase):
    def test_filtra_por_qr_periodo_e_origem(self):
        self.assertEqual(len(overview.filter_audios(AUDIOS, qrs=["Banner Homs"])), 1)
        self.assertEqual(len(overview.filter_audios(AUDIOS, period=(date(2026, 9, 10), date(2026, 9, 30)))), 2)
        self.assertEqual(len(overview.filter_audios(AUDIOS, origins=["whatsapp"])), 2)
        self.assertEqual(overview.as_period((date(2026, 9, 1),)), (date(2026, 9, 1), date(2026, 9, 1)))

    def test_descricao_do_recorte(self):
        limites = (date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(overview.filter_description([], limites, [], limites), "")
        self.assertEqual(overview.filter_description([], limites, ["whatsapp", "upload"], limites), "")
        self.assertEqual(
            overview.filter_description(["Banner Homs"], (date(2026, 9, 10), date(2026, 9, 20)), ["upload"], limites),
            "QR code Banner Homs; de 10/09/2026 a 20/09/2026; origem Upload",
        )


class ResumoPorQrTest(unittest.TestCase):
    def test_indice_ponderado_pelos_trechos_e_divergencias(self):
        indice = pd.DataFrame({
            "grupo": ["wa_5511999990412_4821", "wa_5511999997781_4820"],
            "trechos": [3, 1], "texto": [0.5, -0.2], "voz": [0.3, -0.4],
            "indice": [0.4, -0.3], "positivo": [0.8, 0.0], "neutro": [0.2, 0.5], "negativo": [0.0, 0.5],
        })
        divergencias = pd.DataFrame({"session_id": ["wa_5511999990412_4821"] * 2})
        resumo = overview.qr_summary(AUDIOS, indice, pd.DataFrame(), divergencias).set_index("QR code")
        self.assertAlmostEqual(resumo.loc["Banner Homs", "Índice"], 0.4)
        self.assertEqual(resumo.loc["Banner Homs", "Divergências"], 2)
        self.assertTrue(pd.isna(resumo.loc[overview.UPLOAD, "Índice"]))


if __name__ == "__main__":
    unittest.main()
