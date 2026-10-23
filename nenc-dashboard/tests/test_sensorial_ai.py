"""IA do Teste Sensorial com a OpenAI simulada: modos, base, prompt e chat."""

import json
import unittest

import pandas as pd

from tests.test_jornada_ai import _FakeCreate
from tests.test_sensorial_ingest import CANARY_WORDS, _indicadores_csv
from tests.test_sensorial_model import BundleBase, _windows
from utils import sensorial_ai, sensorial_design, sensorial_ingest, sensorial_metrics, sensorial_prompts

SESSIONS = ([("a{}".format(i), "P{:02d}".format(i), "2001A", "A") for i in range(1, 7)]
            + [("n{}".format(i), "P{:02d}".format(i), "2001Neutro", "N") for i in range(1, 7)])
TAXONOMIA = "Benefício > Energia física: Energia; Revigorante\nExperiência > Primeira impressão: Olfação"


class _Base(BundleBase):
    def setUp(self):
        super().setUp()
        self._file("eeg_psd", _windows(SESSIONS, stages=("Basal", "Olfacao"), per_stage=6))
        # Uma tabela lida do pipeline com o nome-canário: o nome não pode chegar ao prompt.
        self._file("eeg_indicadores", sensorial_ingest.parse_file("indicadores.csv", _indicadores_csv()).table)
        self.model = self._model({"indices": {"nomes": {"FAI": "Atratividade"}}})
        self.metrics = sensorial_metrics.compute_all(self.model)
        self.project = {"id": 21, "name": "Estudo", "objetivo": "Comparar fragrâncias.",
                        "briefing_text": "x" * 10_000, "taxonomia": TAXONOMIA, "taxonomia_status": "aprovada"}

    def _generate(self, mode, create, vector_store_id=None, **kwargs):
        return sensorial_ai.generate_analysis(self.project, self.model, self.metrics, mode=mode, ai_model="m",
                                              recorte="todo o projeto", vector_store_id=vector_store_id,
                                              create=create, **kwargs)


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
        self.assertTrue(result["text"].startswith("Estratégia."))
        self.assertTrue(result["text"].endswith("## Apêndice técnico\n\nLeitura."))
        self.assertIn("Apêndice técnico", first["system_prompt"])
        self.assertIn("Não escreva apêndice técnico", second["system_prompt"])

    def test_the_prompt_carries_evidence_in_tags_without_names_and_under_the_cap(self):
        prompt = sensorial_prompts.build_sensorial_project_user_prompt(self.project, self.model, self.metrics)
        self.assertIn("<desenho>", prompt)
        self.assertIn("<eeg>", prompt)
        self.assertIn("[briefing truncado]", prompt)
        self.assertTrue(prompt.endswith("números para o apêndice."))
        for word in CANARY_WORDS:
            self.assertNotIn(word, prompt.lower())
        small = sensorial_prompts.build_sensorial_project_user_prompt(self.project, self.model, self.metrics,
                                                                      max_chars=3_000)
        self.assertLessEqual(len(small), 3_000)
        self.assertTrue(small.endswith("números para o apêndice."))

    def test_the_system_prompt_asks_for_themes_with_three_blocks_for_marketing(self):
        create = _FakeCreate(["Relatório"])
        self._generate("rapida", create)
        system = create.calls[0]["system_prompt"]
        for text in ("O que vimos", "O que pode significar", "O que fazer", "Pontos a conferir com a equipe",
                     "Amostras neste relatório", "Apêndice técnico", "O relatório do estudo prevalece",
                     "Comunicação / Marketing", "Produto / P&D", "CX / Experiência"):
            self.assertIn(text, system)
        self.assertEqual(create.calls[0]["max_tokens"], sensorial_ai.REPORT_MAX_TOKENS)

    def test_the_study_report_comes_right_after_the_context_and_the_confirmed_names(self):
        (code,) = sensorial_ai.sample_codes(self.model)
        label = sensorial_design.condition_labels(self.model["design"])[code]
        self.project["nomes_amostras"] = json.dumps({code: "Zinger 0,1%", "X9": "Fora do desenho"})
        create = _FakeCreate(["Relatório"])
        result = self._generate("rapida", create, report=("Relatorio.pptx", "--- Slide 1 ---\nZinger lidera."))
        self.assertEqual(len(create.calls), 1)  # os nomes vêm do cadastro, sem chamada à IA
        self.assertEqual(result["amostras"], {label: "Zinger 0,1%"})
        prompt = create.calls[0]["user_prompt"]
        self.assertIn("<nomes_amostras>\n{} = Zinger 0,1%\n</nomes_amostras>".format(label), prompt)
        self.assertIn('<relatorio_estudo arquivo="Relatorio.pptx">\n--- Slide 1 ---\nZinger lidera.', prompt)
        order = [prompt.index(tag) for tag in ("<contexto_projeto>", "<nomes_amostras>", "<relatorio_estudo",
                                               "<taxonomia>", "<desenho>")]
        self.assertEqual(order, sorted(order))
        self.assertIn("aprovada pela equipe", prompt)

    def test_without_confirmed_names_the_text_keeps_the_chart_labels(self):
        create = _FakeCreate(["Relatório"])
        result = self._generate("rapida", create, report=("R.pptx", "texto"))
        self.assertEqual(result["amostras"], {})
        self.assertNotIn("<nomes_amostras>", create.calls[0]["user_prompt"])
        self.assertIn("fica com o rótulo técnico", create.calls[0]["system_prompt"])

    def test_recommendation_areas_are_brought_to_the_fixed_list(self):
        text = ("- Lançar — Área: Marketing · Prioridade: Alta · Base: x\n"
                "- Ajustar — Área: P&D · Prioridade: Média\n"
                "- Ativar — Área: Experiência do cliente\n"
                "- Testar — Área: Pesquisa · Prioridade: Baixa")
        self.assertEqual(sensorial_prompts.normalize_areas(text),
                         "- Lançar — Área: Comunicação / Marketing · Prioridade: Alta · Base: x\n"
                         "- Ajustar — Área: Produto / P&D · Prioridade: Média\n"
                         "- Ativar — Área: CX / Experiência\n"
                         "- Testar — Área: Pesquisa · Prioridade: Baixa")
        create = _FakeCreate(["Ação — Área: Comunicação · Prioridade: Alta"])
        self.assertIn("Área: Comunicação / Marketing ·", self._generate("rapida", create)["text"])

    def test_the_number_lines_are_read_and_the_rest_ignored(self):
        text = "Segue:\n**AMOSTRA:** Clash MOD 8 (IAT) | 9,47; 7,18 ;\nAMOSTRA: sem números\n- AMOSTRA: Clash MOD 8 | 0,24"
        self.assertEqual(sensorial_prompts.parse_sample_numbers(text), {"Clash MOD 8": ["9,47", "7,18", "0,24"]})

    def test_a_long_report_is_capped_and_the_tables_are_cut_first(self):
        long_report = ("Relatorio.pptx", "r" * (sensorial_prompts.MAX_REPORT_CHARS + 500))
        prompt = sensorial_prompts.build_sensorial_project_user_prompt(self.project, self.model, self.metrics,
                                                                       report=long_report)
        self.assertIn("r\n...[relatório truncado]\n</relatorio_estudo>", prompt)
        self.assertLessEqual(len(prompt), sensorial_prompts.MAX_USER_PROMPT_CHARS)
        short_report = ("Relatorio.pptx", "Conclusão do estudo. " * 100)
        small = sensorial_prompts.build_sensorial_project_user_prompt(self.project, self.model, self.metrics,
                                                                      report=short_report, max_chars=9_000)
        self.assertIn(short_report[1].strip() + "\n</relatorio_estudo>", small)
        self.assertNotIn("<eeg>", small)
        self.assertLessEqual(len(small), 9_000)

    def test_without_a_taxonomy_the_ai_proposes_one_that_is_saved_as_provisional(self):
        self.project.update(taxonomia="", taxonomia_status="")
        saved = []
        create = _FakeCreate(["Benefício > Energia: Energia\nExperiência > Na hora: Olfação", "Relatório"])
        result = self._generate("rapida", create, report=("R.pptx", "texto do relatório"),
                                save_taxonomy=lambda texto, status: saved.append((texto, status)))
        proposal, report_call = create.calls
        self.assertIsNone(proposal["vector_store_id"])
        self.assertIn("<relatorio_estudo>", proposal["user_prompt"])
        self.assertIn("<etapas>\n- Olfação\n", proposal["user_prompt"])
        self.assertNotIn("- Basal", proposal["user_prompt"])  # a referência não é um momento da experiência
        self.assertEqual(saved, [("Benefício > Energia: Energia\nExperiência > Na hora: Olfação", "provisoria")])
        self.assertIn("provisória, proposta pela IA", report_call["user_prompt"])
        self.assertEqual((result["taxonomia"]["proposta"], result["taxonomia"]["aviso"]), (True, ""))

    def test_a_proposal_that_cannot_be_saved_still_feeds_the_report(self):
        self.project.update(taxonomia="")

        def refuse(texto, status):
            raise PermissionError("outro autor")

        create = _FakeCreate(["Benefício > Energia: Energia", "Relatório"])
        result = self._generate("rapida", create, save_taxonomy=refuse)
        self.assertIn("Benefício > Energia: Energia", create.calls[1]["user_prompt"])
        self.assertIn("outro autor", result["taxonomia"]["aviso"])

    def test_a_proposal_out_of_format_leaves_the_grouping_to_the_report(self):
        self.project.update(taxonomia="")
        saved = []
        create = _FakeCreate(["Não sei.", "Relatório"])
        result = self._generate("rapida", create, save_taxonomy=lambda *args: saved.append(args))
        self.assertEqual(saved, [])
        self.assertIn("Taxonomia de Temas (ausente)", create.calls[1]["user_prompt"])
        self.assertFalse(result["taxonomia"]["proposta"])
        self.assertTrue(result["taxonomia"]["aviso"])

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self._generate("outro", _FakeCreate(["x"]))


def _design_and_metrics():
    """Três amostras e um controle, com números que se repetem entre amostras, como no 1055."""
    conditions = [("A", "Amostra A", "amostra"), ("B1", "Amostra B1", "amostra"), ("B2", "Amostra B2", "amostra"),
                  ("N", "Neutro", "controle")]
    model = {"design": {"condicoes": [{"codigo": c, "rotulo": r, "papel": p} for c, r, p in conditions]}}
    eeg = pd.DataFrame([
        {"medida": "ENERGIA", "condicao": "B1", "etapa": "pos", "media": 9.4689, "mediana": 7.09},
        {"medida": "ATENCAO", "condicao": "B1", "etapa": "pos", "media": 7.1801, "mediana": 5.65},
        {"medida": "FAI", "condicao": "A", "etapa": "pos", "media": 0.1331, "mediana": 0.99},
        {"medida": "FAI", "condicao": "B1", "etapa": "pos", "media": 0.9912, "mediana": 0.2},
        {"medida": "FAI", "condicao": "N", "etapa": "pos", "media": 5.55, "mediana": 0.5},
    ])
    association = pd.DataFrame([
        {"condicao": "A", "score": 0.0644, "pct_sim": 0.485, "cr_sim": 0.219},
        {"condicao": "B1", "score": 0.267, "pct_sim": 0.879, "cr_sim": 0.303},
        {"condicao": "B2", "score": 0.218, "pct_sim": 0.457, "cr_sim": -0.216},
    ])
    return model, {"eeg": {"resumo": eeg}, "associacao": {"por_condicao": association}}


class SampleNameTests(unittest.TestCase):
    def setUp(self):
        self.model, self.metrics = _design_and_metrics()

    def test_unique_numbers_vote_and_named_samples_leave_the_search(self):
        numbers = {"Zinger 0,1%": ["0,13", "0,0644"],
                   "Zinger 0,001%": ["0,99", "0,219", "0,22"],
                   "Clash MOD 8": ["9,47", "7,18", "0,269"]}
        self.assertEqual(sensorial_ai.match_sample_names(numbers, self.model, self.metrics),
                         {"A": "Zinger 0,1%", "B1": "Clash MOD 8", "B2": "Zinger 0,001%"})

    def test_the_suggestion_reads_only_the_report_and_lists_unmatched_names(self):
        create = _FakeCreate(["AMOSTRA: Clash MOD 8 | 9,47; 7,18\nAMOSTRA: Outra | 1,23"])
        suggestion = sensorial_ai.suggest_sample_names(self.model, self.metrics, ai_model="m",
                                                       report=("R.pptx", "Clash: 9,47"), create=create)
        call = create.calls[0]
        self.assertEqual(call["system_prompt"], sensorial_prompts.SAMPLE_NUMBERS_SYSTEM_PROMPT)
        self.assertIn("<relatorio_estudo", call["user_prompt"])
        self.assertNotIn("<desenho>", call["user_prompt"])  # as tabelas do app ficam com o código
        self.assertEqual(suggestion, {"nomes": {"B1": "Clash MOD 8"}, "lidos": ["Clash MOD 8", "Outra"]})

    def test_whole_numbers_the_control_and_a_single_vote_name_nothing(self):
        numbers = {"Neutro": ["5,55", "35"], "Clash": ["9,47"]}
        self.assertEqual(sensorial_ai.match_sample_names(numbers, self.model, self.metrics), {})

    def test_two_names_on_the_same_sample_are_both_dropped(self):
        numbers = {"Clash": ["9,47", "7,18"], "Clash MOD 8": ["9,47", "7,18"]}
        self.assertEqual(sensorial_ai.match_sample_names(numbers, self.model, self.metrics), {})


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
