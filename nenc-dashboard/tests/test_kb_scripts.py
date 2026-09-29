"""Scripts de manutencao da base de conhecimento, por modulo.

Os dois scripts nasceram quando so o NencBoost mandava material de projeto para
a base. Com a Jornada de Compra fazendo o mesmo, conferir o store dela contra a
tabela `projects` apagaria documentos de projetos vivos, e o backfill com
`--refazer` recarimbaria material de projeto como literatura.
"""

import sqlite3
import unittest
from collections import Counter

from scripts.backfill_kb_attributes import _classificar, _processar_store, _project_index
from scripts.cleanup_orphan_kb_files import (
    _orfaos_do_store,
    _projetos_vivos,
    _sessoes_vivas,
    _tabelas,
)


class _VectorStoreFile:
    def __init__(self, file_id, attributes=None):
        self.id = file_id
        self.attributes = attributes


class _VectorStoreFiles:
    def __init__(self, files):
        self._files = files
        self.updated = []

    def list(self, vector_store_id):
        return list(self._files)

    def update(self, file_id, vector_store_id, attributes):
        self.updated.append((file_id, attributes))


class _Files:
    def __init__(self, names):
        self._names = names

    def list(self, purpose, limit):
        return [_Named(file_id, name) for file_id, name in self._names.items()]


class _Named:
    def __init__(self, file_id, filename):
        self.id = file_id
        self.filename = filename


class _VectorStores:
    def __init__(self, files):
        self.files = _VectorStoreFiles(files)


class _Client:
    def __init__(self, files, names=None):
        self.vector_stores = _VectorStores(files)
        self.files = _Files(names or {})


def _database() -> sqlite3.Connection:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript(
        """
        CREATE TABLE projects (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT);
        CREATE TABLE audios (
            id INTEGER PRIMARY KEY, project_id INTEGER, session_id TEXT,
            openai_file_id_prosodia TEXT, openai_file_id_transcricao TEXT
        );
        CREATE TABLE jc_projects (id INTEGER PRIMARY KEY, organization_id INTEGER, name TEXT);
        INSERT INTO projects VALUES (1, 7, 'Pesquisa Voz');
        INSERT INTO audios VALUES (1, 1, 's1', NULL, NULL);
        INSERT INTO jc_projects VALUES (5, 7, 'Estudo Gondola');
        """
    )
    return database


class CleanupScriptTests(unittest.TestCase):
    def setUp(self):
        self.database = _database()
        self.addCleanup(self.database.close)
        self.tabelas = _tabelas(self.database)

    def test_each_module_is_checked_against_its_own_projects(self):
        self.assertEqual(
            _projetos_vivos(self.database, 7, "prosodia", self.tabelas), {1}
        )
        self.assertEqual(
            _projetos_vivos(self.database, 7, "jornada_compra", self.tabelas), {5}
        )

    def test_a_live_journey_project_is_not_an_orphan(self):
        client = _Client(
            [
                _VectorStoreFile("file-vivo", {"escopo": "projeto", "project_id": 5}),
                _VectorStoreFile("file-morto", {"escopo": "analise", "project_id": 1}),
            ]
        )
        projetos = _projetos_vivos(self.database, 7, "jornada_compra", self.tabelas)
        sessoes = _sessoes_vivas(self.database, 7, "jornada_compra", self.tabelas)

        orfaos = _orfaos_do_store(client, "vs_jornada", projetos, sessoes)

        # O projeto 1 existe, mas no NencBoost: na base da Jornada ele e orfao.
        self.assertEqual([file_id for file_id, _, _ in orfaos], ["file-morto"])

    def test_an_unknown_module_never_deletes_project_material(self):
        client = _Client(
            [_VectorStoreFile("file-x", {"escopo": "projeto", "project_id": 9})]
        )
        projetos = _projetos_vivos(self.database, 7, "teste_sensorial", self.tabelas)

        orfaos = _orfaos_do_store(client, "vs_ts", projetos, None)

        self.assertIsNone(projetos)
        self.assertEqual(orfaos[0][2], False)


class BackfillScriptTests(unittest.TestCase):
    def setUp(self):
        self.database = _database()
        self.addCleanup(self.database.close)

    def test_journey_names_find_their_project(self):
        indice = _project_index(self.database, 7, "jornada_compra")

        atributos = _classificar(
            "file-1", "briefing_jc_estudo_gondola_20260901_briefing.docx", indice,
            "jornada_compra",
        )

        self.assertEqual(atributos["project_id"], 5)
        self.assertEqual(atributos["escopo"], "projeto")

    def test_journey_literature_stays_reference(self):
        indice = _project_index(self.database, 7, "jornada_compra")

        atributos = _classificar("file-2", "artigo.pdf", indice, "jornada_compra")

        self.assertEqual(atributos, {"escopo": "referencia", "modulo": "jornada_compra"})

    def test_refazer_never_restamps_a_document_that_has_a_project(self):
        client = _Client(
            [
                _VectorStoreFile(
                    "file-proj",
                    {"escopo": "analise", "modulo": "jornada_compra", "project_id": 5},
                ),
                _VectorStoreFile("file-lit", {"escopo": "referencia"}),
            ],
            names={"file-proj": "qualquer.md", "file-lit": "artigo.pdf"},
        )
        row = {"organization_id": 7, "module_key": "jornada_compra", "vector_store_id": "vs"}

        contagem = _processar_store(client, self.database, row, True, True)

        atualizados = [file_id for file_id, _ in client.vector_stores.files.updated]
        self.assertEqual(atualizados, ["file-lit"])
        self.assertEqual(contagem["ja_de_projeto"], 1)
        self.assertIsInstance(contagem, Counter)


if __name__ == "__main__":
    unittest.main()
