"""Resumo por tema da Análise Geral (pedido do Pedro em 11/11/2026).

Para cada tema em padrão, um bloco logo depois da Taxonomia de Temas: o
cabeçalho e o Termômetro do tema, calculados pelo código, e três linhas da
IA (achado, hipótese e recomendação; na leitura exploratória, o que
acompanhar e investigar no lugar da recomendação).
"""

import unittest

import pandas as pd

from tests import test_prosodia_tags as tags
from tests.test_prosodia_montagem import TEXTO_DO_PROJETO_PEQUENO, _exemplo, _projeto_pequeno, _trocar, _verificar
from tests.test_prosodia_relatorio import MAQUINA, _agregar
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel
from utils.prosodia_project_types import PESQUISA_OPINIAO
from utils.prosodia_relatorio import PADRAO, LeituraTrecho, Termometro, Trecho

BLOCO_A4 = (
    "**A4 · Ambiente > Limpeza da loja**\n"
    "- *Achado:* a limpeza é elogiada em frases curtas, sem detalhe.\n"
    "- *Hipótese:* item de higiene, elogiado sem entusiasmo, ao contrário do atendente (H3).\n"
    "- *Recomendação:* nenhuma recomendação responde a este tema."
)


def _montar(agregacao, texto=prompts.EXEMPLO_ANALISE_GERAL):
    return rel.montar_relatorio(agregacao, "cabeçalho", rel.separar_secoes(texto), "apêndice")


def _secao_resumo(relatorio):
    return relatorio.split("### 2. Resumo por tema\n\n", 1)[1].split("\n\n### ", 1)[0]


def _ocorrencia(respondente, audio_id, linha, nota):
    trecho = Trecho(audio_id, "Áudio {:02d}".format(audio_id), linha, 0.0, "fala", "positiva", nota)
    return respondente, trecho, LeituraTrecho(nota_texto=nota)


class TermometroDoTemaTests(unittest.TestCase):
    def test_each_respondent_weighs_the_same_and_a_repeated_excerpt_counts_once(self):
        termometro = rel.termometro_do_tema([
            _ocorrencia("a", 1, 2, 0.8),
            _ocorrencia("a", 1, 3, 0.4),
            # O mesmo trecho classificado em dois subtemas da tag.
            _ocorrencia("a", 1, 3, 0.4),
            _ocorrencia("b", 2, 2, -0.5),
            _ocorrencia("c", 3, 2, None),
        ])

        # a: (0,8 + 0,4) / 2 = 0,6; b: -0,5; c fica de fora, sem nota.
        self.assertEqual(termometro, Termometro(2, 0.05, 0, 1, 0, 1))

    def test_without_notes_it_is_not_assessable(self):
        self.assertFalse(rel.termometro_do_tema([_ocorrencia("a", 1, 2, None)]).avaliavel)

    def test_the_aggregation_reads_only_the_excerpts_of_each_theme(self):
        audios = [tags._audio(1, tags._tema("Staff", "Recepção", "positiva"), tags._tema("Staff", "Professor", "positiva"))]
        audios += [tags._audio(i, tags._tema("Staff", "Recepção", "positiva")) for i in range(2, 7)]
        audios += [tags._audio(i, tags._tema("Limpeza")) for i in (7, 8)]
        audios.append(tags._audio(9, tags._tema("Alerta Crítico", evidencia="relata assédio")))
        notas = {1: 0.6, 2: 0.6, 3: 0.6, 4: 0.6, 5: -0.5, 7: -0.8, 8: -0.6, 9: -0.9}
        transcricao = pd.DataFrame([
            {"session_id": audio.session_id, "segmento_idx": 1, "start_s": 4.0, "end_s": 7.5, "Text": "Fala.",
             "sentimento_texto": notas.get(audio.audio_id)}
            for audio in audios
        ])

        agregacao = rel.agregar(audios, [a.audio_id for a in audios], tags.TAXONOMIA, PESQUISA_OPINIAO,
                                transcricao_projeto=transcricao)

        # Staff: 0,6 × 4 e -0,5 (o áudio 6 não tem nota) → 0,38 × 5 = 1,9 → +2.
        self.assertEqual(agregacao.termometro_por_tema["Staff"], Termometro(5, 0.38, 2, 4, 0, 1))
        self.assertEqual(agregacao.termometro_por_tema["Limpeza"], Termometro(2, -0.7, -4, 0, 0, 2))
        self.assertNotIn("Alerta Crítico", agregacao.termometro_por_tema)
        staff = next(tema for tema in agregacao.temas if tema.subtema == "Staff")
        self.assertEqual(
            rel.termometro_do_tema_markdown(agregacao, staff),
            "**Termômetro do tema:** **+2** (de -5 a +5) · 4 respondentes com relato positivo, 0 neutros e "
            "1 negativo. 5 dos 6 respondentes do tema têm nota do texto.",
        )


class MontagemDoResumoTests(unittest.TestCase):
    def setUp(self):
        self.agregacao = _exemplo()
        self.agregacao.termometro_por_tema = {MAQUINA: Termometro(17, -0.62, -3, 1, 2, 14)}

    def test_one_block_per_pattern_right_after_the_taxonomy(self):
        resumo = _secao_resumo(_montar(self.agregacao))

        self.assertTrue(resumo.startswith(
            "_Um bloco por tema em padrão, na ordem da tabela: o achado, a hipótese e a recomendação, em uma frase "
            "cada, e o Termômetro só com o que foi dito sobre o tema."
        ))
        cabecalhos = [linha for linha in resumo.splitlines() if linha.startswith("#### ")]
        self.assertEqual(cabecalhos, [
            "#### A1 · Equipamentos > Máquina fora de serviço · padrão, 19/64",
            "#### A2 · Atendimento > Atendente da unidade · padrão, 13/64",
            "#### A3 · Pagamento > Falha no app de pagamento · padrão, 11/64",
            "#### A4 · Ambiente > Limpeza da loja · padrão, 9/64",
        ])
        self.assertIn(
            "#### A1 · Equipamentos > Máquina fora de serviço · padrão, 19/64\n\n"
            "**Termômetro do tema:** **-3** (de -5 a +5) · 1 respondente com relato positivo, 2 neutros e "
            "14 negativos. 17 dos 19 respondentes do tema têm nota do texto.\n\n"
            "- *Achado:* a máquina parada é quase sempre criticada, com ciclo pago e perdido.\n"
            "- *Hipótese:* é a dor com mais chance de virar reclamação pública (H2).\n"
            "- *Recomendação:* mostrar o status da máquina antes do pagamento e estornar o ciclo interrompido (R1).",
            resumo,
        )
        self.assertIn(
            "#### A2 · Atendimento > Atendente da unidade · padrão, 13/64\n\n**Termômetro do tema:** não avaliável",
            resumo,
        )
        # O cabeçalho que a IA escreveu não entra: o do bloco é do código.
        self.assertNotIn("**A1 ·", resumo)
        self.assertNotIn("Espera por máquina livre", resumo)

    def test_the_appendix_shows_the_math_of_each_theme(self):
        conta = rel.termometro_por_tema_conta_markdown(self.agregacao)

        self.assertIn(
            "- A1 · Equipamentos > Máquina fora de serviço: -0,62 × 5 = -3,1, arredondado para -3 "
            "(17 respondentes com nota do texto).",
            conta,
        )
        self.assertIn("- A4 · Ambiente > Limpeza da loja: não avaliável, nenhum trecho com nota do texto.", conta)

    def test_the_ai_text_goes_to_its_own_section_and_not_to_the_executive_summary(self):
        separado = rel.separar_secoes(prompts.EXEMPLO_ANALISE_GERAL)

        self.assertTrue(separado.secao(rel.RESUMO).startswith("Máquina fora de serviço é a dor mais citada"))
        self.assertNotIn("*Achado:*", separado.secao(rel.RESUMO))
        self.assertTrue(separado.secao(rel.POR_TEMA).startswith("**A1 · Equipamentos > Máquina fora de serviço**"))
        self.assertEqual(separado.fora_da_estrutura, [])

    def test_without_patterns_the_section_says_so(self):
        agregacao = _exemplo()
        agregacao.temas = [tema for tema in agregacao.temas if tema.faixa != PADRAO]
        tarefa = rel.mensagem_para_ia(agregacao, {}, "cabeçalho").split("## Tarefa", 1)[1]

        self.assertIn("**Resumo por tema**: nenhum achado está em padrão; escreva apenas: Nenhum tema em padrão.", tarefa)
        self.assertTrue(_secao_resumo(_montar(agregacao, "### Resumo por tema\nNenhum tema em padrão.")).startswith(
            "Nenhum tema em padrão nesta amostra (5 ou mais respondentes e pelo menos 10% do total)"
        ))
        self.assertEqual(rel.verificar_referencias(agregacao, rel.separar_secoes("### Resumo por tema\nNenhum.")).count(
            'A resposta da IA não trouxe a seção "Resumo por tema".'
        ), 0)

    def test_the_exploratory_reading_follows_up_instead_of_recommending(self):
        agregacao = _agregar(_projeto_pequeno())
        tarefa = rel.mensagem_para_ia(agregacao, {}, "cabeçalho").split("## Tarefa", 1)[1]

        self.assertIn(
            "**Resumo por tema**, um bloco para cada um destes achados em padrão, nesta ordem: A1. Sem "
            "recomendação nesta leitura, o bloco não tem a linha *Recomendação:*: a última linha é *Para "
            "acompanhar e investigar:*, com o ID do item M que você escreveu para o achado.",
            tarefa,
        )

    def test_the_exploratory_block_loses_a_recommendation_line(self):
        # Na geração de 11/11/2026 (Duque de Caxias 2), a IA escreveu as duas linhas.
        agregacao = _agregar(_projeto_pequeno())
        texto = TEXTO_DO_PROJETO_PEQUENO.replace(
            "- *Para acompanhar e investigar:*",
            "- *Recomendação:* nenhuma recomendação responde a este tema.\n- *Para acompanhar e investigar:*",
        )

        resumo = _secao_resumo(rel.montar_relatorio(agregacao, "cabeçalho", rel.separar_secoes(texto), "apêndice"))

        self.assertNotIn("Recomendação", resumo)
        self.assertIn("- *Para acompanhar e investigar:* se a máquina parada continua", resumo)
        self.assertEqual(_verificar(texto, agregacao), [
            'O bloco de A1 no Resumo por tema traz "Recomendação:", que a leitura exploratória não tem: a linha '
            "ficou fora do relatório.",
        ])


class VerificacaoDoResumoTests(unittest.TestCase):
    def test_a_missing_block_and_a_block_for_a_signal(self):
        texto = _trocar(
            BLOCO_A4,
            "**A5 · Ambiente > Espera por máquina livre**\n- *Achado:* a espera é criticada no fim de semana.\n"
            "- *Hipótese:* nenhuma hipótese se apoia neste tema.\n"
            "- *Recomendação:* nenhuma recomendação responde a este tema.",
        )

        self.assertEqual(_verificar(texto), [
            "O Resumo por tema traz A5, de Ambiente > Espera por máquina livre, que está em sinal: o resumo cobre "
            "só os temas em padrão, e o bloco ficou fora do relatório.",
            "O Resumo por tema não trouxe o bloco de A4 (Ambiente > Limpeza da loja).",
        ])
        resumo = _secao_resumo(_montar(_exemplo(), texto))
        self.assertIn(
            "#### A4 · Ambiente > Limpeza da loja · padrão, 9/64\n\n**Termômetro do tema:** não avaliável: nenhum "
            "trecho do tema tem nota do sentimento do texto.\n\n_A IA não escreveu esta seção._",
            resumo,
        )
        self.assertNotIn("fim de semana", resumo)

    def test_repeated_and_unknown_blocks(self):
        texto = _trocar(
            "**A2 · Atendimento > Atendente da unidade**", "**A1 · Equipamentos > Máquina fora de serviço**"
        ).replace("**A4 · Ambiente > Limpeza da loja**", "**A9 · Ambiente > Limpeza da loja**")

        self.assertEqual(_verificar(texto), [
            "O Resumo por tema traz o bloco de A1 mais de uma vez; entrou o primeiro.",
            "O Resumo por tema traz A9, que não existe.",
            "O Resumo por tema não trouxe o bloco de A2 (Atendimento > Atendente da unidade).",
            "O Resumo por tema não trouxe o bloco de A4 (Ambiente > Limpeza da loja).",
        ])

    def test_each_block_has_the_three_lines(self):
        texto = _trocar("- *Hipótese:* é a dor com mais chance de virar reclamação pública (H2).\n", "").replace(
            "- *Recomendação:* nenhuma recomendação responde a este tema.", ""
        )

        self.assertEqual(_verificar(texto), [
            'O bloco de A1 no Resumo por tema está sem "Hipótese:".',
            'O bloco de A4 no Resumo por tema está sem "Recomendação:".',
        ])

    def test_the_ids_in_a_block_belong_to_its_finding(self):
        texto = (
            _trocar("reclamação pública (H2).", "reclamação pública (H1).")
            .replace("estornar o ciclo interrompido (R1).", "estornar o ciclo interrompido.")
            .replace("até a correção (R2).", "até a correção (R3).")
        )

        self.assertEqual(_verificar(texto), [
            "O bloco de A1 no Resumo por tema cita H1, que não se apoia em A1.",
            "O bloco de A1 no Resumo por tema não cita a recomendação que se liga a ele (R1).",
            "O bloco de A3 no Resumo por tema cita R3, que não responde a A3.",
        ])

    def test_a_recommendation_through_a_hypothesis_of_the_finding_passes(self):
        # R3 responde a A2 e a H3, que se apoia em A2 e em A4: citá-la no bloco de A4 não é erro.
        texto = _trocar(BLOCO_A4, BLOCO_A4.replace(
            "nenhuma recomendação responde a este tema.", "levar a rotina dos atendentes às outras unidades (R3)."
        ))

        self.assertEqual(_verificar(texto), [])

    def test_the_exploratory_block_cites_the_follow_up_of_its_finding(self):
        agregacao = _agregar(_projeto_pequeno())
        texto = TEXTO_DO_PROJETO_PEQUENO.replace("em metade dos relatos (M1).", "em metade dos relatos (M2).")

        self.assertEqual(_verificar(TEXTO_DO_PROJETO_PEQUENO, agregacao), [])
        self.assertEqual(_verificar(texto, agregacao), [
            "O bloco de A1 no Resumo por tema cita M2, que não vem de A1.",
        ])

    def test_the_block_text_is_checked_like_the_rest(self):
        texto = _trocar("com ciclo pago e perdido.", "com ciclo pago e perdido, dito com valência baixa (Áudio 99).")

        self.assertEqual(_verificar(texto), [
            'Resumo por tema usa "valência", termo técnico da voz, que fica no apêndice.',
            "Resumo por tema cita Áudio 99, que não existe.",
        ])


if __name__ == "__main__":
    unittest.main()
