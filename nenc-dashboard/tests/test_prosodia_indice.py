"""Índice combinado gravado por áudio: cálculo, faixas e gravação no banco."""

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils import auth, prosodia_db
from utils import prosodia_overview as overview
from utils.prosodia_indice import calcular_indices, faixa


def _sincronizado(texto: float, valencia: float) -> bytes:
    linhas = [{"segmento_idx": i, "start_s": float(i), "end_s": i + 0.9, "speakers": "S",
               "sentimento_texto": texto, "dim_valence": valencia + 0.01 * i} for i in range(10)]
    buffer = io.StringIO()
    pd.DataFrame(linhas).to_csv(buffer, index=False)
    return buffer.getvalue().encode()


class CalculoTest(unittest.TestCase):
    def test_regua_e_o_projeto_inteiro(self):
        indices = calcular_indices({"A": _sincronizado(0.8, 0.3), "B": _sincronizado(-0.8, -0.3), "C": b"lixo"})
        self.assertEqual(set(indices), {"A", "B"})
        self.assertGreater(indices["A"][0], 0.2)
        self.assertLess(indices["B"][0], -0.2)

    def test_faixas(self):
        self.assertEqual(faixa(0.5, 0.6, 0.4), "Favorável")
        self.assertEqual(faixa(-0.3, -0.4, -0.2), "Desfavorável")
        self.assertEqual(faixa(0.05, 0.7, -0.6), "Divergente")
        self.assertEqual(faixa(0.1, 0.1, 0.1), "Neutro")
        self.assertIsNone(faixa(None, None, None))


class SincronizacaoTest(unittest.TestCase):
    def test_texto_do_estado(self):
        agora = datetime(2026, 10, 9, 15, 0)
        with patch.object(overview.prosodia_summary, "_TZ", "America/Sao_Paulo"):
            self.assertEqual(overview.sync_text("2026-10-09T17:48:00", agora), "Sincronizado há 12 min")
        self.assertEqual(overview.sync_text(None, agora), "Ainda não sincronizado")


class GravacaoTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization("Org", database_path=self.database_path, _bootstrap=True)
        self.environment = patch.dict(os.environ, {"NENC_DB_PATH": str(self.database_path)})
        self.environment.start()
        prosodia_db.init_db()
        for attribute, kwargs in (("_require_write", {}), ("_audit", {}),
                                  ("_active_organization_id", {"return_value": self.organization.id})):
            guard = patch.object(prosodia_db, attribute, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        self.project_id = prosodia_db.create_project("Projeto")

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_grava_e_a_lista_de_audios_le(self):
        prosodia_db.create_audio(self.project_id, "A", sincronizado_csv=_sincronizado(0.8, 0.3))
        prosodia_db.create_audio(self.project_id, "B", sincronizado_csv=_sincronizado(-0.8, -0.3))
        prosodia_db.create_audio(self.project_id, "sem-sinc")
        from utils.prosodia_indice import atualizar_indices_do_projeto

        self.assertEqual(atualizar_indices_do_projeto(self.project_id), 2)
        por_sessao = {a["session_id"]: a for a in prosodia_db.get_audios_for_interviews(self.project_id)}
        self.assertGreater(por_sessao["A"]["indice_combinado"], 0)
        self.assertIsNotNone(por_sessao["A"]["indice_texto"])
        self.assertIsNone(por_sessao["sem-sinc"]["indice_combinado"])

    def test_sync_registra_a_hora(self):
        prosodia_db.mark_project_synced(self.project_id)
        self.assertTrue(prosodia_db.get_project(self.project_id)["last_sync_at"])

    def test_migracao_cria_as_colunas(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            colunas = {row[1] for row in conn.execute("PRAGMA table_info(audios)")}
        self.assertTrue({"indice_combinado", "indice_texto", "indice_voz"} <= colunas)


if __name__ == "__main__":
    unittest.main()
