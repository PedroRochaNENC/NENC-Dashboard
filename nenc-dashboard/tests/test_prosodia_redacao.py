"""Redação do relatório da Análise Geral (Fases 4 e 5 da spec de Unidades e tags).

O sinal emocional em frase de negócio, a seção "Como ler esta análise",
escrita pelo código, e a regra de que só tema em padrão sustenta
recomendação. Da validação com a Smart Fit (Fase 5): o que vai para a IA
sem a leitura técnica da voz, o aviso de voz sem destaque, a verificação de
"vocal" e de superlativo sem número, e Outros na Matriz. A montagem e a
verificação do texto da IA estão em test_prosodia_montagem.py.
"""

import dataclasses
import unittest

import pandas as pd

from tests import test_prosodia_tags as tags
from tests.test_prosodia_montagem import _exemplo, _trocar, _verificar
from tests.test_prosodia_relatorio import (
    ATENDENTE,
    LOTACAO,
    MAQUINA,
    _agregar,
    _audio,
    _linhas,
    _tema,
    _vad,
)
from tests.test_prosodia_signals import BASE, sincronizado
from utils import prosodia_relatorio as rel
from utils import prosodia_signals as sinais
from utils.prosodia_project_types import PESQUISA_OPINIAO
from utils.prosodia_temas import OUTROS
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


# ---------------------------------------------------------------------------
# Validação com a Smart Fit (Fase 5)
# ---------------------------------------------------------------------------

class VozForaDaMensagemTests(unittest.TestCase):
    """Na validação, a IA leu "Sem destaque" como "satisfação funcional, sem
    encantamento vocal": a leitura vinha dos Sumários Executivos e da tabela de
    divergências, que chegavam com valência, ativação e "habitual"."""

    def test_the_summary_loses_the_sentences_that_read_the_voice(self):
        texto = (
            "O aluno elogia a recepção e a energia da academia. A prosódia indica baixa ativação e valência "
            "neutra, sugerindo satisfação funcional. O tom de voz é contido!\nRecomenda manter a equipe."
        )

        self.assertEqual(
            rel.sem_leitura_da_voz(texto),
            "O aluno elogia a recepção e a energia da academia.\nRecomenda manter a equipe.",
        )

    def test_a_summary_that_only_read_the_voice_stays_out(self):
        audios = [_audio(1, _tema(MAQUINA)), _audio(2, _tema(MAQUINA))]
        agregacao = _agregar(audios)
        analises = {
            1: "## Sumário Executivo\nMáquina parada. A fala tem energia emocional baixa, sem entusiasmo vocal.",
            2: "## Sumário Executivo\nA valência vocal fica abaixo do habitual.",
        }

        sumarios = rel.sumarios_individuais(agregacao, analises)

        self.assertEqual(sumarios.texto, "### Áudio 01\nMáquina parada.")
        self.assertEqual((sumarios.com, sumarios.sem), (2, 0))

    def test_divergences_reach_the_ai_without_the_voice_numbers(self):
        divergencias = sinais.detectar_divergencias(sincronizado(BASE + [-0.6], [0.0] * 9 + [0.8]))

        para_ia = sinais.divergencias_texto(divergencias, com_voz=False)
        apendice = sinais.divergencias_texto(divergencias)

        self.assertIn("| Tempo | Locutor | Fala | Texto (nota) | Leitura |", para_ia)
        self.assertIn("texto positivo × voz negativa", para_ia)
        for termo in ("valência", "habitual", "(z ", "_z:"):
            with self.subTest(termo=termo):
                self.assertNotIn(termo, para_ia)
        self.assertIn("Voz (valência, z)", apendice)
        self.assertNotIn("valência", sinais.divergencias_texto(pd.DataFrame(), com_voz=False))


class VozSemDestaqueTests(unittest.TestCase):
    def test_without_a_highlighted_signal_the_task_says_to_leave_the_voice_out(self):
        agregacao = _agregar([_audio(i, _tema(MAQUINA)) for i in range(1, 7)])

        tarefa = rel.mensagem_para_ia(agregacao, {}, "cabeçalho").split("## Tarefa", 1)[1]

        self.assertTrue(rel.voz_sem_destaque(agregacao))
        self.assertIn("Nenhum achado tem sinal emocional com destaque", tarefa)
        self.assertIn("não comente a voz no Resumo, nas Hipóteses, em Para acompanhar e investigar nem no "
                      "Termômetro", tarefa)

    def test_one_highlighted_signal_is_enough_to_talk_about_the_voice(self):
        agregacao = _exemplo()

        self.assertFalse(rel.voz_sem_destaque(agregacao))
        self.assertNotIn("Nenhum achado tem sinal emocional", rel.mensagem_para_ia(agregacao, {}, "cabeçalho"))
        self.assertFalse(rel.voz_sem_destaque(_agregar([_audio(1, _tema(MAQUINA))])))


class VerificacaoDaRedacaoTests(unittest.TestCase):
    def test_vocal_and_outside_the_habitual_are_technical_terms(self):
        texto = _trocar(
            "(A1, 19/64) e a única dita com irritação perceptível na voz.",
            "(A1, 19/64), sem encantamento vocal fora do habitual.",
        )

        self.assertEqual(_verificar(texto), [
            'Resumo Executivo usa "fora do habitual", termo técnico da voz, que fica no apêndice.',
            'Resumo Executivo usa "vocal", termo técnico da voz, que fica no apêndice.',
        ])

    def test_a_superlative_needs_a_number(self):
        sem_numero = _trocar("O encantamento pode depender de pessoas", "O atendente pode ser o principal diferencial")
        com_numero = _trocar(
            "O encantamento pode depender de pessoas, não de processo:",
            "O atendente pode ser o principal diferencial (A2, 13/64), e o encantamento depende de pessoas:",
        )

        self.assertEqual(_verificar(sem_numero), [
            'Hipóteses usa "principal diferencial" sem número: a ordem dos temas vem da fração da tabela.',
        ])
        self.assertEqual(_verificar(com_numero), [])


class OutrosNaMatrizTests(unittest.TestCase):
    def test_an_audio_that_only_talks_about_others_shows_it(self):
        audios = [tags._audio(i, tags._tema("Staff", "Recepção", "positiva", "Elogia a recepção.")) for i in range(1, 12)]
        audios.append(tags._audio(12, tags._tema(OUTROS, "Preço da mensalidade", "positiva", "Acha a mensalidade justa.")))
        vad = []
        for audio in audios:
            for linha in _vad(audio.session_id, range(4), valencia=0.02 * audio.audio_id):
                vad.append(dict(linha, sentimento_texto=0.8))

        agregacao = rel.agregar(audios, [a.audio_id for a in audios], tags.TAXONOMIA, PESQUISA_OPINIAO,
                                sinc_projeto=pd.DataFrame(vad))

        self.assertTrue(agregacao.matriz.avaliavel)
        linha = next(l for l in agregacao.matriz.vinculo.linhas if l.audio_id == 12)
        self.assertEqual(linha.temas, (OUTROS,))
        self.assertIn("| Áudio 12 | Outros | Acha a mensalidade justa. |", rel.matriz_markdown(agregacao))


if __name__ == "__main__":
    unittest.main()
