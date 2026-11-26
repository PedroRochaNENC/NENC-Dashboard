"""Máscara de nomes e dados pessoais (docs/SPEC_RELATORIO_POR_TEMA.md, §6 e §14)."""

import unittest
from unittest import mock

from utils import prosodia_mascara as mascara
from utils.prosodia_mascara import (
    BANCO,
    DADO_PESSOAL,
    FUNCIONARIO,
    PESSOA,
    VALOR,
    mascarar_fala,
    preservar_do_projeto,
)

PRESERVAR = preservar_do_projeto(
    "Lava Já — Voz do Cliente",
    ["Moema · LJ-MOE-01", "LJ-CEN-02 Centro Histórico", "Caxias Shopping"],
    ["Atendimento", "Pagamento > Estorno", "Limpeza"],
)


class DadosTests(unittest.TestCase):
    def test_money_in_digits_and_in_words(self):
        self.assertEqual(mascarar_fala("Paguei R$ 129,90 e mais R$45.", nomes=False), "Paguei {} e mais {}.".format(VALOR, VALOR))
        self.assertEqual(mascarar_fala("Cobraram duzentos e cinquenta reais.", nomes=False), "Cobraram {}.".format(VALOR))
        self.assertEqual(mascarar_fala("Foram 99 reais por mês.", nomes=False), "Foram {} por mês.".format(VALOR))

    def test_cpf_phone_card_and_email(self):
        texto = "CPF 123.456.789-09, fone (21) 99876-5432, cartão 4111 1111 1111 1111, ana@exemplo.com"
        mascarado = mascarar_fala(texto, nomes=False)
        for dado in ("123.456", "99876", "4111", "ana@"):
            self.assertNotIn(dado, mascarado)
        self.assertEqual(mascarado.count(DADO_PESSOAL), 4)

    def test_bank_names(self):
        self.assertEqual(
            mascarar_fala("O débito veio do Itaú e não do Banco do Brasil.", nomes=False),
            "O débito veio do {} e não do {}.".format(BANCO, BANCO),
        )

    def test_times_and_counts_stay(self):
        texto = "Cheguei às 18:30, tinha 20 pessoas e esperei 15 minutos."
        self.assertEqual(mascarar_fala(texto, nomes=False), texto)


class NomesTests(unittest.TestCase):
    def test_staff_name_after_a_role(self):
        self.assertEqual(
            mascarar_fala("O gerente Marcos não resolveu nada.", PRESERVAR),
            "O gerente {} não resolveu nada.".format(FUNCIONARIO),
        )

    def test_full_name_is_one_mark(self):
        mascarado = mascarar_fala("A atendente Ana Paula da Silva foi grossa.", PRESERVAR)
        self.assertEqual(mascarado, "A atendente {} foi grossa.".format(FUNCIONARIO))

    def test_name_after_an_article_without_role_is_a_person(self):
        self.assertEqual(mascarar_fala("Minha esposa Joana reclamou.", PRESERVAR), "Minha esposa {} reclamou.".format(PESSOA))

    def test_brand_units_and_labels_are_preserved(self):
        texto = "No Lava Já do Caxias Shopping e em Moema o Atendimento é bom, LavaJá nota dez."
        self.assertEqual(mascarar_fala(texto, PRESERVAR), texto)

    def test_unit_word_alone_is_preserved(self):
        texto = "Aqui no Centro Histórico a máquina quebrou de novo."
        self.assertEqual(mascarar_fala(texto, PRESERVAR), texto)

    def test_praise_keeps_the_name_and_loses_the_money(self):
        self.assertEqual(
            mascarar_fala("A Natália é ótima, paguei 99 reais.", PRESERVAR, nomes=False),
            "A Natália é ótima, paguei {}.".format(VALOR),
        )

    def test_common_capitalized_words_stay(self):
        texto = "Gostei muito. Tudo limpo e o Wi-Fi funciona."
        self.assertEqual(mascarar_fala(texto, PRESERVAR), texto)

    def test_without_the_model_capitalized_words_are_masked(self):
        with mock.patch.object(mascara, "_modelo", return_value=None):
            mascarado = mascarar_fala("Fui atendido pela Bruna e pelo Caio no Lava Já.", PRESERVAR)
        self.assertNotIn("Bruna", mascarado)
        self.assertNotIn("Caio", mascarado)
        self.assertIn("Lava Já", mascarado)

    def test_empty_text(self):
        self.assertEqual(mascarar_fala(None), "")
        self.assertEqual(mascarar_fala("   "), "   ")


@unittest.skipUnless(mascara.modelo_disponivel(), "pt_core_news_sm não instalado")
class ModeloTests(unittest.TestCase):
    def test_name_the_model_finds_without_a_trigger(self):
        mascarado = mascarar_fala("Gostaria de reclamar da Fernanda, que me tratou mal.", PRESERVAR)
        self.assertNotIn("Fernanda", mascarado)


class PreservarTests(unittest.TestCase):
    def test_unit_code_is_left_out_and_words_go_in(self):
        self.assertIn("Centro Histórico", PRESERVAR)
        self.assertIn("Histórico", PRESERVAR)
        self.assertNotIn("LJ-CEN-02", PRESERVAR)
        self.assertNotIn("LJ-MOE-01", PRESERVAR)

    def test_brand_without_space(self):
        self.assertIn("LavaJá", PRESERVAR)
        self.assertIn("Lava Já", PRESERVAR)

    def test_taxonomy_parts(self):
        self.assertIn("Pagamento", PRESERVAR)
        self.assertIn("Estorno", PRESERVAR)


if __name__ == "__main__":
    unittest.main()
