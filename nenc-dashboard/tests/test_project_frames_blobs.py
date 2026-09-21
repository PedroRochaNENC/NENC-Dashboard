"""Conteudo dos audios chegando na Analise Geral do projeto.

O commit 03b8613 tirou prosodia_json, transcricao_csv e sincronizado_csv do
SELECT de get_audios_for_interviews — a tabela de entrevistas relia os audios a
cada render e carregar os blobs ali lia o banco quase inteiro. As paginas de
audio passaram a buscar o conteudo a parte, mas a Analise Geral ficou para tras:
seguia lendo audio.get("sincronizado_csv") de dicionarios que nao tinham mais a
chave. Sem erro nenhum, os dataframes do projeto saiam vazios e o prompt da IA
recebia "Nenhuma metrica acustica disponivel." com os dados intactos no banco.

Estes testes seguram as duas pontas do contrato: a query da tabela continua sem
blobs (a decisao de performance) e attach_audio_blobs devolve o conteudo para
quem vai de fato le-lo.
"""

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from utils import auth
from utils import prosodia_db
from utils.prosodia_loader import load_prosodia_from_uploads


SINCRONIZADO_CSV = (
    "start_s,end_s,duracao_s,speakers,timestamp_inicio,texto_transcricao,"
    "f0_media,f0_variacao,loudness_media,loudness_variacao,speaking_rate,"
    "dim_arousal,dim_valence\n"
    "0.0,1.5,1.5,Entrevistado,00:00:00.00,a academia e muito limpa,"
    "213.826,44.820,0.518,0.301,5.769,-0.215,0.181\n"
    "1.5,3.0,1.5,Entrevistado,00:00:01.50,o atendimento e otimo,"
    "198.401,39.117,0.604,0.288,4.932,0.104,0.263\n"
).encode("utf-8")

TRANSCRICAO_CSV = (
    "SpeakerName,Timestamp,Text\n"
    "Entrevistado,00:00:00.00,a academia e muito limpa\n"
    "Entrevistado,00:00:01.50,o atendimento e otimo\n"
).encode("utf-8")

PROSODIA_JSON = b'{"result": {"vad": [{"start": 0.0, "end": 1.5}]}}'

METRICAS_ACUSTICAS = [
    "f0_media",
    "f0_variacao",
    "loudness_media",
    "loudness_variacao",
    "speaking_rate",
    "dim_arousal",
    "dim_valence",
]


class _BytesFile:
    """Mesmo adaptador que as paginas usam para reaproveitar o loader."""

    def __init__(self, data: bytes, name: str):
        self._buf = io.BytesIO(data)
        self.name = name

    def read(self):
        return self._buf.read()

    def seek(self, pos: int):
        return self._buf.seek(pos)


class ProjectFramesBlobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        prosodia_db.init_db()
        # O alvo aqui e o dado lido, nao papel nem auditoria: as guardas de
        # escrita e o audit_log dependem da sessao Streamlit.
        for attribute, kwargs in (
            ("_require_write", {}),
            ("_audit", {}),
            ("_active_organization_id", {"return_value": self.organization.id}),
        ):
            guard = patch.object(prosodia_db, attribute, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        self.project_id = prosodia_db.create_project("Projeto Smartfit")
        self.audio_id = prosodia_db.create_audio(
            self.project_id,
            "wa_+5521993514655_40",
            prosodia_json=PROSODIA_JSON,
            transcricao_csv=TRANSCRICAO_CSV,
            sincronizado_csv=SINCRONIZADO_CSV,
        )

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _interviews(self):
        return prosodia_db.get_audios_for_interviews(self.project_id)

    def _attached(self):
        return prosodia_db.attach_audio_blobs(self.project_id, self._interviews())

    # -- a decisao de performance -------------------------------------

    def test_interviews_table_stays_free_of_blobs(self):
        """Se voltarem ao SELECT, a tabela relê os blobs a cada render."""
        row = self._interviews()[0]

        for column in ("prosodia_json", "transcricao_csv", "sincronizado_csv"):
            self.assertNotIn(column, row)

    # -- o contrato que quebrou ---------------------------------------

    def test_attach_restores_the_content_of_each_audio(self):
        row = self._attached()[0]

        self.assertEqual(row["prosodia_json"], PROSODIA_JSON)
        self.assertEqual(row["transcricao_csv"], TRANSCRICAO_CSV)
        self.assertEqual(row["sincronizado_csv"], SINCRONIZADO_CSV)

    def test_attach_keeps_the_metadata_and_the_order(self):
        second_id = prosodia_db.create_audio(
            self.project_id,
            "wa_+5521994637053_36",
            sincronizado_csv=SINCRONIZADO_CSV,
        )

        interviews = self._interviews()
        attached = prosodia_db.attach_audio_blobs(self.project_id, interviews)

        self.assertEqual(
            [row["id"] for row in attached], [row["id"] for row in interviews]
        )
        self.assertEqual(
            [row["session_id"] for row in attached],
            [row["session_id"] for row in interviews],
        )
        self.assertIn(second_id, [row["id"] for row in attached])

    def test_audio_still_processing_comes_back_with_empty_content(self):
        """Importacao cria o audio antes do resultado; nao pode estourar aqui."""
        pending_id = prosodia_db.create_audio(self.project_id, "wa_pendente_1")

        row = next(r for r in self._attached() if r["id"] == pending_id)

        self.assertIsNone(row["prosodia_json"])
        self.assertIsNone(row["transcricao_csv"])
        self.assertIsNone(row["sincronizado_csv"])

    def test_attach_does_not_touch_the_list_it_received(self):
        interviews = self._interviews()

        prosodia_db.attach_audio_blobs(self.project_id, interviews)

        self.assertNotIn("sincronizado_csv", interviews[0])

    # -- o efeito que o bug tinha na analise --------------------------

    def test_acoustic_metrics_survive_until_the_dataframes(self):
        """Era aqui que o projeto perdia pitch, loudness e arousal."""
        row = self._attached()[0]
        self.assertIsNotNone(
            row["sincronizado_csv"], "o audio chegou sem conteudo para ler"
        )

        parsed = load_prosodia_from_uploads(
            sincronizado_files=[
                _BytesFile(row["sincronizado_csv"], "Sincronizado-x.csv")
            ],
        )
        sinc_df = pd.read_csv(io.BytesIO(row["sincronizado_csv"]))

        self.assertFalse(parsed.get("transcricao", pd.DataFrame()).empty)
        for metric in METRICAS_ACUSTICAS:
            self.assertIn(metric, sinc_df.columns)
            self.assertTrue(
                sinc_df[metric].notna().any(), f"{metric} chegou vazia ao dataframe"
            )
        self.assertAlmostEqual(sinc_df["f0_media"].mean(), 206.1135, places=3)

    # -- isolamento entre organizacoes --------------------------------

    def test_another_organization_does_not_read_the_content(self):
        other = auth.create_organization(
            "Organization Two",
            database_path=self.database_path,
            _bootstrap=True,
        )

        with patch.object(
            prosodia_db, "_active_organization_id", return_value=other.id
        ):
            blobs = prosodia_db.get_audio_blobs_for_project(self.project_id)

        self.assertEqual(blobs, {})

    def test_all_organizations_mode_reads_the_content(self):
        with patch.object(prosodia_db, "_active_organization_id", return_value=0):
            blobs = prosodia_db.get_audio_blobs_for_project(self.project_id)

        self.assertEqual(blobs[self.audio_id]["sincronizado_csv"], SINCRONIZADO_CSV)

    def test_the_blobs_really_live_in_the_audios_table(self):
        with closing(sqlite3.connect(self.database_path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(audios)")}

        for column in ("prosodia_json", "transcricao_csv", "sincronizado_csv"):
            self.assertIn(column, columns)


if __name__ == "__main__":
    unittest.main()
