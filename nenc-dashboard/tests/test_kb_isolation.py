import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import NotFoundError

from utils import ai_provider, auth, kb_cleanup, prosodia_db, ui
from utils.kb_attributes import (
    ESCOPO_ANALISE,
    ESCOPO_REFERENCIA,
    belongs_to_project,
    build_kb_filter,
    project_document,
    reference_document,
)


def _not_found(file_id):
    """O 404 que a OpenAI devolve quando o arquivo ja saiu da conta."""

    request = httpx.Request("DELETE", f"https://api.openai.com/v1/files/{file_id}")
    return NotFoundError(
        f"No such File object: {file_id}",
        response=httpx.Response(404, request=request),
        body=None,
    )


class _VectorStoreFile:
    def __init__(self, file_id, attributes=None):
        self.id = file_id
        self.attributes = attributes


class _VectorStoreFiles:
    def __init__(self, files, missing=()):
        self._files = files
        self._missing = set(missing)
        self.deleted = []

    def list(self, vector_store_id):
        return list(self._files)

    def delete(self, vector_store_id, file_id):
        if file_id in self._missing:
            raise _not_found(file_id)
        self.deleted.append(file_id)


class _Files:
    def __init__(self, missing=()):
        self._missing = set(missing)
        self.deleted = []

    def delete(self, file_id):
        if file_id in self._missing:
            raise _not_found(file_id)
        self.deleted.append(file_id)


class _VectorStores:
    def __init__(self, files, missing=()):
        self.files = _VectorStoreFiles(files, missing)
        self.deleted = []

    def delete(self, vector_store_id):
        self.deleted.append(vector_store_id)


class _Client:
    def __init__(self, files, missing_files=(), missing_entries=()):
        self.files = _Files(missing_files)
        self.vector_stores = _VectorStores(files, missing_entries)


class AttributeContractTests(unittest.TestCase):
    def test_the_filter_keeps_literature_and_the_project_itself(self):
        self.assertEqual(
            build_kb_filter(7),
            {
                "type": "or",
                "filters": [
                    {"type": "eq", "key": "escopo", "value": ESCOPO_REFERENCIA},
                    {"type": "eq", "key": "project_id", "value": 7},
                ],
            },
        )

    def test_a_module_also_has_to_match_for_project_material(self):
        kb_filter = build_kb_filter(7, modulo="jornada_compra")
        self.assertEqual(kb_filter["filters"][0], {"type": "eq", "key": "escopo", "value": ESCOPO_REFERENCIA})
        self.assertEqual(
            kb_filter["filters"][1],
            {
                "type": "and",
                "filters": [
                    {"type": "eq", "key": "project_id", "value": 7},
                    {"type": "eq", "key": "modulo", "value": "jornada_compra"},
                ],
            },
        )

    def test_without_a_project_there_is_nothing_to_isolate(self):
        self.assertIsNone(build_kb_filter(None))
        self.assertIsNone(build_kb_filter(0))

    def test_project_material_carries_the_project_and_the_session(self):
        attributes = project_document(
            "prosodia", 3, escopo=ESCOPO_ANALISE, session_id="sessao-1", tipo="analise_ia"
        )
        self.assertEqual(attributes["escopo"], ESCOPO_ANALISE)
        self.assertEqual(attributes["project_id"], 3)
        self.assertEqual(attributes["session_id"], "sessao-1")
        self.assertEqual(attributes["tipo"], "analise_ia")

    def test_reference_material_is_never_bound_to_a_project(self):
        attributes = reference_document("prosodia", tipo="artigo")
        self.assertEqual(attributes["escopo"], ESCOPO_REFERENCIA)
        self.assertNotIn("project_id", attributes)
        # Literatura enviada com project_id continua sendo de todos: apagar um
        # projeto nao pode levar a referencia junto.
        self.assertFalse(
            belongs_to_project({"escopo": ESCOPO_REFERENCIA, "project_id": 3}, 3)
        )

    def test_a_scope_outside_the_contract_is_refused(self):
        with self.assertRaises(ValueError):
            project_document("prosodia", 3, escopo="qualquer_coisa")

    def test_ownership_survives_the_attribute_coming_back_as_text(self):
        self.assertTrue(belongs_to_project({"escopo": "projeto", "project_id": "3"}, 3))
        self.assertFalse(belongs_to_project({"escopo": "projeto", "project_id": 4}, 3))
        self.assertFalse(belongs_to_project(None, 3))


class AnalysisFilterTests(unittest.TestCase):
    def test_the_filter_reaches_the_file_search_tool(self):
        class _Responses:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return type("Response", (), {"output": []})()

        client = type("Client", (), {"responses": _Responses()})()
        kb_filter = build_kb_filter(5)

        with patch.object(ai_provider, "get_openai_client", return_value=client):
            ai_provider.create_analysis(
                "sistema", "usuario", vector_store_id="vs_1", kb_filter=kb_filter
            )

        self.assertEqual(client.responses.kwargs["tools"][0]["filters"], kb_filter)

    def test_without_a_filter_the_tool_stays_as_it_was(self):
        class _Responses:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return type("Response", (), {"output": []})()

        client = type("Client", (), {"responses": _Responses()})()

        with patch.object(ai_provider, "get_openai_client", return_value=client):
            ai_provider.create_analysis("sistema", "usuario", vector_store_id="vs_1")

        self.assertNotIn("filters", client.responses.kwargs["tools"][0])


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.files = [
            _VectorStoreFile("file-artigo", {"escopo": "referencia"}),
            _VectorStoreFile(
                "file-transcricao",
                {"escopo": "projeto", "project_id": 3, "session_id": "s1"},
            ),
            _VectorStoreFile(
                "file-analise",
                {"escopo": "analise", "project_id": 3, "session_id": "s2"},
            ),
            _VectorStoreFile(
                "file-outro-projeto", {"escopo": "projeto", "project_id": 9}
            ),
            _VectorStoreFile("file-legado", None),
        ]
        self.client = _Client(self.files)

    def _patched(self):
        return patch.multiple(
            kb_cleanup,
            get_openai_client=lambda: self.client,
            get_prosodia_vector_store_id=lambda: "vs_1",
        )

    def test_deleting_a_project_takes_only_its_own_material(self):
        with self._patched():
            removed = kb_cleanup.remove_documents_for_project(3)

        self.assertEqual(removed, 2)
        self.assertEqual(
            sorted(self.client.files.deleted), ["file-analise", "file-transcricao"]
        )

    def test_deleting_one_interview_leaves_the_rest_of_the_project(self):
        with self._patched():
            kb_cleanup.remove_documents_for_audio(3, "s1", ("file-do-banco",))

        # O id guardado no banco entra sempre; do resto, so o que e daquela sessao.
        self.assertEqual(
            sorted(self.client.files.deleted), ["file-do-banco", "file-transcricao"]
        )

    def test_without_a_configured_store_nothing_is_touched(self):
        with patch.multiple(
            kb_cleanup,
            get_openai_client=lambda: self.client,
            get_prosodia_vector_store_id=lambda: None,
        ):
            self.assertEqual(kb_cleanup.remove_documents_for_project(3), 0)
        self.assertEqual(self.client.files.deleted, [])

    def test_without_an_api_key_nothing_is_touched(self):
        with patch.object(kb_cleanup, "get_openai_client", lambda: None):
            self.assertEqual(kb_cleanup.remove_documents_for_project(3), 0)
            self.assertEqual(kb_cleanup.remove_files(["file-x"]), 0)
            self.assertFalse(kb_cleanup.delete_vector_store("vs_1"))

    def test_a_per_project_store_is_deleted_whole(self):
        with patch.object(kb_cleanup, "get_openai_client", lambda: self.client):
            self.assertTrue(kb_cleanup.delete_vector_store("vs_projeto"))
        self.assertEqual(self.client.vector_stores.deleted, ["vs_projeto"])

    def test_the_journey_cleans_its_own_store(self):
        # A Jornada tem base propria: limpar o projeto dela na base do NencBoost
        # nao acharia nada e deixaria o material indexado.
        with patch.multiple(
            kb_cleanup,
            get_openai_client=lambda: self.client,
            get_prosodia_vector_store_id=lambda: self.fail("base errada"),
            get_vector_store_id=lambda: "vs_jornada",
        ):
            removed = kb_cleanup.remove_documents_for_project(
                3, module_key="jornada_compra"
            )
            kb_cleanup.remove_files(["file-avulso"], module_key="jornada_compra")

        self.assertEqual(removed, 2)
        self.assertIn("file-avulso", self.client.files.deleted)

    def test_prosodia_stays_the_default_module(self):
        with patch.multiple(
            kb_cleanup,
            get_openai_client=lambda: self.client,
            get_prosodia_vector_store_id=lambda: "vs_1",
            get_vector_store_id=lambda: self.fail("base errada"),
        ):
            self.assertEqual(kb_cleanup.remove_documents_for_project(3), 2)


class DocumentRemovalTests(unittest.TestCase):
    """O botao "Remover" da tela de Base de Conhecimento.

    Ele some com o documento nos tres modulos pelo mesmo caminho, e nenhum
    deles pode parar num erro por causa de um arquivo que ja nao existe.
    """

    class _Listing:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

    def _patched(self, client, listing=None):
        return patch.multiple(
            kb_cleanup,
            get_openai_client=lambda: client,
            list_vector_store_documents=listing or self._Listing(),
        )

    def test_a_file_that_openai_no_longer_has_is_removed_without_error(self):
        # O caso do "No such File object": o documento continuava listado, e o
        # 404 do arquivo aparecia na tela como se nada tivesse saido.
        client = _Client(
            [], missing_files=["file-sumido"], missing_entries=["file-sumido"]
        )
        listing = self._Listing()

        with self._patched(client, listing):
            kb_cleanup.remove_document("vs_1", "file-sumido")

        self.assertEqual(listing.cleared, 1)

    def test_the_file_still_goes_when_it_had_already_left_the_vector_store(self):
        # Sem isto o arquivo ficaria na conta, invisivel e cobrado.
        client = _Client([], missing_entries=["file-solto"])

        with self._patched(client):
            kb_cleanup.remove_document("vs_1", "file-solto")

        self.assertEqual(client.files.deleted, ["file-solto"])

    def test_a_failure_that_is_not_a_404_reaches_who_clicked(self):
        def explode(file_id):
            raise RuntimeError("erro da OpenAI")

        client = _Client([])
        client.files.delete = explode
        listing = self._Listing()

        with self._patched(client, listing):
            with self.assertRaises(RuntimeError):
                kb_cleanup.remove_document("vs_1", "file-x")

        # A remocao parou no meio: a listagem em cache nao vale mais.
        self.assertEqual(listing.cleared, 1)


class DocumentRemovalTests(unittest.TestCase):
    """O botao "Remover" da tela de Base de Conhecimento.

    Ele some com o documento nos tres modulos pelo mesmo caminho, e nenhum
    deles pode parar num erro por causa de um arquivo que ja nao existe.
    """

    class _Listing:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

    def _patched(self, client, listing=None):
        return patch.multiple(
            kb_cleanup,
            get_openai_client=lambda: client,
            list_vector_store_documents=listing or self._Listing(),
        )

    def test_a_file_that_openai_no_longer_has_is_removed_without_error(self):
        # O caso do "No such File object": o documento continuava listado, e o
        # 404 do arquivo aparecia na tela como se nada tivesse saido.
        client = _Client(
            [], missing_files=["file-sumido"], missing_entries=["file-sumido"]
        )
        listing = self._Listing()

        with self._patched(client, listing):
            kb_cleanup.remove_document("vs_1", "file-sumido")

        self.assertEqual(listing.cleared, 1)

    def test_the_file_still_goes_when_it_had_already_left_the_vector_store(self):
        # Sem isto o arquivo ficaria na conta, invisivel e cobrado.
        client = _Client([], missing_entries=["file-solto"])

        with self._patched(client):
            kb_cleanup.remove_document("vs_1", "file-solto")

        self.assertEqual(client.files.deleted, ["file-solto"])

    def test_a_failure_that_is_not_a_404_reaches_who_clicked(self):
        def explode(file_id):
            raise RuntimeError("erro da OpenAI")

        client = _Client([])
        client.files.delete = explode
        listing = self._Listing()

        with self._patched(client, listing):
            with self.assertRaises(RuntimeError):
                kb_cleanup.remove_document("vs_1", "file-x")

        # A remocao parou no meio: a listagem em cache nao vale mais.
        self.assertEqual(listing.cleared, 1)


class ReferenceRenderingTests(unittest.TestCase):
    def test_the_old_string_citations_still_render(self):
        # O Teste Sensorial gravou por um tempo so o nome do arquivo; o
        # historico dele passa pelo mesmo renderizador.
        with patch.object(ui, "st") as fake_st:
            ui.knowledge_base_references({"citations": ["artigo.pdf"]})

        fake_st.markdown.assert_called_once()
        self.assertIn("artigo.pdf", fake_st.markdown.call_args.args[0])

    def test_the_queries_never_open_an_expander(self):
        # As telas chamam esta funcao de dentro de um expander, e o Streamlit
        # recusa expander aninhado: isso quebraria a analise recem-gerada.
        with patch.object(ui, "st") as fake_st:
            ui.knowledge_base_references(
                {
                    "citations": [],
                    "search": {
                        "available": True,
                        "searched": True,
                        "queries": ["pitch", "valencia"],
                        "excerpts": 0,
                    },
                }
            )

        fake_st.expander.assert_not_called()
        self.assertIn("pitch", fake_st.caption.call_args.args[0])

    def test_a_citation_with_an_excerpt_shows_score_and_quote(self):
        with patch.object(ui, "st") as fake_st:
            ui.knowledge_base_references(
                {
                    "citations": [
                        {"filename": "artigo.pdf", "quote": "trecho", "score": 0.9}
                    ]
                }
            )

        self.assertIn("artigo.pdf", fake_st.markdown.call_args.args[0])
        fake_st.caption.assert_called_once_with("trecho")


class DatabaseHookTests(unittest.TestCase):
    """A exclusao no banco precisa acionar a limpeza na base de conhecimento."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.environment = patch.dict(
            os.environ, {"NENC_DB_PATH": str(self.database_path)}
        )
        self.environment.start()
        prosodia_db.init_db()
        for attribute in ("_require_write", "_audit"):
            guard = patch.object(prosodia_db, attribute)
            guard.start()
            self.addCleanup(guard.stop)
        organization = patch.object(
            prosodia_db, "_active_organization_id", return_value=self.organization.id
        )
        organization.start()
        self.addCleanup(organization.stop)

        self.project_id = prosodia_db.create_project("Projeto Um")
        self.audio_id = prosodia_db.create_audio(self.project_id, "sessao-1")
        prosodia_db.update_audio_openai_ids(self.audio_id, "file-p", "file-t")

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_deleting_an_audio_asks_for_its_documents_to_go(self):
        with patch.object(prosodia_db, "_remove_from_knowledge_base") as cleanup:
            prosodia_db.delete_audio(self.audio_id)

        cleanup.assert_called_once()
        chamada = cleanup.call_args.kwargs
        self.assertEqual(chamada["project_id"], self.project_id)
        self.assertEqual(chamada["session_id"], "sessao-1")
        self.assertEqual(sorted(chamada["file_ids"]), ["file-p", "file-t"])

    def test_deleting_a_project_asks_for_the_whole_project(self):
        with patch.object(prosodia_db, "_remove_from_knowledge_base") as cleanup:
            prosodia_db.delete_project(self.project_id)

        cleanup.assert_called_once_with(project_id=self.project_id, whole_project=True)

    def test_reprocessing_removes_the_files_it_replaces(self):
        with patch.object(prosodia_db, "_remove_from_knowledge_base") as cleanup:
            prosodia_db.update_audio_openai_ids(self.audio_id, "file-p2", "file-t")

        # "file-t" continua valendo; so o substituido sai.
        cleanup.assert_called_once_with(file_ids=["file-p"])


if __name__ == "__main__":
    unittest.main()
