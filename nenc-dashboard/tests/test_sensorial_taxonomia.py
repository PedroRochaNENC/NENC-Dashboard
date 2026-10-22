"""Taxonomia de temas do Teste Sensorial: leitura, escrita e proposta da IA."""

import unittest

from tests.test_jornada_ai import _FakeCreate
from utils import sensorial_taxonomia as tax

TEXTO = """\
Benefício > Energia física: Energia; Aumenta sua energia ;Revigorante; revigorante
- beneficio > Mente e foco: Recarrega sua mente
# comentário
Experiência > Primeira impressão: Olfação
Outro > Tema: x
linha solta
"""


class ParseTests(unittest.TestCase):
    def test_reads_both_axes_and_keeps_what_it_ignored(self):
        parsed = tax.parse(TEXTO)
        self.assertEqual([t.nome for t in parsed.do_eixo(tax.BENEFICIO)], ["Energia física", "Mente e foco"])
        self.assertEqual(parsed.do_eixo(tax.BENEFICIO)[0].itens, ("Energia", "Aumenta sua energia", "Revigorante"))
        self.assertEqual(parsed.do_eixo(tax.EXPERIENCIA)[0].itens, ("Olfação",))
        self.assertEqual(parsed.ignoradas, ("Outro > Tema: x", "linha solta"))
        self.assertFalse(parsed.vazia)

    def test_without_a_benefit_theme_it_is_empty(self):
        self.assertTrue(tax.parse("").vazia)
        self.assertTrue(tax.parse("Experiência > Momento: Olfação").vazia)

    def test_formatting_round_trips_with_benefits_first(self):
        text = tax.formatar(tax.parse("Experiência > Depois: Pós\nBenefício > Foco: Mais focado; Mente"))
        self.assertEqual(text, "Benefício > Foco: Mais focado; Mente\nExperiência > Depois: Pós")
        self.assertEqual(tax.formatar(tax.parse(text)), text)

    def test_claims_without_a_theme_ignore_accents_and_case(self):
        parsed = tax.parse("Benefício > Energia: energia; Revigorante")
        self.assertEqual(tax.claims_sem_tema(parsed, ["Energia", "Revigorante", "Determinação", "determinacao"]),
                         ("Determinação",))


class ProposalTests(unittest.TestCase):
    def test_the_proposal_goes_without_the_base_and_caps_the_report(self):
        create = _FakeCreate(["Benefício > Energia: Energia\nComentário solto"])
        proposal = tax.propor(create, "m", {"Amostra A": ["Energia"]}, ["Olfação", "Pós-olfação"],
                              "r" * (tax.MAX_REPORT_CHARS_FOR_PROPOSAL + 10), "Projeto: 1055")
        call = create.calls[0]
        self.assertIsNone(call["vector_store_id"])
        self.assertIn("- Amostra A: Energia", call["user_prompt"])
        self.assertIn("[relatório cortado]", call["user_prompt"])
        self.assertEqual(proposal.ignoradas, ())
        self.assertEqual(tax.formatar(proposal), "Benefício > Energia: Energia")

    def test_a_claim_in_two_proposed_themes_stays_in_the_first(self):
        create = _FakeCreate(["Benefício > Energia: Energia; Recarrega\nBenefício > Mente: recarrega\n"
                              "Benefício > Foco: Focado; Recarrega"])
        self.assertEqual(tax.formatar(tax.propor(create, "m", {}, [])),
                         "Benefício > Energia: Energia; Recarrega\nBenefício > Foco: Focado")

    def test_an_answer_without_benefits_is_no_proposal(self):
        self.assertTrue(tax.propor(_FakeCreate(["Experiência > X: Olfação"]), "m", {}, []).vazia)


if __name__ == "__main__":
    unittest.main()
