"""Redação do relatório da Análise Geral (Fase 4 da spec de Unidades e tags).

O sinal emocional em frase de negócio, a seção "Como ler esta análise",
escrita pelo código, e a regra de que só tema em padrão sustenta
recomendação. A montagem e a verificação do texto da IA estão em
test_prosodia_montagem.py.
"""

import dataclasses
import unittest

from tests.test_prosodia_relatorio import ATENDENTE, LOTACAO, MAQUINA, _agregar, _audio, _linhas, _tema
from utils import prosodia_relatorio as rel
from utils.prosodia_relatorio import (
    ABAIXO,
    ACIMA,
    MISTO_OU_NEUTRO,
    NEGATIVO,
    NO_HABITUAL,
    POSITIVO,
    SEM_DESTAQUE,
    SEM_VOZ,
    LeituraVoz,
)


def _voz(direcao, nivel, valencia=0.0):
    return LeituraVoz(8, valencia, 0.0, direcao, nivel)


class SinalEmocionalTests(unittest.TestCase):
    def test_few_respondents_with_voice(self):
        for polaridade in (POSITIVO, NEGATIVO, MISTO_OU_NEUTRO):
            with self.subTest(polaridade=polaridade):
                self.assertEqual(rel.sinal_emocional(LeituraVoz(3), polaridade), SEM_VOZ)

    def test_the_voice_of_the_project_itself_has_no_highlight(self):
        # Na validação com a Smart Fit, essa voz, a da maioria dos temas,
        # virou "cortesia protocolar" no relatório inteiro.
        for polaridade in (POSITIVO, NEGATIVO, MISTO_OU_NEUTRO):
            for nivel in ("média", ""):
                with self.subTest(polaridade=polaridade, nivel=nivel):
                    self.assertEqual(rel.sinal_emocional(_voz(NO_HABITUAL, nivel), polaridade), SEM_DESTAQUE)

    def test_phrases_of_the_feedback(self):
        casos = [
            (_voz(NO_HABITUAL, "baixa"), NEGATIVO, "Crítica dita com resignação, sem irritação na voz"),
            (_voz(NO_HABITUAL, "baixa"), POSITIVO, "Elogio dito sem entusiasmo"),
            (_voz(ABAIXO, "alta", -0.3), NEGATIVO, "Irritação perceptível na voz"),
            (_voz(ABAIXO, "média", -0.3), NEGATIVO, "Tom de voz mais negativo que a média; confirma a crítica"),
            (_voz(ACIMA, "alta", 0.3), POSITIVO, "Fala mais animada que a média; confirma o elogio"),
            (_voz(ABAIXO, "média", -0.3), POSITIVO, "Tom de voz menos positivo que as palavras"),
            (_voz(ACIMA, "baixa", 0.3), NEGATIVO, "Tom de voz mais leve que a crítica"),
        ]
        for voz, polaridade, frase in casos:
            with self.subTest(frase=frase):
                self.assertEqual(rel.sinal_emocional(voz, polaridade), frase)

    def test_no_phrase_uses_a_technical_term(self):
        frases = {SEM_DESTAQUE, SEM_VOZ, *rel._SINAIS.values(), *rel._SINAIS_NO_HABITUAL.values()}

        for frase in frases:
            with self.subTest(frase=frase):
                self.assertIsNone(rel._TERMO_TECNICO_DE_VOZ.search(rel._normal(frase)))

    def test_the_row_uses_the_predominant_polarity(self):
        agregacao = _agregar([_audio(i, _tema(MAQUINA)) for i in range(1, 6)])
        tema = dataclasses.replace(agregacao.temas[0], voz=_voz(NO_HABITUAL, "baixa"))

        self.assertEqual(tema.sinal, "Crítica dita com resignação, sem irritação na voz")
        tema = dataclasses.replace(tema, polaridades={"negativa": 2, "positiva": 2})
        self.assertEqual(tema.sinal, "Fala com menos energia que a média")


def _itens(texto):
    return texto.splitlines()


class ComoLerTests(unittest.TestCase):
    def test_every_item_says_what_we_saw_and_what_it_means(self):
        texto = rel.como_ler_markdown(_agregar([_audio(1, _tema(MAQUINA)), _audio(2)]))

        for linha in _itens(texto):
            with self.subTest(linha=linha[:40]):
                self.assertTrue(linha.startswith("- **O que observamos:** "))
                self.assertIn(" **O que isso significa:** ", linha)
        self.assertTrue(_itens(texto)[0].startswith(
            "- **O que observamos:** 2 respondentes, numa coleta aberta e espontânea."
        ))

    def test_a_small_base_has_no_recommendations(self):
        pequena = rel.como_ler_markdown(_agregar([_audio(i, _tema(MAQUINA)) for i in range(1, 13)]))
        grande = rel.como_ler_markdown(_agregar([_audio(i, _tema(MAQUINA)) for i in range(1, 26)]))

        self.assertIn("A base ainda é pequena, abaixo de 20 respondentes.", pequena)
        self.assertIn("Por isso esta análise não traz recomendações de ação.", pequena)
        self.assertNotIn("A base ainda é pequena", grande)

    def test_short_reports(self):
        curtos = [_audio(i, _tema(MAQUINA)) for i in range(1, 4)]
        longos = [dataclasses.replace(audio, duracao_s=75.0) for audio in curtos]

        self.assertIn("Os relatos são curtos (mediana de 12 s).", rel.como_ler_markdown(_agregar(curtos)))
        self.assertNotIn("relatos são curtos", rel.como_ler_markdown(_agregar(longos)))

    def test_without_voice_the_themes_stand_on_what_was_said(self):
        texto = rel.como_ler_markdown(_agregar([_audio(i, _tema(MAQUINA)) for i in range(1, 4)]))

        self.assertIn("Nenhum tema teve relatos com voz suficientes para uma leitura (5 ou mais respondentes).", texto)

    def test_voice_that_barely_varies(self):
        audios = [_audio(i, _tema(MAQUINA)) for i in range(1, 6)] + [_audio(i, _tema(ATENDENTE)) for i in range(6, 11)]
        agregacao = _agregar(audios)
        agregacao.temas = [dataclasses.replace(tema, voz=_voz(NO_HABITUAL, "média")) for tema in agregacao.temas]

        texto = rel.como_ler_markdown(agregacao)

        self.assertIn("O tom de voz varia pouco de um tema para outro.", texto)
        self.assertNotIn("Nenhum tema teve relatos com voz", texto)
        agregacao.temas = [dataclasses.replace(tema, voz=_voz(ABAIXO, "alta", -0.3)) for tema in agregacao.temas]
        self.assertNotIn("varia pouco", rel.como_ler_markdown(agregacao))

    def test_themes_without_mentions_are_not_irrelevant(self):
        texto = rel.como_ler_markdown(_agregar([_audio(1, _tema(MAQUINA)), _audio(2, _tema(LOTACAO))]))

        self.assertIn("2 dos 4 temas da taxonomia não foram citados.", texto)
        self.assertIn("Numa coleta aberta, a ausência de menções não indica que o tema seja irrelevante.", texto)

    def test_audios_left_out_of_the_count(self):
        texto = rel.como_ler_markdown(_agregar([_audio(1, _tema(MAQUINA)), _audio(2, linhas=())]))

        self.assertIn("1 dos 2 áudios recebidos ficaram fora da contagem", texto)

    def test_taxonomy_status(self):
        agregacao = _agregar([_audio(1, _tema(MAQUINA))])

        validada = rel.como_ler_markdown(agregacao, "validada")
        self.assertNotIn("não foi validada", validada)
        self.assertNotIn("propostos pela IA", validada)
        self.assertIn("A taxonomia ainda não foi validada com a cliente.", rel.como_ler_markdown(agregacao, "provisoria"))
        self.assertIn("Os temas foram propostos pela IA.", rel.como_ler_markdown(agregacao, None, taxonomia_da_ia=True))


class RecomendacaoTests(unittest.TestCase):
    def test_only_a_pattern_sustains_a_recommendation(self):
        audios = [_audio(i, _tema(MAQUINA)) for i in range(1, 6)]
        audios += [_audio(i, _tema(ATENDENTE, "positiva")) for i in range(6, 9)]
        audios += [_audio(i) for i in range(9, 41)]
        agregacao = _agregar(audios)

        maquina, atendente = agregacao.temas[0], agregacao.temas[1]
        self.assertEqual((maquina.faixa, atendente.faixa), (rel.PADRAO, rel.SINAL))
        self.assertTrue(rel.pode_recomendar(agregacao, maquina))
        self.assertFalse(rel.pode_recomendar(agregacao, atendente))
        self.assertEqual(rel.achados_a_acompanhar(agregacao), ["A2"])

    def test_the_exploratory_reading_follows_up_everything(self):
        audios = [_audio(i, _tema(MAQUINA)) for i in range(1, 7)] + [_audio(i) for i in range(7, 13)]
        agregacao = _agregar(audios)

        self.assertTrue(agregacao.exploratoria)
        self.assertEqual(agregacao.temas[0].faixa, rel.PADRAO)
        self.assertFalse(rel.pode_recomendar(agregacao, agregacao.temas[0]))
        self.assertEqual(rel.achados_a_acompanhar(agregacao), ["A1"])


if __name__ == "__main__":
    unittest.main()
