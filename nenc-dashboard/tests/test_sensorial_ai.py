"""IA do Teste Sensorial com a OpenAI simulada: modos, base, prompt e chat."""

import unittest

from tests.test_jornada_ai import _FakeCreate
from tests.test_sensorial_ingest import CANARY_WORDS, _indicadores_csv
from tests.test_sensorial_model import BundleBase, _windows
from utils import sensorial_ai, sensorial_ingest, sensorial_metrics, sensorial_prompts

SESSIONS = ([("a{}".format(i), "P{:02d}".format(i), "2001A", "A") for i in range(1, 7)]
            + [("n{}".format(i), "P{:02d}".format(i), "2001Neutro", "N") for i in range(1, 7)])


class _Base(BundleBase):
    def setUp(self):
        super().setUp()
        self._file("eeg_psd", _windows(SESSIONS, stages=("Basal", "Olfacao"), per_stage=6))
        # Uma tabela lida do pipeline com o nome-canário: o nome não pode chegar ao prompt.
        self._file("eeg_indicadores", sensorial_ingest.parse_file("indicadores.csv", _indicadores_csv()).table)
        self.model = self._model({"indices": {"nomes": {"FAI": "Atratividade"}}})
        self.metrics = sensorial_metrics.compute_all(self.model)
        self.project = {"id": 21, "name": "Estudo", "objetivo": "Comparar fragrâncias.",
                        "briefing_text": "x" * 10_000}

    def _generate(self, mode, create, vector_store_id=None):
        return sensorial_ai.generate_analysis(self.project, self.model, self.metrics, mode=mode, ai_model="m",
                                              recorte="todo o projeto", vector_store_id=vector_store_id,
                                              create=create)


class GenerateTests(_Base):
    def test_quick_mode_is_one_call_filtered_to_this_project_and_module(self):
        create = _FakeCreate(["Relatório"])
        result = self._generate("rapida", create, vector_store_id="vs_s")
        call = create.calls[0]
        self.assertEqual(len(create.calls), 1)
        self.assertIn("Relatório Geral do Projeto", call["system_prompt"])
        self.assertIn("**Atratividade** (FAI)", call["system_prompt"])  # o nome de negócio do projeto
        self.assertIn("Holm", call["system_prompt"])
        clause = call["kb_filter"]["filters"][1]
        self.assertIn({"type": "eq", "key": "project_id", "value": 21}, clause["filters"])
        self.assertIn({"type": "eq", "key": "modulo", "value": "teste_sensorial"}, clause["filters"])
        self.assertEqual(result["text"], "Relatório")

    def test_without_a_store_there_is_no_filter(self):
        create = _FakeCreate(["Relatório"])
        self._generate("rapida", create)
        self.assertIsNone(create.calls[0]["vector_store_id"])
        self.assertIsNone(create.calls[0]["kb_filter"])

    def test_deep_mode_reads_the_numbers_first(self):
        create = _FakeCreate(["Leitura.", "Estratégia."])
        result = self._generate("aprofundada", create, vector_store_id="vs_s")
        first, second = create.calls
        self.assertIsNone(first["vector_store_id"])
        self.assertIn("Leitura.", second["user_prompt"])
        self.assertIn(first["user_prompt"], second["user_prompt"])
        self.assertIn("## Interpretação estratégica\n\nEstratégia.", result["text"])

    def test_the_prompt_carries_evidence_in_tags_without_names_and_under_the_cap(self):
        prompt = sensorial_prompts.build_sensorial_project_user_prompt(self.project, self.model, self.metrics)
        self.assertIn("<desenho>", prompt)
        self.assertIn("<eeg>", prompt)
        self.assertIn("[briefing truncado]", prompt)
        self.assertTrue(prompt.endswith("mantenha as limitações."))
        for word in CANARY_WORDS:
            self.assertNotIn(word, prompt.lower())
        small = sensorial_prompts.build_sensorial_project_user_prompt(self.project, self.model, self.metrics,
                                                                      max_chars=3_000)
        self.assertLessEqual(len(small), 3_000)
        self.assertTrue(small.endswith("mantenha as limitações."))

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self._generate("outro", _FakeCreate(["x"]))


class KnowledgeBaseAndChatTests(unittest.TestCase):
    def test_the_analysis_goes_in_as_analysis_of_this_project(self):
        calls = []

        def add(store, filename, content, attributes, wait):
            calls.append((filename, content, attributes))
            return {"id": "file_7"}

        analysis = {"id": 3, "mode": "rapida", "model": "m", "analysis_text": "Texto."}
        file_id = sensorial_ai.send_analysis_to_kb({"id": 5, "name": "Estudo"}, analysis, "todo o projeto", "vs",
                                                   add=add)
        filename, content, attributes = calls[0]
        self.assertEqual((file_id, filename), ("file_7", "analise_geral_ts_Estudo_3.md"))
        self.assertEqual((attributes["modulo"], attributes["project_id"], attributes["escopo"]),
                         ("teste_sensorial", 5, "analise"))
        self.assertIn("- Módulo: Teste Sensorial", content.decode("utf-8"))
        with self.assertRaises(ValueError):
            sensorial_ai.send_analysis_to_kb({"id": 5}, analysis, "", "", add=add)

    def test_the_chat_and_the_filter_text(self):
        create = _FakeCreate(["Resposta."])
        answer = sensorial_ai.chat_answer("## Síntese", {"achados": [], "limitacoes": ["n pequeno"]},
                                          [{"role": "user", "content": "E a amostra A?"}], ai_model="m",
                                          project_id=5, vector_store_id="vs", create=create)
        self.assertIn("<relatorio>", create.calls[0]["system_prompt"])
        self.assertIn("n pequeno", create.calls[0]["system_prompt"])
        self.assertTrue(answer.startswith("Resposta."))
        self.assertEqual(sensorial_ai.filters_text({}), "todo o projeto")
        self.assertEqual(sensorial_ai.filters_text({"perfil": {"sexo": ["F"]}, "comparar_por": "grupo"}),
                         "sexo = F; comparando grupos de grupo")


if __name__ == "__main__":
    unittest.main()
