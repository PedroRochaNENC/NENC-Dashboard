"""Pasta do Teste Sensorial e o envio pelo nenc_enviar, ate a importacao gravada.

A pasta sintetica segue a estrutura NENC com experimentos genericos; o
nome-canario esta nos dados e nas pastas de dado pessoal, e nao pode chegar ao
servidor.
"""

import gzip
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import main
from scripts import nenc_enviar
from tests.test_briefing import _docx_bytes
from tests.test_jornada_enviar import _Interrupted
from tests.test_sensorial_imports import _ImportBase
from tests.test_sensorial_ingest import BASE_LIMPA_ROWS, CANARY_WORDS, _indicadores_csv, _texts, _workbook
from utils import sensorial_db, sensorial_folder, sensorial_imports

TOKEN = "e" * 40
P22 = "2.DADOS/2.2.Dados Processados/"
EEG = P22 + "2.EEG Bruto/"
OLD_RUN, NEW_RUN, FAILED_RUN = (EEG + "run_2026010{}-100000/".format(day) for day in (1, 2, 3))
P23 = "2.DADOS/2.3.Dados Consolidados (finais para análise)/"
BASE = P23 + "EEG/Estudo BASE E RESULTADOS.xlsx"


def _png(side: int = 4) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (side, side)).save(buffer, format="PNG")
    return buffer.getvalue()


def _manifest(**extra) -> bytes:
    return json.dumps(dict({"tipo": "eeg", "entradas": [{"arquivo": "EEG_07-Canario Sentinela.csv"}]},
                           **extra)).encode("utf-8")


BRIEFING = _docx_bytes(*["Objetivo {}: medir a resposta a fragrâncias diferentes em comparação com um controle, "
                         "olhando a atividade cerebral, o corpo e o teste de associação.".format(n)
                         for n in range(6)])
FIELD_LOG = _workbook({"Planilha1": [
    (None, None, None, None, "CANAIS QUE APRESENTARAM PROBLEMAS"),
    ("ITEM", "ID\nPARTICIPANTE", "QUALIDADE DO SINAL", "OBSERVAÇÕES", "CANAL 1", "CANAL 2"),
    (1, "Participante 7", None, None, True, False),
]})
INVENTORY = _workbook({"sessoes": [
    ("participante", "slot", "EEG", "sessao_id"), ("07-Canario Sentinela", "2001A", "ok", "a1b2c3d4e5f60718")]})
BASE_LIMPA = _workbook({"BASE ORIGINAL": [("x",)], "BASE LIMPA": BASE_LIMPA_ROWS})
IAT = P23 + "IAT/IAT_consolidado.xlsx"
IAT_LIMPA = _workbook({"BASE LIMPA": [("participante", "sessao_id", "Trial Number", "Word"),
                                      ("07-Canario Sentinela", "s1", 1, "Fresco")]})
# Sem pasta de camada: a camada sai das colunas da aba.
OTHER = P23 + "Consolidados/Tudo.xlsx"
OTHER_LIMPA = _workbook({"BASE LIMPA": [("sessao_id", "Etapa", "Bloco", "Tempo", "BPM"), ("s1", "Basal", 1, 0.5, 70)]})

FILES = {
    OLD_RUN + "indicadores.csv": _indicadores_csv(),
    OLD_RUN + "manifest.json": _manifest(),
    NEW_RUN + "indicadores.csv": _indicadores_csv().replace(b"3.5", b"4.5"),
    NEW_RUN + "indicadores.xlsx": b"PK copia",
    NEW_RUN + "manifest.json": _manifest(),
    NEW_RUN + "error_log.csv": b"filename,erro\n",
    NEW_RUN + "execucao.log": b"log",
    NEW_RUN + "topomaps/topomap_2001A_Basal.png": _png(),
    FAILED_RUN + "indicadores.csv": b"sessao_id\nx\n",
    FAILED_RUN + "manifest.json": _manifest(falha="caiu"),
    P22 + "4.Audios/run_20260102-120000/audio_metrics.csv": b"sessao_id\nx\n",
    P22 + "_execucoes_antigas/2.EEG Bruto/run_20251201-100000/indicadores.csv": b"sessao_id\nx\n",
    P22 + "_inventarios/inventario_20260101-000000.xlsx": INVENTORY,
    P22 + "_inventarios/inventario_20260102-000000.xlsx": INVENTORY,
    BASE: BASE_LIMPA,
    P23 + "EEG/Sintaxe.sps": b"COMPUTE x = 1.",
    P23 + "EEG/ANTIGO/Estudo antigo.xlsx": BASE_LIMPA,
    IAT: IAT_LIMPA,
    OTHER: OTHER_LIMPA,
    P23 + "EEG/Resultados.xlsx": _workbook({"RESULTADOS": [("Claim", "Score")]}),
    "2.DADOS/2.1 Dados para trabalho (Cópia)/3.EEG Bruto/EEG_07-Canario Sentinela.csv": b"nome",
    "1.GESTAO_PROJETOS/1.4.Campo/Recrutamento/Lista.xlsx": b"nomes",
    "1.GESTAO_PROJETOS/1.4.Campo/Registro de qualidade.xlsx": FIELD_LOG,
    "1.GESTAO_PROJETOS/1.4.Campo/Controle.xlsx": _workbook({"x": [("dia", "sala")]}),
    "1.GESTAO_PROJETOS/1.1 Documentação/Briefing.docx": BRIEFING,
    "4.ARQUIVOS AUXILIARES/Registros de coletas/Imagem 1.jpeg": b"\xff\xd8rosto",
    "5.RELATORIOS/5.2 Relatório/Relatorio V1.pdf": b"%PDF-1.4 v1",
    "5.RELATORIOS/5.2 Relatório/Relatorio V2.pdf": b"%PDF-1.4 v2",
    "6.ARQUIVOS_AUXILIARES/Artigos/artigo.pdf": b"%PDF-1.4 artigo",
    "6.ESTIMULOS/frasco.png": _png(8),
}
KEPT = {
    NEW_RUN + "indicadores.csv": "eeg_indicadores",
    NEW_RUN + "manifest.json": "manifesto",
    NEW_RUN + "topomaps/topomap_2001A_Basal.png": "eeg_topomapa",
    P22 + "_inventarios/inventario_20260102-000000.xlsx": "inventario",
    BASE: "base_limpa_eeg",
    IAT: "base_limpa_associacao",
    OTHER: "base_limpa_perifericos",
    "1.GESTAO_PROJETOS/1.4.Campo/Registro de qualidade.xlsx": "campo_qualidade",
    "1.GESTAO_PROJETOS/1.1 Documentação/Briefing.docx": "documento",
    "5.RELATORIOS/5.2 Relatório/Relatorio V2.pdf": "documento",
    "6.ARQUIVOS_AUXILIARES/Artigos/artigo.pdf": "literatura",
    "6.ESTIMULOS/frasco.png": "estimulo",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(root: Path, files) -> None:
    for rel_path, data in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class FolderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "Estudo"
        _write(self.root, FILES)

    def _scan(self):
        return {entry.rel_path: entry for entry in sensorial_folder.scan_project(self.root)}

    def test_only_the_newest_valid_run_and_no_personal_data(self):
        entries = self._scan()
        kept = {path: entry.role for path, entry in entries.items() if entry.role != "ignorado"}
        self.assertEqual(kept, KEPT)
        reasons = {path: entry.reason for path, entry in entries.items()}
        self.assertIn("rodada anterior do pipeline (vai a run_20260102-100000)", reasons[OLD_RUN + "indicadores.csv"])
        self.assertIn("falha", reasons[FAILED_RUN + "indicadores.csv"])
        self.assertIn("cópia em xlsx", reasons[NEW_RUN + "indicadores.xlsx"])
        self.assertIn("prosódia", reasons[P22 + "4.Audios/run_20260102-120000/audio_metrics.csv"])
        self.assertIn("inventário anterior", reasons[P22 + "_inventarios/inventario_20260101-000000.xlsx"])
        self.assertIn("versão antiga", reasons[P23 + "EEG/ANTIGO/Estudo antigo.xlsx"])
        self.assertIn("recalcula", reasons[P23 + "EEG/Resultados.xlsx"])
        self.assertIn("sem o participante e os canais", reasons["1.GESTAO_PROJETOS/1.4.Campo/Controle.xlsx"])
        self.assertIn("versão anterior do relatório", reasons["5.RELATORIOS/5.2 Relatório/Relatorio V1.pdf"])
        for personal in ("2.DADOS/2.1 Dados para trabalho (Cópia)/3.EEG Bruto/EEG_07-Canario Sentinela.csv",
                         "1.GESTAO_PROJETOS/1.4.Campo/Recrutamento/Lista.xlsx",
                         "4.ARQUIVOS AUXILIARES/Registros de coletas/Imagem 1.jpeg"):
            self.assertEqual(entries[personal].role, "ignorado")
        self.assertIn("dado pessoal", reasons["1.GESTAO_PROJETOS/1.4.Campo/Recrutamento/Lista.xlsx"])
        self.assertIn("dado pessoal", reasons["4.ARQUIVOS AUXILIARES/Registros de coletas/Imagem 1.jpeg"])
        self.assertEqual(entries[NEW_RUN + "indicadores.csv"].meta,
                         {"run_id": "run_20260102-100000", "modalidade": "2.EEG Bruto"})
        self.assertEqual(entries[BASE].meta, {"aba": "BASE LIMPA"})

    def test_the_config_points_folders_and_ignores_patterns(self):
        _write(self.root, {
            "Perfil/perfil.csv": b"Codigo;Sexo\n7;F\n",
            sensorial_folder.CONFIG_NAME: 'ignorar = ["6.ESTIMULOS/**"]\n[pastas]\nperfil = ["Perfil"]\n'.encode(),
        })
        entries = self._scan()
        self.assertEqual(entries["Perfil/perfil.csv"].role, "perfil")
        self.assertEqual(entries["6.ESTIMULOS/frasco.png"].role, "ignorado")
        self.assertNotIn(sensorial_folder.CONFIG_NAME, entries)
        _write(self.root, {sensorial_folder.CONFIG_NAME: b'[pastas]\nvideos = ["x"]\n'})
        with self.assertRaises(ValueError):
            self._scan()

    def test_without_manifests_the_newest_run_wins(self):
        for run in (OLD_RUN, NEW_RUN, FAILED_RUN):
            (self.root / run / "manifest.json").unlink()
        kept = [path for path, entry in self._scan().items() if entry.role == "eeg_indicadores"]
        self.assertEqual(kept, [FAILED_RUN + "indicadores.csv"])


class SendTests(_ImportBase):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, {
            "NENC_IMPORT_TOKEN_{}".format(self.org): TOKEN,
            "NENC_IMPORT_TOKEN": TOKEN,
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.root = Path(self.temporary_directory.name) / "Estudo"  # mesmo nome do projeto
        _write(self.root, FILES)
        self.cache = Path(self.temporary_directory.name) / "cache"
        gzip_from = patch.object(nenc_enviar, "GZIP_MIN_BYTES", 200)  # a tabela sintética vai comprimida
        gzip_from.start()
        self.addCleanup(gzip_from.stop)
        self.lines = []

    def _client(self, base_url, token):
        return TestClient(main.app, headers={"Authorization": "Bearer {}".format(token)})

    def _run(self, *extra, client_factory=None):
        self.lines = []
        argv = [str(self.root), "--modulo", "teste_sensorial", "-y", "--cache", str(self.cache)] + list(extra)
        return nenc_enviar.run(argv, client_factory=client_factory or self._client, out=self.lines.append)

    def _files(self):
        with closing(sqlite3.connect(self.database_path)) as database:
            database.row_factory = sqlite3.Row
            return {row["rel_path"]: dict(row) for row in database.execute("SELECT * FROM sens_import_files")}

    def test_the_simulation_shows_the_plan_without_network(self):
        def no_network(*_):
            raise AssertionError("a simulação não pode abrir conexão")

        self.assertEqual(self._run("--simular", client_factory=no_network), 0)
        text = "\n".join(self.lines)
        self.assertIn("Teste Sensorial", text)
        self.assertIn("BASE LIMPA do EEG: janelas mantidas", text)
        self.assertIn("(só as chaves)", text)
        self.assertIn("dado pessoal", text)
        self.assertIn("Simulação: nada foi enviado.", text)

    def test_the_folder_arrives_prepared_and_the_review_records_it(self):
        self.assertEqual(self._run(), 0, self.lines)
        files = self._files()
        original = FILES[NEW_RUN + "indicadores.csv"]
        packed = files[NEW_RUN + "indicadores.csv.gz"]
        self.assertEqual((packed["role"], packed["source_sha256"]), ("eeg_indicadores", _sha(original)))
        self.assertNotEqual(packed["sha256"], _sha(original))
        keys = files[BASE[:-len(".xlsx")] + ".base_limpa_eeg.csv"]
        self.assertEqual((keys["role"], keys["source_sha256"]), ("base_limpa_eeg", _sha(BASE_LIMPA)))
        self.assertEqual(len(files), len(KEPT))
        self.assertFalse(any("Canario" in path or "Recrutamento" in path for path in files))
        self.assertTrue(any("Teste Sensorial > projeto Estudo > Uploads" in line for line in self.lines))
        self.assertEqual(list((self.cache / "gzip").glob("*.gz")), [])  # o cache só servia para retomar
        with closing(sqlite3.connect(self.database_path)) as database:
            ignored = json.loads(database.execute("SELECT ignored_json FROM sens_import_batches").fetchone()[0])
        # O que fica de fora chega só como pasta e motivo: nome de arquivo pode ter nome de pessoa.
        self.assertNotIn("canario", json.dumps(ignored, ensure_ascii=False).lower())
        self.assertIn({"rel_path": "2.DADOS/2.1 Dados para trabalho (Cópia)",
                       "reason": "cópia de trabalho dos dados brutos, com o nome dos participantes", "arquivos": 1},
                      ignored)

        self.assertEqual(self._run(), 0)
        self.assertIn("Nada novo", "\n".join(self.lines))

        [batch] = sensorial_imports.list_batches(self.project_id)
        with self._as(self.first_admin):
            report = sensorial_imports.apply_batch(self.project_id, batch["id"])
        self.assertEqual(report["skipped"], [])
        self.assertEqual(report["files"], len(KEPT))
        bundle = sensorial_db.load_project_bundle(self.project_id, self.org)
        by_role = {item["role"]: item for item in bundle["files"]}
        keys_table = sensorial_db.read_file_table(by_role["base_limpa_eeg"])
        self.assertEqual(keys_table["Tempo"].tolist(), [0.25, 0.5])
        trials = sensorial_db.read_file_table(by_role["base_limpa_associacao"])
        self.assertEqual(trials.to_dict("records"), [{"sessao_id": "s1", "tentativa": 1}])
        indicators = sensorial_db.read_file_table(by_role["eeg_indicadores"])
        self.assertEqual(indicators["primeira_olfacao_s"].iloc[0], 4.5)  # a rodada nova
        text = _texts(indicators, [item["meta"] for item in bundle["files"]])
        for word in CANARY_WORDS:
            self.assertNotIn(word, text)

        # Depois de gravado, reenviar a pasta reconhece tudo pelo hash do original.
        self.assertEqual(self._run(), 0)
        self.assertEqual(sum(line.endswith("já está no projeto") for line in self.lines), len(KEPT))

    def test_an_interrupted_send_continues_the_same_compressed_file(self):
        with patch.object(nenc_enviar, "CHUNK_BYTES", 100):
            interrupted = lambda url, token: _Interrupted(self._client(url, token), after=25)  # noqa: E731
            self.assertEqual(self._run(client_factory=interrupted), 130)
            self.assertEqual(self._run(), 0, self.lines)
        self.assertTrue(any("Continuando o envio interrompido" in line for line in self.lines))
        files = self._files()
        self.assertTrue(all(item["status"] == "completo" for item in files.values()))
        packed = files[NEW_RUN + "indicadores.csv.gz"]
        stored = next(self.inbox.rglob("{}.bin".format(packed["id"])))
        self.assertEqual(gzip.decompress(stored.read_bytes()), FILES[NEW_RUN + "indicadores.csv"])


if __name__ == "__main__":
    unittest.main()
