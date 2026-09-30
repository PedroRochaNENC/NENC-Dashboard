import unittest
from unittest.mock import patch

from utils import ai_provider


class _Annotation:
    def __init__(self, file_id, filename, index=0, kind="file_citation"):
        self.type = kind
        self.file_id = file_id
        self.filename = filename
        self.index = index


class _ContentBlock:
    type = "output_text"

    def __init__(self, text, annotations=None):
        self.text = text
        self.annotations = annotations or []


class _Message:
    type = "message"

    def __init__(self, content):
        self.content = content


class _SearchResult:
    def __init__(self, file_id, filename, score, text):
        self.file_id = file_id
        self.filename = filename
        self.score = score
        self.text = text


class _SearchCall:
    type = "file_search_call"

    def __init__(self, queries, results=None, status="completed"):
        self.queries = queries
        self.results = results
        self.status = status


class _Response:
    def __init__(self, output):
        self.output = output


class _Responses:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class _AnalysisClient:
    def __init__(self, response):
        self.responses = _Responses(response)


class _UploadedFile:
    def __init__(self, file_id):
        self.id = file_id


class _Files:
    def __init__(self):
        self.deleted = []
        self.created = []

    def create(self, file, purpose):
        self.created.append((file, purpose))
        return _UploadedFile("file-novo")

    def delete(self, file_id):
        self.deleted.append(file_id)


class _VectorStoreFile:
    def __init__(self, file_id, status="completed", attributes=None):
        self.id = file_id
        self.status = status
        self.attributes = attributes


class _VectorStoreFiles:
    def __init__(self, fail_attach=False):
        self.fail_attach = fail_attach
        self.attributes = None

    def create_and_poll(self, file_id, vector_store_id, attributes=None):
        if self.fail_attach:
            raise RuntimeError("vector store recusou o arquivo")
        self.attributes = attributes
        return _VectorStoreFile(file_id, attributes=attributes)


class _UploadClient:
    def __init__(self, fail_attach=False):
        self.files = _Files()
        self.vector_stores = type(
            "VectorStores", (), {"files": _VectorStoreFiles(fail_attach)}
        )()


class CreateAnalysisTests(unittest.TestCase):
    """A analise precisa dizer o que a base devolveu, e provar que consultou."""

    def _run(self, output, **kwargs):
        client = _AnalysisClient(_Response(output))
        with patch.object(ai_provider, "get_openai_client", return_value=client):
            result = ai_provider.create_analysis("sistema", "usuario", **kwargs)
        return result, client.responses.kwargs

    def test_excerpt_and_score_come_from_the_search_result(self):
        output = [
            _SearchCall(
                queries=["variacao de pitch"],
                results=[
                    _SearchResult("file-a", "artigo.pdf", 0.42, "trecho fraco"),
                    _SearchResult("file-a", "artigo.pdf", 0.91, "  trecho forte  "),
                    _SearchResult("file-b", "outro.pdf", 0.30, "nunca citado"),
                ],
            ),
            _Message(
                [
                    _ContentBlock(
                        "analise",
                        [
                            _Annotation("file-a", "artigo.pdf", index=10),
                            _Annotation("file-a", "artigo.pdf", index=20),
                        ],
                    )
                ]
            ),
        ]

        result, request = self._run(output, vector_store_id="vs_1")

        self.assertEqual(result["text"], "analise")
        # O mesmo documento citado duas vezes e uma referencia, nao duas.
        self.assertEqual(len(result["citations"]), 1)
        citation = result["citations"][0]
        self.assertEqual(citation["filename"], "artigo.pdf")
        self.assertEqual(citation["quote"], "trecho forte")
        self.assertEqual(citation["score"], 0.91)
        self.assertTrue(result["search"]["searched"])
        self.assertEqual(result["search"]["excerpts"], 3)
        self.assertEqual(result["search"]["queries"], ["variacao de pitch"])

        self.assertEqual(request["tools"][0]["vector_store_ids"], ["vs_1"])
        self.assertEqual(request["tools"][0]["max_num_results"], 10)
        self.assertEqual(request["tool_choice"], {"type": "file_search"})
        self.assertEqual(request["include"], ["file_search_call.results"])

    def test_search_that_found_nothing_differs_from_search_never_made(self):
        vazia, _ = self._run(
            [
                _SearchCall(queries=["assunto ausente"], results=[]),
                _Message([_ContentBlock("analise", [])]),
            ],
            vector_store_id="vs_1",
        )
        self.assertTrue(vazia["search"]["searched"])
        self.assertEqual(vazia["search"]["excerpts"], 0)
        self.assertEqual(vazia["citations"], [])

        ignorada, _ = self._run(
            [_Message([_ContentBlock("analise", [])])],
            vector_store_id="vs_1",
        )
        self.assertFalse(ignorada["search"]["searched"])
        self.assertTrue(ignorada["search"]["available"])

    def test_without_a_vector_store_no_tool_is_declared(self):
        result, request = self._run([_Message([_ContentBlock("analise", [])])])

        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertNotIn("include", request)
        self.assertFalse(result["search"]["searched"])
        # Base desligada nao e o mesmo defeito de base ligada e ignorada.
        self.assertFalse(result["search"]["available"])

    def test_score_threshold_is_only_sent_when_asked_for(self):
        _, sem_corte = self._run(
            [_Message([_ContentBlock("analise", [])])], vector_store_id="vs_1"
        )
        self.assertNotIn("ranking_options", sem_corte["tools"][0])

        _, com_corte = self._run(
            [_Message([_ContentBlock("analise", [])])],
            vector_store_id="vs_1",
            score_threshold=0.5,
        )
        self.assertEqual(
            com_corte["tools"][0]["ranking_options"], {"score_threshold": 0.5}
        )


class DocumentAttributesTests(unittest.TestCase):
    def test_empty_values_are_dropped_and_text_is_trimmed(self):
        cleaned = ai_provider.clean_document_attributes(
            {
                "escopo": "referencia",
                "projeto": "  Marca X  ",
                "marca": "",
                "categoria": None,
                "ano": 2025,
                "revisado": True,
                "resumo": "a" * 600,
            }
        )

        self.assertEqual(cleaned["projeto"], "Marca X")
        self.assertEqual(cleaned["ano"], 2025)
        self.assertIs(cleaned["revisado"], True)
        self.assertEqual(len(cleaned["resumo"]), 512)
        self.assertNotIn("marca", cleaned)
        self.assertNotIn("categoria", cleaned)

    def test_at_most_sixteen_attributes_are_kept(self):
        cleaned = ai_provider.clean_document_attributes(
            {"chave_{}".format(index): index for index in range(30)}
        )
        self.assertEqual(len(cleaned), 16)


class AddDocumentTests(unittest.TestCase):
    def test_attributes_reach_the_vector_store(self):
        client = _UploadClient()
        with patch.object(ai_provider, "get_openai_client", return_value=client):
            document = ai_provider.add_document_to_vector_store(
                "vs_1", "artigo.pdf", b"conteudo", {"tipo": "artigo", "marca": ""}
            )

        self.assertEqual(document.status, "completed")
        self.assertEqual(client.vector_stores.files.attributes, {"tipo": "artigo"})
        self.assertEqual(client.files.deleted, [])

    def test_a_failed_attach_does_not_leave_an_orphan_file(self):
        client = _UploadClient(fail_attach=True)
        with patch.object(ai_provider, "get_openai_client", return_value=client):
            with self.assertRaises(RuntimeError):
                ai_provider.add_document_to_vector_store(
                    "vs_1", "artigo.pdf", b"conteudo"
                )

        # O arquivo chegou a ser criado na OpenAI: sem esta limpeza ele ficaria
        # pago, fora de qualquer base e invisivel na tela.
        self.assertEqual(client.files.deleted, ["file-novo"])


class _ListVectorStoreFiles:
    def __init__(self, files):
        self._files = files
        self.calls = 0

    def list(self, vector_store_id):
        self.calls += 1
        return list(self._files)


class _ListFiles:
    def __init__(self, files):
        self._files = files
        self.list_calls = 0
        self.retrieve_calls = 0

    def list(self, **kwargs):
        self.list_calls += 1
        return list(self._files)

    def retrieve(self, file_id):
        self.retrieve_calls += 1
        raise AssertionError("files.retrieve por documento e o gargalo removido")


class _OpenAIFile:
    def __init__(self, file_id, filename, size):
        self.id = file_id
        self.filename = filename
        self.bytes = size


class _ListClient:
    def __init__(self, vector_store_files, openai_files):
        self.files = _ListFiles(openai_files)
        self.vector_stores = type(
            "VectorStores", (), {"files": _ListVectorStoreFiles(vector_store_files)}
        )()


class ListVectorStoreDocumentsTests(unittest.TestCase):
    def setUp(self):
        ai_provider.list_vector_store_documents.clear()

    def test_one_listing_resolves_every_name(self):
        client = _ListClient(
            [
                _VectorStoreFile("file-a", attributes={"tipo": "artigo"}),
                _VectorStoreFile("file-b", status="in_progress"),
            ],
            [
                _OpenAIFile("file-a", "artigo.pdf", 2048),
                _OpenAIFile("file-b", "relatorio.pptx", 0),
                _OpenAIFile("file-z", "de-outra-base.pdf", 999),
            ],
        )

        with patch.object(ai_provider, "get_openai_client", return_value=client):
            documents = ai_provider.list_vector_store_documents("vs_1")

        self.assertEqual(
            documents,
            [
                {
                    "id": "file-a",
                    "filename": "artigo.pdf",
                    "size_kb": 2.0,
                    "status": "completed",
                    "attributes": {"tipo": "artigo"},
                },
                {
                    "id": "file-b",
                    "filename": "relatorio.pptx",
                    "size_kb": 0.0,
                    "status": "in_progress",
                    "attributes": {},
                },
            ],
        )
        self.assertEqual(client.files.list_calls, 1)
        self.assertEqual(client.files.retrieve_calls, 0)

    def test_the_listing_is_cached_between_reruns(self):
        client = _ListClient([_VectorStoreFile("file-a")], [])

        with patch.object(ai_provider, "get_openai_client", return_value=client):
            ai_provider.list_vector_store_documents("vs_2")
            ai_provider.list_vector_store_documents("vs_2")

        self.assertEqual(client.vector_stores.files.calls, 1)

        ai_provider.list_vector_store_documents.clear()
        with patch.object(ai_provider, "get_openai_client", return_value=client):
            ai_provider.list_vector_store_documents("vs_2")

        self.assertEqual(client.vector_stores.files.calls, 2)

    def test_without_file_names_the_ids_still_show_up(self):
        client = _ListClient([_VectorStoreFile("file-a")], [])
        client.files.list = lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("sem permissao")
        )

        with patch.object(ai_provider, "get_openai_client", return_value=client):
            documents = ai_provider.list_vector_store_documents("vs_3")

        self.assertEqual(documents[0]["filename"], "file-a")
        self.assertEqual(documents[0]["size_kb"], 0.0)


class _Recorder:
    """Guarda os argumentos da chamada e devolve uma resposta pronta."""

    def __init__(self, response):
        self._response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class _TextBlock:
    def __init__(self, text, kind="text"):
        self.type = kind
        self.text = text


class _AnthropicClient:
    def __init__(self, blocks):
        self.messages = _Recorder(type("Resp", (), {"content": blocks})())


class _ChatClient:
    def __init__(self, text):
        message = type("Msg", (), {"content": text})()
        choice = type("Choice", (), {"message": message})()
        completions = _Recorder(type("Resp", (), {"choices": [choice]})())
        self.chat = type("Chat", (), {"completions": completions})()


class ChatCompletionTests(unittest.TestCase):
    def test_claude_receives_the_system_prompt_apart_and_no_temperature(self):
        client = _AnthropicClient([_TextBlock("Olá, "), _TextBlock("mundo")])
        history = [{"role": "user", "content": "Pergunta"}]

        with patch.object(ai_provider, "get_anthropic_client", return_value=client):
            text = ai_provider.chat_completion(
                ai_provider.PROVIDER_ANTHROPIC,
                "claude-sonnet-5-5",
                "Sistema",
                history,
                temperature=0.7,
                max_tokens=100,
            )

        self.assertEqual(text, "Olá, mundo")
        kwargs = client.messages.kwargs
        self.assertEqual(kwargs["system"], "Sistema")
        self.assertEqual(kwargs["messages"], history)
        self.assertEqual(kwargs["max_tokens"], 100)
        self.assertNotIn("temperature", kwargs)

    def test_groq_gets_the_system_prompt_as_first_message(self):
        client = _ChatClient("resposta")

        with patch.object(ai_provider, "get_groq_client", return_value=client):
            text = ai_provider.chat_completion(
                ai_provider.PROVIDER_GROQ,
                "llama-3.3-70b-versatile",
                "Sistema",
                [{"role": "user", "content": "Pergunta"}],
            )

        self.assertEqual(text, "resposta")
        messages = client.chat.completions.kwargs["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "Sistema"})
        self.assertEqual(messages[1]["content"], "Pergunta")

    def test_a_provider_without_key_says_which_variable_is_missing(self):
        with patch.object(ai_provider, "get_anthropic_client", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
                ai_provider.chat_completion(
                    ai_provider.PROVIDER_ANTHROPIC, "claude-sonnet-5-5", "s", []
                )


class AvailableModelsTests(unittest.TestCase):
    def test_only_providers_with_a_key_reach_the_selector(self):
        clients = {
            ai_provider.PROVIDER_OPENAI: None,
            ai_provider.PROVIDER_GROQ: object(),
            ai_provider.PROVIDER_ANTHROPIC: object(),
        }

        with patch.object(ai_provider, "get_provider_client", side_effect=clients.get):
            options = ai_provider.available_models()
            missing = ai_provider.unavailable_providers()

        providers = {provider for provider, _ in options}
        self.assertEqual(
            providers, {ai_provider.PROVIDER_GROQ, ai_provider.PROVIDER_ANTHROPIC}
        )
        self.assertEqual(missing, [ai_provider.PROVIDER_OPENAI])
        self.assertEqual(
            ai_provider.format_model_option(("anthropic", "claude-sonnet-5-5")),
            "Claude · claude-sonnet-5-5",
        )

    def test_the_example_placeholder_does_not_count_as_a_key(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "your_groq_api_key_here"}):
            self.assertEqual(ai_provider._configured_key("GROQ_API_KEY"), "")
        with patch.dict("os.environ", {"GROQ_API_KEY": " gsk_real "}):
            self.assertEqual(ai_provider._configured_key("GROQ_API_KEY"), "gsk_real")


class GenerateAnalysisTests(unittest.TestCase):
    def test_openai_keeps_going_through_create_analysis(self):
        with patch.object(
            ai_provider, "create_analysis", return_value={"text": "ok"}
        ) as create:
            ai_provider.generate_analysis(
                ai_provider.PROVIDER_OPENAI, "gpt-4.1", "s", "u",
                vector_store_id="vs_1", kb_filter={"k": "v"},
            )

        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4.1")
        self.assertEqual(kwargs["vector_store_id"], "vs_1")
        self.assertEqual(kwargs["kb_filter"], {"k": "v"})

    def test_other_providers_report_the_base_as_not_used(self):
        with patch.object(ai_provider, "chat_completion", return_value="texto") as chat:
            result = ai_provider.generate_analysis(
                ai_provider.PROVIDER_ANTHROPIC, "claude-sonnet-5-5", "s", "u",
                vector_store_id="vs_1",
            )

        self.assertEqual(result["text"], "texto")
        self.assertEqual(result["citations"], [])
        self.assertFalse(result["search"]["available"])
        self.assertEqual(chat.call_args.args[3], [{"role": "user", "content": "u"}])


class CoverageClientTests(unittest.TestCase):
    def test_openai_uses_the_cheap_model_and_others_the_selected_one(self):
        openai_client, groq_client = object(), object()
        clients = {
            ai_provider.PROVIDER_OPENAI: openai_client,
            ai_provider.PROVIDER_GROQ: groq_client,
        }

        with patch.object(ai_provider, "get_provider_client", side_effect=clients.get):
            self.assertEqual(
                ai_provider.coverage_client(ai_provider.PROVIDER_OPENAI, "gpt-4o"),
                (openai_client, "gpt-4.1-mini"),
            )
            self.assertEqual(
                ai_provider.coverage_client(ai_provider.PROVIDER_GROQ, "llama"),
                (groq_client, "llama"),
            )
            self.assertEqual(
                ai_provider.coverage_client(ai_provider.PROVIDER_ANTHROPIC, "claude"),
                (None, None),
            )
        self.assertEqual(ai_provider.coverage_client(None, None), (None, None))

    def test_question_coverage_accepts_a_claude_client(self):
        from utils import prosodia_quality

        client = _AnthropicClient([_TextBlock("[]")])
        prosodia_quality._coverage_ai_batch("transcricao", ["Pergunta?"], client, "claude-sonnet-5-5")

        kwargs = client.messages.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-5-5")
        self.assertEqual(kwargs["system"], prosodia_quality.COVERAGE_SYSTEM_PROMPT)
        self.assertNotIn("temperature", kwargs)


class _GroqModels:
    def __init__(self, ids, error=None):
        self._ids = ids
        self._error = error

    def list(self):
        if self._error:
            raise self._error
        model = lambda i: type("Model", (), {"id": i})()
        return type("Page", (), {"data": [model(i) for i in self._ids]})()


class GroqModelListTests(unittest.TestCase):
    def setUp(self):
        ai_provider._groq_account_models.clear()

    def tearDown(self):
        ai_provider._groq_account_models.clear()

    def _groq(self, ids, error=None):
        client = type("Groq", (), {})()
        client.models = _GroqModels(ids, error)
        return client

    def test_retired_model_leaves_the_selector_and_preferred_come_first(self):
        client = self._groq(
            ["whisper-large-v3", "zeta-chat", "openai/gpt-oss-120b", "alpha-chat"]
        )

        with patch.object(ai_provider, "get_groq_client", return_value=client):
            models = ai_provider._provider_models(ai_provider.PROVIDER_GROQ)

        self.assertEqual(models, ["openai/gpt-oss-120b", "alpha-chat", "zeta-chat"])

    def test_a_failed_listing_falls_back_to_the_fixed_list(self):
        client = self._groq([], error=RuntimeError("fora do ar"))

        with patch.object(ai_provider, "get_groq_client", return_value=client):
            models = ai_provider._provider_models(ai_provider.PROVIDER_GROQ)

        self.assertEqual(models, ai_provider.PROVIDER_MODELS[ai_provider.PROVIDER_GROQ])


class AnthropicWorkspaceTests(unittest.TestCase):
    def setUp(self):
        ai_provider.get_anthropic_client.clear()

    def tearDown(self):
        ai_provider.get_anthropic_client.clear()

    def test_workspace_id_goes_as_header_when_configured(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-x", "ANTHROPIC_WORKSPACE_ID": "wrkspc_1"}
        with patch.dict("os.environ", env):
            client = ai_provider.get_anthropic_client()

        self.assertEqual(client.default_headers.get("anthropic-workspace-id"), "wrkspc_1")

    def test_no_workspace_header_without_the_variable(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}):
            os_env = __import__("os").environ
            os_env.pop("ANTHROPIC_WORKSPACE_ID", None)
            client = ai_provider.get_anthropic_client()

        self.assertNotIn("anthropic-workspace-id", client.default_headers)


if __name__ == "__main__":
    unittest.main()
