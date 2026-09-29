"""IA da Jornada com a OpenAI simulada: modos, base de conhecimento e chat."""

import unittest

from tests.test_jornada_model import SAMPLES, _bundle, _file, _frames
from utils import jornada_ai
from utils.jornada_metrics import compute_all
from utils.jornada_model import build_model


class _FakeCreate:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": self.texts.pop(0), "citations": [{"filename": "briefing.docx", "quote": "trecho"}],
                "search": {"available": bool(kwargs.get("vector_store_id")), "searched": True}}


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.bundle = _bundle([
            _file(1, "DSP1234-INDIVIDUAL2.csv", SAMPLES),
            _file(2, "Pt01-JLivre-DSP1234.csv", _frames(200, 0.05)),
        ])
        self.project = dict(self.bundle["project"], id=11)
        self.model = build_model(self.bundle)
        self.metrics = compute_all(self.model, {})

    def _generate(self, mode, create, vector_store_id=None):
        return jornada_ai.generate_analysis(
            self.project, self.model, self.metrics, mode=mode, ai_model="gpt-4.1-mini",
            recorte="Tarefas: todas", vector_store_id=vector_store_id, create=create,
        )

    def test_quick_mode_is_one_call_filtered_to_this_project(self):
        create = _FakeCreate(["Relatório"])
        result = self._generate("rapida", create, vector_store_id="vs_1")
        self.assertEqual(len(create.calls), 1)
        call = create.calls[0]
        self.assertIn("Relatório Geral do Projeto", call["system_prompt"])
        self.assertEqual(call["vector_store_id"], "vs_1")
        project_clause = call["kb_filter"]["filters"][1]
        self.assertIn({"type": "eq", "key": "project_id", "value": 11}, project_clause["filters"])
        self.assertIn({"type": "eq", "key": "modulo", "value": "jornada_compra"}, project_clause["filters"])
        self.assertEqual(result["text"], "Relatório")
        self.assertEqual(result["citations"][0]["filename"], "briefing.docx")

    def test_without_a_store_there_is_no_filter(self):
        create = _FakeCreate(["Relatório"])
        self._generate("rapida", create)
        self.assertIsNone(create.calls[0]["vector_store_id"])
        self.assertIsNone(create.calls[0]["kb_filter"])

    def test_deep_mode_reads_the_numbers_first_and_keeps_the_base_prompt(self):
        create = _FakeCreate(["Leitura: Marca A 60%.", "Estratégia."])
        result = self._generate("aprofundada", create, vector_store_id="vs_1")
        self.assertEqual(len(create.calls), 2)
        first, second = create.calls
        self.assertIsNone(first["vector_store_id"])
        self.assertIn("Leitura: Marca A 60%.", second["user_prompt"])
        self.assertIn(first["user_prompt"], second["user_prompt"])
        self.assertEqual(second["vector_store_id"], "vs_1")
        self.assertIn("## Leitura estatística", result["text"])
        self.assertIn("## Interpretação estratégica\n\nEstratégia.", result["text"])

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self._generate("outro", _FakeCreate(["x"]))


class KnowledgeBaseTests(unittest.TestCase):
    def test_the_analysis_goes_in_as_analysis_of_this_project(self):
        calls = []

        def add(store, filename, content, attributes, wait):
            calls.append((store, filename, content, attributes, wait))
            return type("Stored", (), {"id": "file_9"})()

        project = {"id": 4, "name": "Estudo Ótimo"}
        analysis = {"id": 12, "mode": "rapida", "model": "m", "created_at": "2026-09-29 10:00",
                    "analysis_text": "## Síntese\nTexto.", "citations": [{"filename": "b.docx", "quote": "q"}]}
        file_id = jornada_ai.send_analysis_to_kb(project, analysis, "Tarefas: todas", "vs_1", add=add)
        self.assertEqual(file_id, "file_9")
        store, filename, content, attributes, wait = calls[0]
        self.assertEqual(filename, "analise_geral_jc_Estudo_Otimo_12.md")
        self.assertEqual(attributes["escopo"], "analise")
        self.assertEqual(attributes["project_id"], 4)
        self.assertEqual(attributes["modulo"], "jornada_compra")
        self.assertIn("Recorte: Tarefas: todas", content.decode("utf-8"))
        self.assertFalse(wait)

    def test_without_a_store_nothing_is_sent(self):
        with self.assertRaises(ValueError):
            jornada_ai.send_analysis_to_kb({"id": 1}, {"id": 2}, "", "", add=lambda *a, **k: None)


class ChatTests(unittest.TestCase):
    def test_the_chat_sends_the_conversation_and_lists_references(self):
        create = _FakeCreate(["Resposta."])
        history = [{"role": "user", "content": "Primeira?"}, {"role": "assistant", "content": "Sim."},
                   {"role": "user", "content": "E a marca foco?"}]
        answer = jornada_ai.chat_answer("## Síntese", {"findings": []}, history, ai_model="m", project_id=3,
                                        vector_store_id="vs_1", create=create)
        call = create.calls[0]
        self.assertTrue(call["user_prompt"].endswith("Usuário: E a marca foco?"))
        self.assertIn("<relatorio>", call["system_prompt"])
        self.assertTrue(answer.startswith("Resposta."))
        self.assertIn("*briefing.docx*", answer)


if __name__ == "__main__":
    unittest.main()
