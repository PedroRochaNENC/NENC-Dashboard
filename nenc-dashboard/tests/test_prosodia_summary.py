"""Números do Resumo do NencBoost: funções puras de prosodia_summary."""

import unittest
from datetime import datetime
from unittest import mock

import pandas as pd

from utils import prosodia_summary as summary

AGORA = datetime(2026, 10, 8, 18, 30)


def _entradas(linhas):
    frame = pd.DataFrame(linhas, columns=["qr_code_name", "entrou_em", "n_analyses", "duration_seconds"])
    frame["qr_code_name"] = frame["qr_code_name"].fillna("")
    frame["entrou_em"] = pd.to_datetime(frame["entrou_em"])
    frame["hora_real"] = True
    return frame


class RankingTest(unittest.TestCase):
    def test_ranking_separa_top_outros_e_upload_direto(self):
        linhas = [("Banner", "2026-10-08 18:00", 1, 120)] * 5 + [("Cartaz", "2026-10-08 10:00", 0, 60)] * 3
        linhas += [("Folheto", "2026-10-07 09:00", 0, 0)] + [("", "2026-10-07 09:00", 0, None)] * 2
        ranking = summary.qr_ranking(_entradas(linhas), top=2)
        self.assertEqual([l["nome"] for l in ranking["linhas"]], ["Banner", "Cartaz"])
        self.assertEqual(ranking["total"], 9)
        self.assertEqual((ranking["outros_qr"], ranking["outros_entradas"]), (1, 1))
        self.assertEqual(ranking["sem_qr"], 2)
        self.assertEqual(ranking["maximo"], 5)

    def test_kpis_conta_so_qr_nas_24h_e_ignora_duracao_zero(self):
        linhas = [("Banner", "2026-10-08 18:00", 1, 120), ("Banner", "2026-10-06 18:00", 0, 0),
                  ("", "2026-10-08 18:10", 1, 60)]
        numeros = summary.kpis(_entradas(linhas), now=AGORA)
        self.assertEqual(numeros["entradas_qr"], 2)
        self.assertEqual(numeros["entradas_24h"], 1)
        self.assertEqual(numeros["analisados"], 2)
        self.assertEqual(numeros["duracao_media"], 90)


class HoraTest(unittest.TestCase):
    def test_horas_vao_de_8_a_20_no_minimo_e_hoje_filtra(self):
        linhas = [("Banner", "2026-10-08 18:05", 0, 1), ("Banner", "2026-10-07 11:40", 0, 1)]
        todas = summary.entries_by_hour(_entradas(linhas), now=AGORA)
        self.assertEqual((todas.index.min(), todas.index.max()), (8, 20))
        self.assertEqual((todas[18], todas[11], todas[9]), (1, 1, 0))
        hoje = summary.entries_by_hour(_entradas(linhas), only_today=True, now=AGORA)
        self.assertEqual(int(hoje.sum()), 1)


class FusoTest(unittest.TestCase):
    """A API grava received_at em UTC sem sufixo; o gráfico por hora é em NENC_TZ."""

    def _entradas_do_banco(self, linhas):
        colunas = ["id", "session_id", "qr_code_name", "duration_seconds",
                   "received_at", "created_at", "n_analyses"]
        with mock.patch.object(summary, "_TZ", "America/Sao_Paulo"), \
                mock.patch.object(summary, "_SERVER_TZ", "UTC"), \
                mock.patch.object(summary.prosodia_db, "get_audio_entries",
                                  return_value=[dict(zip(colunas, linha)) for linha in linhas]):
            return summary.project_entries(1)

    def test_received_at_sem_fuso_e_utc_e_vira_hora_local(self):
        entradas = self._entradas_do_banco([
            (1, "wa_1", "Banner", 60, "2026-10-08T21:30:00", "2026-10-08 22:00:00", 0),
            (2, "wa_2", "Banner", 60, "2026-10-08T12:00:00Z", "2026-10-08 13:00:00", 1),
        ])
        self.assertEqual(list(entradas["entrou_em"]),
                         [pd.Timestamp("2026-10-08 18:30"), pd.Timestamp("2026-10-08 09:00")])
        self.assertTrue(entradas["hora_real"].all())

    def test_sem_received_at_usa_a_importacao_no_fuso_do_servidor(self):
        entradas = self._entradas_do_banco([
            (1, "manual", "", 60, None, "2026-10-08 15:00:00", 0),
        ])
        self.assertEqual(entradas["entrou_em"].iloc[0], pd.Timestamp("2026-10-08 12:00"))
        self.assertFalse(entradas["hora_real"].iloc[0])

    def test_projeto_sem_audios(self):
        entradas = self._entradas_do_banco([])
        self.assertTrue(entradas.empty)
        self.assertEqual(summary.kpis(entradas, now=AGORA)["total"], 0)
        self.assertTrue(summary.entries_by_hour(entradas, now=AGORA).empty)


class IndiceTest(unittest.TestCase):
    def _sincronizado(self):
        """Dois áudios: texto e voz positivos num, negativos no outro."""
        linhas = []
        for sessao, texto, valencia in (("A", 0.8, 0.30), ("B", -0.8, -0.30)):
            for i in range(10):
                linhas.append({
                    "session_id": sessao, "segmento_idx": i, "SpeakerName": "S1",
                    "start_s": float(i), "end_s": float(i + 1),
                    "sentimento_texto": texto, "dim_valence": valencia + 0.01 * i,
                })
        return pd.DataFrame(linhas)

    def test_usa_a_formula_do_projeto_e_divide_o_tempo(self):
        indice = summary.combined_index(self._sincronizado())
        fatias = dict(indice["fatias"])
        self.assertEqual(list(fatias), ["Positivo", "Neutro", "Negativo"])
        self.assertAlmostEqual(fatias["Positivo"], 0.5)
        self.assertAlmostEqual(fatias["Negativo"], 0.5)
        self.assertAlmostEqual(indice["media"], 0.0, places=6)
        self.assertEqual(indice["trechos"], 20)

    def test_sem_valencia_nao_ha_indice(self):
        self.assertIsNone(summary.combined_index(self._sincronizado().drop(columns="dim_valence")))
        self.assertIsNone(summary.combined_index(pd.DataFrame()))


class FormatoTest(unittest.TestCase):
    def test_tempo_relativo(self):
        self.assertEqual(summary.relative_time(datetime(2026, 10, 8, 18, 18), AGORA), "há 12 min")
        self.assertEqual(summary.relative_time(datetime(2026, 10, 7, 23, 0), AGORA), "ontem")
        self.assertEqual(summary.relative_time(datetime(2026, 10, 5, 9, 0), AGORA), "há 3 dias")
        self.assertEqual(summary.relative_time(pd.NaT, AGORA), "")

    def test_telefone_e_numeros(self):
        self.assertEqual(summary.phone_text("551151233587"), "+55 11 5123-3587")
        self.assertEqual(summary.phone_text("5511988776655"), "+55 11 98877-6655")
        self.assertEqual(summary.number_text(1148), "1.148")
        self.assertEqual(summary.signed_text(0.236), "+0,24")
        self.assertEqual(summary.duration_text(171), "2:51")


if __name__ == "__main__":
    unittest.main()
