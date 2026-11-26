"""Consolidação dos subtemas das tags abertas (docs/SPEC_RELATORIO_POR_TEMA.md, §4 e §14)."""

import json
import unittest

from tests.test_prosodia_taxonomia import _BancoDescartavel
from utils import prosodia_db
from utils import prosodia_subtemas as subtemas
from utils.prosodia_subtemas import OUTROS_ASSUNTOS, Detalhe, Grupo
from utils.prosodia_temas import OUTROS, RespostaInvalida, parse_taxonomia

TAXONOMIA = parse_taxonomia(
    "! Alerta Crítico: furto\n"
    "Equipamentos: máquinas e secadoras\n"
    "Atendimento: atendentes e suporte\n"
    "Ambiente > Lotação"
)

EQUIPAMENTOS = [
    Detalhe("máquina travou", 6, "a máquina travou no meio do ciclo"),
    Detalhe("fora de serviço", 4),
    Detalhe("secadora não aquece", 3),
    Detalhe("máquinas novas", 2),
    Detalhe("porta emperrada", 1),
]


def _resposta(*grupos):
    return json.dumps({"grupos": [{"nome": nome, "subtemas": list(s)} for nome, s in grupos]})


class ValidarGruposTests(unittest.TestCase):
    def test_each_subtheme_in_one_group_invented_dropped_and_leftover(self):
        grupos = subtemas.validar_grupos(
            [
                {"nome": "Máquina parada", "subtemas": ["máquina travou", "Fora de Serviço", "inventado"]},
                {"nome": "Secagem", "subtemas": ["secadora não aquece", "máquina travou"]},
            ],
            EQUIPAMENTOS,
        )

        self.assertEqual(
            grupos,
            [
                Grupo("Máquina parada", ("máquina travou", "fora de serviço")),
                Grupo("Secagem", ("secadora não aquece",)),
                Grupo(OUTROS_ASSUNTOS, ("máquinas novas", "porta emperrada")),
            ],
        )

    def test_repeated_group_name_is_merged(self):
        grupos = subtemas.validar_grupos(
            [
                {"nome": "Máquina parada", "subtemas": ["máquina travou"]},
                {"nome": "maquina parada", "subtemas": ["fora de serviço"]},
            ],
            EQUIPAMENTOS[:2],
        )

        self.assertEqual(grupos, [Grupo("Máquina parada", ("máquina travou", "fora de serviço"))])

    def test_groups_are_ordered_by_respondents_and_capped_at_five(self):
        detalhes = [Detalhe("s{}".format(i), i) for i in range(1, 8)]
        grupos = subtemas.validar_grupos([{"nome": "G{}".format(i), "subtemas": ["s{}".format(i)]} for i in range(1, 8)], detalhes)

        self.assertEqual([g.nome for g in grupos], ["G7", "G6", "G5", "G4", "G3", OUTROS_ASSUNTOS])
        self.assertEqual(grupos[-1].subtemas, ("s2", "s1"))

    def test_name_is_cut_to_five_words_and_ia_leftover_group_is_ignored(self):
        grupos = subtemas.validar_grupos(
            [
                {"nome": "Máquinas que param no meio do ciclo", "subtemas": ["máquina travou"]},
                {"nome": "Outros assuntos do tema", "subtemas": ["fora de serviço"]},
            ],
            EQUIPAMENTOS[:2],
        )

        self.assertEqual(grupos[0].nome, "Máquinas que param no meio")
        self.assertEqual(grupos[-1], Grupo(OUTROS_ASSUNTOS, ("fora de serviço",)))

    def test_no_valid_group_is_an_invalid_answer(self):
        with self.assertRaises(RespostaInvalida):
            subtemas.validar_grupos([{"nome": "X", "subtemas": ["não existe"]}], EQUIPAMENTOS)


class DetalhesDoProjetoTests(unittest.TestCase):
    def test_counts_respondents_once_and_keeps_first_spelling_and_example(self):
        def classificacao(*temas):
            return {"temas": [{"subtema": t, "detalhe": d, "evidencia": e} for t, d, e in temas]}

        mencoes = [
            ("r1", classificacao(("Equipamentos", "Máquina travou", "travou no ciclo"))),
            ("r1", classificacao(("Equipamentos", "maquina travou", "de novo"))),
            ("r2", classificacao(("Equipamentos", "máquina travou", ""), ("Equipamentos", "fora de serviço", "aviso"))),
            ("r3", classificacao(("Atendimento", "", "sem subtema"), (OUTROS, "preço", "caro"))),
        ]

        detalhes = subtemas.detalhes_do_projeto(mencoes, ["Equipamentos", "Atendimento"])

        self.assertEqual(
            detalhes,
            {"Equipamentos": [Detalhe("Máquina travou", 2, "travou no ciclo"), Detalhe("fora de serviço", 1, "aviso")]},
        )


class ConsolidarTests(unittest.TestCase):
    def setUp(self):
        self.chamadas = []
        self.salvos = []

    def _responder(self, resposta):
        def responder(sistema, usuario, max_tokens):
            self.chamadas.append(usuario)
            if isinstance(resposta, Exception):
                raise resposta
            return resposta

        return responder

    def _consolidar(self, detalhes, salvos=None, resposta=None, sem_ia=False):
        return subtemas.consolidar_subtemas(
            TAXONOMIA,
            detalhes,
            salvos or {},
            None if sem_ia else self._responder(resposta),
            lambda tag, chave, grupos: self.salvos.append((tag, chave, grupos)),
        )

    def test_tag_with_up_to_three_subthemes_does_not_go_to_the_ai(self):
        resultado = self._consolidar({"Atendimento": [Detalhe("atendente", 3), Detalhe("suporte", 1)]})

        self.assertEqual(self.chamadas, [])
        self.assertEqual(resultado.grupos["Atendimento"], [Grupo("atendente", ("atendente",)), Grupo("suporte", ("suporte",))])

    def test_ai_groups_are_validated_and_saved(self):
        resposta = _resposta(("Máquina parada", ["máquina travou", "fora de serviço"]), ("Secagem", ["secadora não aquece"]))

        resultado = self._consolidar({"Equipamentos": EQUIPAMENTOS}, resposta=resposta)

        self.assertEqual(len(self.chamadas), 1)
        self.assertEqual([g.nome for g in resultado.grupos["Equipamentos"]], ["Máquina parada", "Secagem", OUTROS_ASSUNTOS])
        self.assertEqual(self.salvos[0][0], "Equipamentos")
        self.assertEqual(self.salvos[0][1], subtemas.chave_da_tag("Equipamentos", "máquinas e secadoras", EQUIPAMENTOS))
        self.assertEqual(resultado.chamadas, ["Equipamentos"])
        self.assertEqual(resultado.nome_do_grupo("Equipamentos", "Fora de serviço"), "Máquina parada")
        self.assertEqual(resultado.nome_do_grupo("Equipamentos", "porta emperrada"), OUTROS_ASSUNTOS)
        self.assertEqual(resultado.nome_do_grupo("Equipamentos", ""), OUTROS_ASSUNTOS)

    def test_saved_groups_with_the_same_key_are_reused(self):
        chave = subtemas.chave_da_tag("Equipamentos", "máquinas e secadoras", EQUIPAMENTOS)
        salvos = {"Equipamentos": {"chave": chave, "grupos": [{"nome": "Máquina parada", "subtemas": ["máquina travou"]}]}}
        # Contagens novas não mudam a chave: a tag não volta à IA.
        com_mais_respondentes = [Detalhe(d.nome, d.respondentes + 5, d.exemplo) for d in EQUIPAMENTOS]

        resultado = self._consolidar({"Equipamentos": com_mais_respondentes}, salvos=salvos, resposta="{}")

        self.assertEqual(self.chamadas, [])
        self.assertEqual(resultado.grupos["Equipamentos"][0], Grupo("Máquina parada", ("máquina travou",)))

    def test_new_subtheme_changes_the_key_and_the_ai_gets_the_old_groups(self):
        antiga = subtemas.chave_da_tag("Equipamentos", "máquinas e secadoras", EQUIPAMENTOS[:4])
        salvos = {"Equipamentos": {"chave": antiga, "grupos": [{"nome": "Máquina parada", "subtemas": ["máquina travou"]}]}}
        resposta = _resposta(("Máquina parada", ["máquina travou", "fora de serviço", "porta emperrada"]))

        self._consolidar({"Equipamentos": EQUIPAMENTOS}, salvos=salvos, resposta=resposta)

        self.assertEqual(len(self.chamadas), 1)
        self.assertIn("Máquina parada: máquina travou", self.chamadas[0])

    def test_definition_change_changes_the_key(self):
        outra = parse_taxonomia("Equipamentos: máquinas")
        self.assertNotEqual(
            subtemas.chave_da_tag("Equipamentos", "máquinas e secadoras", EQUIPAMENTOS),
            subtemas.chave_da_tag("Equipamentos", outra.subtemas[0].definicao, EQUIPAMENTOS),
        )

    def test_ai_failure_falls_back_to_raw_subthemes_with_a_warning(self):
        resultado = self._consolidar({"Equipamentos": EQUIPAMENTOS * 2}, resposta=RuntimeError("timeout"))

        grupos = resultado.grupos["Equipamentos"]
        self.assertEqual(len(grupos), 6)
        self.assertEqual(grupos[-1].nome, OUTROS_ASSUNTOS)
        self.assertEqual(self.salvos, [])
        self.assertEqual(len(resultado.problemas), 1)
        self.assertIn("timeout", resultado.problemas[0])

    def test_unreadable_answer_is_a_failure(self):
        resultado = self._consolidar({"Equipamentos": EQUIPAMENTOS}, resposta="não sei")

        self.assertEqual(len(resultado.problemas), 1)
        self.assertEqual(resultado.grupos["Equipamentos"], subtemas.grupos_de_reserva(EQUIPAMENTOS))

    def test_without_responder_the_tag_uses_the_fallback(self):
        resultado = self._consolidar({"Equipamentos": EQUIPAMENTOS}, sem_ia=True)

        self.assertEqual(resultado.grupos["Equipamentos"], subtemas.grupos_de_reserva(EQUIPAMENTOS))
        self.assertEqual(len(resultado.problemas), 1)

    def test_alert_tag_outros_and_fixed_lines_are_not_consolidated(self):
        muitos = [Detalhe("x{}".format(i), 1) for i in range(5)]

        resultado = self._consolidar(
            {"Alerta Crítico": muitos, OUTROS: muitos, "Ambiente > Lotação": muitos}, resposta="{}"
        )

        self.assertEqual(self.chamadas, [])
        self.assertEqual(resultado.grupos, {})

    def test_message_carries_the_definition_counts_and_examples(self):
        mensagem = subtemas.mensagem_da_consolidacao("Equipamentos", "máquinas e secadoras", EQUIPAMENTOS)

        self.assertIn("Equipamentos: máquinas e secadoras", mensagem)
        self.assertIn("- máquina travou (6 respondentes): a máquina travou no meio do ciclo", mensagem)
        self.assertIn("- porta emperrada (1 respondente)", mensagem)
        self.assertIn("Nenhum.", mensagem)


class SubtemaGruposPersistenceTests(_BancoDescartavel):
    def setUp(self):
        super().setUp()
        self.project_id = prosodia_db.create_project("Lava Já")

    def test_saves_reads_and_replaces_by_tag(self):
        grupos = [{"nome": "Máquina parada", "subtemas": ["máquina travou"]}]
        prosodia_db.save_subtema_grupos(self.project_id, "Equipamentos", "chave-1", grupos, "gpt-4.1")
        prosodia_db.save_subtema_grupos(self.project_id, "Equipamentos", "chave-2", grupos[:0], "gpt-4.1")
        prosodia_db.save_subtema_grupos(self.project_id, "Atendimento", "chave-3", grupos, "")

        salvos = prosodia_db.get_subtema_grupos(self.project_id)

        self.assertEqual(sorted(salvos), ["Atendimento", "Equipamentos"])
        self.assertEqual((salvos["Equipamentos"]["chave"], salvos["Equipamentos"]["grupos"]), ("chave-2", []))
        self.assertEqual(salvos["Atendimento"]["grupos"], grupos)

    def test_unreadable_json_is_left_out(self):
        prosodia_db.save_subtema_grupos(self.project_id, "Equipamentos", "chave-1", [], "")
        with prosodia_db._connect() as conn:
            conn.execute("UPDATE subtema_grupos SET grupos_json = '{quebrado'")

        self.assertEqual(prosodia_db.get_subtema_grupos(self.project_id), {})

    def test_project_analysis_keeps_the_report_json(self):
        relatorio = {"versao": 2, "capitulos": [{"id": "c1", "tema": "Equipamentos"}]}
        com = prosodia_db.save_project_analysis(self.project_id, "gpt-4.1", "texto", relatorio=relatorio)
        sem = prosodia_db.save_project_analysis(self.project_id, "gpt-4.1", "texto v2")

        analises = {a["id"]: a for a in prosodia_db.get_project_analyses(self.project_id)}

        self.assertEqual(analises[com]["relatorio"], relatorio)
        self.assertIsNone(analises[sem]["relatorio"])
        self.assertNotIn("relatorio_json", analises[com])
        self.assertEqual(prosodia_db.get_latest_project_analysis(self.project_id)["id"], sem)


if __name__ == "__main__":
    unittest.main()
