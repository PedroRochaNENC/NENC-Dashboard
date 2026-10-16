"""Agregação da Análise Geral v2 (fase 3 da spec).

Os números do relatório saem daqui, e não da IA: a contagem por respondente e
as faixas, a voz por tema, a Matriz, o Termômetro e a comparação com a análise
anterior. Os testes montam projetos pequenos à mão, com a classificação de
cada áudio já pronta, sem banco e sem IA.
"""

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

from utils import prosodia_relatorio as rel
from utils.prosodia_project_types import ENTREVISTA_QUALITATIVA, PESQUISA_OPINIAO
from utils.prosodia_relatorio import (
    MENCAO_ISOLADA,
    PADRAO,
    SINAL,
    AnaliseAnterior,
    AudioDoProjeto,
    LeituraTrecho,
    Recorte,
)
from utils.prosodia_temas import Linha, chave_classificacao, parse_taxonomia

TAXONOMIA = parse_taxonomia(
    "Equipamentos > Máquina fora de serviço: parada ou quebrada\n"
    "Atendimento > Atendente da unidade\n"
    "Ambiente > Lotação\n"
    "Pagamento > Preço do ciclo"
)
MAQUINA, ATENDENTE, LOTACAO, PRECO = (subtema.rotulo for subtema in TAXONOMIA.subtemas)
INICIO = datetime(2026, 10, 1, 9, 0)


def _linhas(n=3, locutor="Respondente"):
    """n falas de 3,5 s, uma a cada 4 s, cada uma um segmento do Whisper."""
    return tuple(
        Linha(i, locutor, "Fala {}.".format(i), (i - 1) * 4.0, (i - 1) * 4.0 + 3.5, i - 1)
        for i in range(1, n + 1)
    )


def _tema(subtema, polaridade="negativa", linhas=(1,)):
    return {
        "subtema": subtema,
        "polaridade": polaridade,
        "evidencia": "",
        "trechos": [
            {"linha": n, "segmento_idx": n - 1, "inicio_s": (n - 1) * 4.0, "fim_s": (n - 1) * 4.0 + 3.5}
            for n in linhas
        ],
    }


def _emergente(rotulo, categoria, polaridade="negativa", linhas=(1,)):
    emergente = _tema(rotulo, polaridade, linhas)
    emergente["rotulo"] = emergente.pop("subtema")
    emergente["categoria"] = categoria
    return emergente


def _sessao(numero, audio_id):
    return "wa_+55219900{:05d}_{}".format(numero, audio_id)


def _audio(audio_id, *temas, numero=None, emergentes=(), linhas=None, classificada=True,
           criado=None, respondente=None, sessao=None):
    """Áudio do WhatsApp; `numero` repetido é o mesmo respondente na Opinião."""
    classificacao = {
        "respondente": respondente,
        "respondente_incerto": False,
        "temas": list(temas),
        "emergentes": list(emergentes),
    }
    return AudioDoProjeto(
        audio_id,
        sessao or _sessao(audio_id if numero is None else numero, audio_id),
        _linhas() if linhas is None else linhas,
        classificacao if classificada else None,
        criado or INICIO + timedelta(hours=audio_id),
    )


def _agregar(audios, tipo=PESQUISA_OPINIAO, recorte=None, **kwargs):
    ids = [audio.audio_id for audio in audios] if recorte is None else recorte
    return rel.agregar(audios, ids, TAXONOMIA, tipo, **kwargs)


def _linha_da_tabela(agregacao, subtema):
    return next(tema for tema in agregacao.temas if tema.subtema == subtema)


def _vad(sessao, segmentos, valencia, ativacao=0.0, linhas_por_segmento=2, com_segmento=True):
    """Linhas do VAD de 1 s, `linhas_por_segmento` em cada fala de _linhas (uma a cada 4 s).

    `valencia` e `ativacao` podem ser um número ou uma função do segmento.
    """
    linhas = []
    for segmento in segmentos:
        for k in range(linhas_por_segmento):
            inicio = segmento * 4.0 + k
            linhas.append({
                "session_id": sessao,
                "SpeakerName": "Respondente",
                "start_s": inicio,
                "end_s": inicio + 1.0,
                "segmento_idx": segmento if com_segmento else None,
                "dim_valence": valencia(segmento) if callable(valencia) else valencia,
                "dim_arousal": ativacao(segmento) if callable(ativacao) else ativacao,
            })
    return linhas


class ChaveRespondenteTests(unittest.TestCase):
    def test_opinion_groups_whatsapp_audios_of_the_same_number(self):
        self.assertEqual(
            rel.chave_respondente("wa_+5521998548459_39", PESQUISA_OPINIAO),
            rel.chave_respondente("wa_+5521998548459_40", PESQUISA_OPINIAO),
        )
        self.assertNotEqual(
            rel.chave_respondente("wa_+5521998548459_39", PESQUISA_OPINIAO),
            rel.chave_respondente("wa_+556192689455_62", PESQUISA_OPINIAO),
        )

    def test_uploads_and_interviews_are_one_respondent_per_audio(self):
        for sessao in ("wa_upload_7", "upload_entrevista_3", "sessao-manual"):
            with self.subTest(sessao=sessao):
                self.assertEqual(rel.chave_respondente(sessao, PESQUISA_OPINIAO), sessao)
        self.assertNotEqual(
            rel.chave_respondente("wa_+5521998548459_39", ENTREVISTA_QUALITATIVA),
            rel.chave_respondente("wa_+5521998548459_40", ENTREVISTA_QUALITATIVA),
        )


def _arquivo(conteudo, audio):
    """O áudio com a assinatura do arquivo: a mesma em dois áudios é o mesmo arquivo enviado de novo."""
    return replace(audio, conteudo=conteudo)


def _prosodia_json(*vad):
    return json.dumps({"result": {"vad": [{"start": s, "end": e} for s, e in vad], "asr": []}}).encode("utf-8")


class ConteudoRepetidoTests(unittest.TestCase):
    def test_the_same_file_has_the_same_signature(self):
        arquivo = _prosodia_json((0.0, 2.5), (3.1, 6.0))

        self.assertEqual(
            rel.assinatura_do_conteudo(arquivo), rel.assinatura_do_conteudo(_prosodia_json((0.0, 2.5), (3.1, 6.0)))
        )
        self.assertNotEqual(
            rel.assinatura_do_conteudo(arquivo), rel.assinatura_do_conteudo(_prosodia_json((0.0, 2.6), (3.1, 6.0)))
        )
        # O JSON sem "result", que o prosodia_loader também lê.
        self.assertIsNotNone(rel.assinatura_do_conteudo(b'{"vad": [{"start": 0.0, "end": 1.0}]}'))

    def test_without_a_vad_segment_there_is_no_signature(self):
        # Dois áudios mudos dariam o mesmo JSON.
        for arquivo in (None, b"", _prosodia_json(), b"nao e json", b"[1, 2]", b'{"result": []}'):
            with self.subTest(arquivo=arquivo):
                self.assertIsNone(rel.assinatura_do_conteudo(arquivo))

    def test_in_an_interview_copies_of_the_same_file_count_once(self):
        # O 03 é o 01 enviado de novo, e a IA achou nele um tema a mais.
        audios = [
            _arquivo("a", _audio(1, _tema(MAQUINA))),
            _arquivo("b", _audio(2, _tema(MAQUINA))),
            _arquivo("a", _audio(3, _tema(MAQUINA), _tema(LOTACAO))),
        ]

        agregacao = _agregar(audios, tipo=ENTREVISTA_QUALITATIVA)

        self.assertEqual(agregacao.repetidos, {3: 1})
        self.assertEqual((agregacao.n_respondentes, agregacao.contados), (2, (1, 2)))
        self.assertEqual(_linha_da_tabela(agregacao, MAQUINA).respondentes, 2)
        self.assertEqual(agregacao.sem_mencao, [ATENDENTE, LOTACAO, PRECO])
        # O snapshot guarda a cópia, para a análise seguinte não a tratar como nova.
        self.assertEqual(rel.snapshot(agregacao, Recorte())["audios"], [1, 2, 3])

    def test_in_opinion_different_audios_of_the_same_number_still_join(self):
        # Arquivos diferentes do mesmo número: uma pessoa, que trata do que qualquer áudio dela tratar.
        audios = [
            _arquivo("a", _audio(1, _tema(MAQUINA), numero=9)),
            _arquivo("b", _audio(2, _tema(LOTACAO), numero=9)),
            _arquivo("c", _audio(3, _tema(MAQUINA))),
        ]

        agregacao = _agregar(audios)

        self.assertEqual(agregacao.repetidos, {})
        self.assertEqual((agregacao.n_respondentes, agregacao.contados), (2, (1, 2, 3)))
        self.assertEqual(_linha_da_tabela(agregacao, MAQUINA).respondentes, 2)
        self.assertEqual(_linha_da_tabela(agregacao, LOTACAO).respondentes, 1)

    def test_in_opinion_the_same_file_from_another_number_counts_once(self):
        # Um áudio encaminhado por outro número é o mesmo depoimento.
        audios = [
            _arquivo("a", _audio(1, _tema(MAQUINA), numero=9)),
            _arquivo("a", _audio(2, _tema(MAQUINA), numero=5)),
        ]

        agregacao = _agregar(audios)

        self.assertEqual((agregacao.repetidos, agregacao.n_respondentes), ({2: 1}, 1))

    def test_the_first_copy_with_a_valid_classification_is_the_one_that_counts(self):
        # A classificação do 01 falhou: conta o 02, e o 01 não aparece como sem classificação.
        audios = [
            _arquivo("a", _audio(1, classificada=False)),
            _arquivo("a", _audio(2, _tema(LOTACAO))),
            _arquivo("a", _audio(3, _tema(LOTACAO), _tema(PRECO))),
        ]

        agregacao = _agregar(audios, tipo=ENTREVISTA_QUALITATIVA)

        self.assertEqual(agregacao.repetidos, {1: 2, 3: 2})
        self.assertEqual((agregacao.contados, agregacao.sem_classificacao), ((2,), ()))
        self.assertEqual([tema.subtema for tema in agregacao.temas], [LOTACAO])

    def test_only_audios_of_the_cut_with_a_signature_are_joined(self):
        audios = [
            _arquivo("a", _audio(1, _tema(MAQUINA))),
            _arquivo("a", _audio(2, _tema(MAQUINA))),
            # Sem assinatura (prosodia_json ausente ou sem VAD), nada se junta.
            _audio(3, _tema(MAQUINA)),
            _audio(4, _tema(MAQUINA)),
        ]

        agregacao = _agregar(audios, tipo=ENTREVISTA_QUALITATIVA, recorte=[2, 3, 4])

        self.assertEqual((agregacao.repetidos, agregacao.n_respondentes), ({}, 3))

    def test_the_copy_stays_out_of_the_voice_the_thermometer_and_the_matrix(self):
        audios = [
            _arquivo("a", _audio(1, _tema(MAQUINA))),
            _arquivo("a", _audio(2, _tema(MAQUINA))),
            _audio(3, _tema(MAQUINA)),
        ]
        sinc = pd.DataFrame([
            linha for a in audios for linha in _vad(a.session_id, (0, 1, 2), valencia=lambda s: 0.1 * s)
        ])
        transcricao = pd.DataFrame([
            {"session_id": a.session_id, "start_s": 0.0, "end_s": 3.5, "sentimento_texto": 0.6} for a in audios
        ])

        with patch.object(rel, "montar_matriz", wraps=rel.montar_matriz) as matriz:
            agregacao = _agregar(
                audios, tipo=ENTREVISTA_QUALITATIVA, sinc_projeto=sinc, transcricao_projeto=transcricao
            )

        self.assertEqual(_linha_da_tabela(agregacao, MAQUINA).voz.respondentes, 2)
        self.assertEqual(agregacao.termometro.respondentes, 2)
        self.assertEqual([a.audio_id for a in matriz.call_args.args[2]], [1, 3])

    def test_the_limitations_list_the_joined_audios_by_alias(self):
        audios = [
            _arquivo("a", _audio(1, _tema(MAQUINA))),
            _arquivo("b", _audio(2, _tema(MAQUINA))),
            _arquivo("a", _audio(3, _tema(MAQUINA))),
            _arquivo("b", _audio(4, _tema(MAQUINA))),
            _arquivo("b", _audio(5, _tema(MAQUINA))),
        ]

        varios = rel.apendice_markdown(_agregar(audios, tipo=ENTREVISTA_QUALITATIVA), None, None)
        um = rel.apendice_markdown(_agregar(audios[:3], tipo=ENTREVISTA_QUALITATIVA), None, None)
        nenhum = rel.apendice_markdown(_agregar(audios[:2], tipo=ENTREVISTA_QUALITATIVA), None, None)

        self.assertIn(
            "3 áudios do recorte são o mesmo arquivo de outro, enviado de novo (a análise de prosódia é "
            "idêntica), e ficaram fora da contagem. Em cada grupo conta só o primeiro: Áudio 01 = Áudio 03; "
            "Áudio 02 = Áudio 04 = Áudio 05.",
            varios,
        )
        self.assertIn("1 áudio do recorte é o mesmo arquivo de outro", um)
        self.assertIn("e ficou fora da contagem. Em cada grupo conta só o primeiro: Áudio 01 = Áudio 03.", um)
        self.assertNotIn("mesmo arquivo", nenhum)


class AliasesTests(unittest.TestCase):
    def test_numbered_in_the_order_the_audios_entered_the_database(self):
        audios = [_audio(30), _audio(7), _audio(12)]

        self.assertEqual(rel.aliases(audios), {7: "Áudio 01", 12: "Áudio 02", 30: "Áudio 03"})

    def test_width_grows_past_99_audios(self):
        nomes = rel.aliases(_audio(i) for i in range(1, 101))

        self.assertEqual((nomes[1], nomes[100]), ("Áudio 001", "Áudio 100"))

    def test_the_alias_does_not_change_with_the_filter(self):
        audios = [_audio(1, _tema(MAQUINA)), _audio(2, _tema(MAQUINA))]

        self.assertEqual(_agregar(audios, recorte=[2]).aliases[2], "Áudio 02")


class ClassificacoesValidasTests(unittest.TestCase):
    def test_only_the_classification_with_the_current_key_counts(self):
        linhas = _linhas()
        chave = chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, linhas)
        salvas = {
            1: {"chave": chave, "temas": {"temas": [_tema(MAQUINA)]}},
            2: {"chave": "de-outra-taxonomia", "temas": {"temas": [_tema(LOTACAO)]}},
        }

        validas = rel.classificacoes_validas({1: linhas, 2: linhas, 3: linhas}, salvas, TAXONOMIA, PESQUISA_OPINIAO)

        self.assertEqual(validas, {1: {"temas": [_tema(MAQUINA)]}})


class FaixaTests(unittest.TestCase):
    def test_limits(self):
        casos = [
            (0, 64, None),
            (1, 64, MENCAO_ISOLADA),
            (2, 64, MENCAO_ISOLADA),
            (3, 64, SINAL),
            (4, 64, SINAL),
            (3, 3, SINAL),
            (5, 50, PADRAO),
            (5, 51, SINAL),
            (6, 64, SINAL),
            (7, 64, PADRAO),
        ]
        for respondentes, total, esperada in casos:
            with self.subTest(respondentes=respondentes, total=total):
                self.assertEqual(rel.faixa(respondentes, total), esperada)


class PolaridadesTests(unittest.TestCase):
    def test_text_cell_goes_from_the_largest_count_down(self):
        self.assertEqual(rel.texto_polaridades({"mista": 2, "negativa": 17}), "17 neg · 2 mistas")
        self.assertEqual(rel.texto_polaridades({"negativa": 1, "positiva": 8}), "8 pos · 1 neg")
        self.assertEqual(rel.texto_polaridades({"mista": 1, "positiva": 1, "neutra": 0}), "1 pos · 1 mista")
        self.assertEqual(rel.texto_polaridades({"neutra": 2}), "2 neutras")

    def test_predominant_needs_a_single_winner(self):
        self.assertEqual(rel.predominante({"positiva": 8, "negativa": 1}), rel.POSITIVO)
        self.assertEqual(rel.predominante({"negativa": 17, "mista": 2}), rel.NEGATIVO)
        self.assertEqual(rel.predominante({"positiva": 2, "negativa": 2}), rel.MISTO_OU_NEUTRO)
        self.assertEqual(rel.predominante({"neutra": 3, "negativa": 2}), rel.MISTO_OU_NEUTRO)
        self.assertEqual(rel.predominante({}), rel.MISTO_OU_NEUTRO)


def _leituras(voz, ativacao=0.0, n=5, peso=1.0, respondente=None):
    """n trechos, cada um de um respondente diferente, ou todos de `respondente`."""
    return [
        (respondente or "resp{}".format(i), LeituraTrecho(voz, ativacao, peso)) for i in range(n)
    ]


class LerVozTests(unittest.TestCase):
    def test_fewer_than_five_respondents_with_voice_is_not_assessable(self):
        # Quatro respondentes, um deles com muitos trechos, e um trecho sem voz.
        leituras = _leituras(0.5, n=3) + _leituras(0.5, n=8, respondente="falante") + [("outro", LeituraTrecho())]

        leitura = rel.ler_voz(leituras, rel.POSITIVO)

        self.assertFalse(leitura.avaliavel)
        self.assertEqual((leitura.respondentes, leitura.trechos), (4, 11))
        self.assertEqual(leitura.texto(), "não avaliável (4 respondentes com voz)")
        self.assertEqual(rel.ler_voz([], rel.NEGATIVO).texto(), "não avaliável (0 respondentes com voz)")
        self.assertEqual(
            rel.ler_voz(_leituras(0.5, n=1), rel.NEGATIVO).texto(), "não avaliável (1 respondente com voz)"
        )

    def test_five_respondents_with_voice_are_assessable(self):
        leitura = rel.ler_voz(_leituras(0.5, n=5), rel.POSITIVO)

        self.assertTrue(leitura.avaliavel)
        self.assertEqual((leitura.respondentes, leitura.trechos), (5, 5))

    def test_valence_limits(self):
        casos = [(0.15, rel.ACIMA), (0.149, rel.NO_HABITUAL), (-0.149, rel.NO_HABITUAL), (-0.15, rel.ABAIXO)]
        for voz, direcao in casos:
            with self.subTest(voz=voz):
                self.assertEqual(rel.ler_voz(_leituras(voz), rel.POSITIVO).direcao, direcao)

    def test_activation_limits(self):
        casos = [(0.3, "alta"), (0.299, "média"), (-0.299, "média"), (-0.3, "baixa")]
        for ativacao, nivel in casos:
            with self.subTest(ativacao=ativacao):
                self.assertEqual(rel.ler_voz(_leituras(0.0, ativacao), rel.POSITIVO).nivel, nivel)

    def test_qualifier_compares_the_voice_with_the_text(self):
        casos = [
            (rel.POSITIVO, 0.4, 0.0, "reforça"),
            (rel.POSITIVO, 0.0, -0.5, "atenua"),
            (rel.POSITIVO, -0.4, 0.0, "contradiz"),
            (rel.NEGATIVO, 0.4, 0.0, "contradiz"),
            (rel.NEGATIVO, 0.0, 0.5, "atenua"),
            (rel.NEGATIVO, -0.4, 0.0, "reforça"),
            (rel.MISTO_OU_NEUTRO, 0.4, 0.0, ""),
        ]
        for predominante, voz, ativacao, qualificador in casos:
            with self.subTest(predominante=predominante, voz=voz, ativacao=ativacao):
                self.assertEqual(rel.ler_voz(_leituras(voz, ativacao), predominante).qualificador, qualificador)

    def test_voice_in_the_habitual_range_with_medium_activation_has_no_qualifier(self):
        # Na validação com a Smart Fit, esse "atenua" virou "satisfação protocolar"
        # mesmo com o prompt pedindo o contrário.
        for predominante in (rel.POSITIVO, rel.NEGATIVO):
            with self.subTest(predominante=predominante):
                self.assertEqual(rel.ler_voz(_leituras(0.0, 0.1), predominante).texto(), "no habitual, ativação média")
        sem_ativacao = _leituras(0.0, None)
        self.assertEqual(rel.ler_voz(sem_ativacao, rel.POSITIVO).texto(), "no habitual")

    def test_a_reading_that_depends_on_one_respondent_stays_in_the_habitual_range(self):
        # Média 0,16, acima do limiar; sem o respondente de 0,4, cai para 0,10.
        estreita = _leituras(0.1, n=4) + _leituras(0.4, n=1, respondente="unico")
        # Média 0,30 e longe do limiar sem qualquer um.
        firme = _leituras(0.3, n=5)

        leitura = rel.ler_voz(estreita, rel.POSITIVO)

        self.assertEqual((leitura.direcao, leitura.instavel), (rel.NO_HABITUAL, True))
        self.assertAlmostEqual(leitura.valencia, 0.16)
        self.assertEqual(rel.ler_voz(firme, rel.POSITIVO).direcao, rel.ACIMA)
        self.assertFalse(rel.ler_voz(firme, rel.POSITIVO).instavel)
        # Como Instalações na Smart Fit: média -0,1505, 0,0005 além do limiar, e
        # sem o respondente de -0,30 ela volta para -0,11.
        no_fio = [
            ("r{}".format(i), LeituraTrecho(voz, 0.0, 1.0))
            for i, voz in enumerate((-0.30, -0.25, -0.10, -0.05, -0.0525))
        ]
        self.assertEqual(rel.ler_voz(no_fio, rel.POSITIVO).texto(), "no habitual, ativação média")

    def test_activation_also_needs_to_hold_without_each_respondent(self):
        leituras = _leituras(0.0, 0.2, n=4) + _leituras(0.0, 0.8, n=1, respondente="unico")

        leitura = rel.ler_voz(leituras, rel.NEGATIVO)

        self.assertEqual((leitura.nivel, leitura.qualificador, leitura.instavel), ("média", "", True))

        self.assertEqual(
            rel.ler_voz(_leituras(-0.4, ativacao=0.5), rel.NEGATIVO).texto(),
            "abaixo do habitual, ativação alta → reforça",
        )
        self.assertEqual(
            rel.ler_voz(_leituras(0.4), rel.MISTO_OU_NEUTRO).texto(), "acima do habitual, ativação média"
        )

    def test_each_respondent_is_weighted_by_duration_within_their_trechos(self):
        # Um respondente com um trecho de 1 s em 0,0 e um de 3 s em 1,0.
        leituras = [("duas falas", LeituraTrecho(0.0, 0.0, 1.0)), ("duas falas", LeituraTrecho(1.0, 0.0, 3.0))]
        leituras += _leituras(0.0, n=4)

        self.assertAlmostEqual(rel.ler_voz(leituras, rel.POSITIVO).valencia, 0.75 / 5)

    def test_a_respondent_who_talks_a_lot_does_not_decide_the_reading(self):
        # Quatro respondentes e um com dez trechos longos: pela média dos trechos
        # a valência daria 50/54 e a ativação, 73/54.
        leituras = _leituras(0.0, -0.5, n=4) + _leituras(1.0, 1.5, n=10, peso=5.0, respondente="falante")

        leitura = rel.ler_voz(leituras, rel.POSITIVO)

        self.assertAlmostEqual(leitura.valencia, 0.2)
        self.assertAlmostEqual(leitura.ativacao, -0.1)
        # Pela média dos trechos, 5/14: acima do habitual.
        self.assertEqual(
            rel.ler_voz(_leituras(0.0, n=4) + _leituras(0.5, n=10, respondente="falante"), rel.POSITIVO).direcao,
            rel.NO_HABITUAL,
        )


class ContagemTests(unittest.TestCase):
    def test_a_respondent_counts_once_per_subtheme(self):
        audios = [
            # Duas falas do mesmo tema no mesmo áudio.
            _audio(1, _tema(MAQUINA, linhas=(1, 2))),
            # Dois áudios do mesmo número, na Pesquisa de Opinião.
            _audio(2, _tema(MAQUINA), numero=9),
            _audio(3, _tema(MAQUINA), numero=9),
            _audio(4, _tema(ATENDENTE, "positiva")),
        ]

        agregacao = _agregar(audios)

        self.assertEqual(agregacao.n_respondentes, 3)
        self.assertEqual(_linha_da_tabela(agregacao, MAQUINA).respondentes, 2)

    def test_in_an_interview_each_audio_is_a_respondent(self):
        audios = [_audio(2, _tema(MAQUINA), numero=9), _audio(3, _tema(MAQUINA), numero=9)]

        agregacao = _agregar(audios, tipo=ENTREVISTA_QUALITATIVA)

        self.assertEqual((agregacao.n_respondentes, _linha_da_tabela(agregacao, MAQUINA).respondentes), (2, 2))

    def test_praise_and_criticism_from_the_same_respondent_is_mixed(self):
        audios = [
            _audio(1, _tema(ATENDENTE, "positiva"), numero=5),
            _audio(2, _tema(ATENDENTE, "negativa"), numero=5),
            _audio(3, _tema(ATENDENTE, "neutra"), numero=6),
            _audio(4, _tema(ATENDENTE, "positiva"), numero=6),
        ]

        linha = _linha_da_tabela(_agregar(audios), ATENDENTE)

        self.assertEqual(linha.polaridades, {"mista": 1, "positiva": 1})

    def test_who_spoke_without_a_theme_is_in_n_and_who_did_not_speak_is_not(self):
        audios = [
            _audio(1, _tema(MAQUINA)),
            _audio(2),
            _audio(3, linhas=()),
            _audio(4, classificada=False),
            _audio(5, _tema(MAQUINA)),
        ]

        agregacao = _agregar(audios, recorte=[1, 2, 3, 4])

        self.assertEqual(agregacao.n_respondentes, 2)
        self.assertEqual((agregacao.sem_fala, agregacao.sem_classificacao), ((3,), (4,)))
        self.assertEqual(agregacao.contados, (1, 2))
        self.assertEqual(_linha_da_tabela(agregacao, MAQUINA).respondentes, 1)

    def test_table_order_and_subthemes_without_mention(self):
        audios = [
            _audio(1, _tema(LOTACAO), _tema(ATENDENTE)),
            _audio(2, _tema(LOTACAO)),
            _audio(3, _tema(MAQUINA)),
        ]

        agregacao = _agregar(audios)

        self.assertEqual([tema.subtema for tema in agregacao.temas], [LOTACAO, MAQUINA, ATENDENTE])
        self.assertEqual(agregacao.sem_mencao, [PRECO])

    def test_subtheme_outside_the_taxonomy_is_not_counted(self):
        agregacao = _agregar([_audio(1, _tema("Ambiente > Estacionamento"))])

        self.assertEqual((agregacao.n_respondentes, agregacao.temas), (1, []))

    def test_emergent_themes_count_respondents_ignoring_case_and_accents(self):
        audios = [
            _audio(1, emergentes=[
                {"rotulo": "Estacionamento", "categoria": "Ambiente", "polaridade": "negativa"},
                {"rotulo": "estacionamento", "polaridade": "negativa"},
            ]),
            _audio(2, emergentes=[{"rotulo": "ESTACIONAMENTO", "polaridade": "positiva"}]),
            _audio(3, emergentes=[{"rotulo": "Cheiro do sabão", "categoria": "Produtos", "polaridade": "positiva"}]),
        ]

        agregacao = _agregar(audios)

        self.assertEqual(
            [(e.rotulo, e.categoria, e.respondentes, e.polaridades) for e in agregacao.emergentes],
            [
                ("Estacionamento", "Ambiente", 2, {"negativa": 1, "positiva": 1}),
                ("Cheiro do sabão", "Produtos", 1, {"positiva": 1}),
            ],
        )
        self.assertIn(
            "Temas emergentes (fora da taxonomia): Estacionamento (Ambiente), 2/3, 1 pos · 1 neg · "
            "Cheiro do sabão (Produtos), 1/3, 1 pos.",
            rel.tabela_temas_markdown(agregacao),
        )

    def test_emergent_whose_category_is_a_subtheme_counts_in_that_subtheme(self):
        # A categoria é o rótulo de Lotação em outra caixa, sem acento e sem espaços.
        audios = [
            _audio(1, emergentes=[_emergente("Fila no vestiário", "AMBIENTE>lotacao", linhas=(2,))]),
            _audio(2, _tema(LOTACAO)),
            _audio(3, _tema(LOTACAO, "positiva")),
        ]

        agregacao = _agregar(audios)

        linha = _linha_da_tabela(agregacao, LOTACAO)
        self.assertEqual((linha.respondentes, linha.polaridades), (3, {"negativa": 2, "positiva": 1}))
        self.assertEqual(agregacao.emergentes, [])
        self.assertIn((1, 2), [(trecho.audio_id, trecho.linha) for trecho in agregacao.trechos[LOTACAO]])
        # A classificação gravada fica como está: a regra vale na leitura.
        self.assertEqual(len(audios[0].classificacao["emergentes"]), 1)

    def test_emergent_in_a_subtheme_the_respondent_cited_joins_the_polarities(self):
        audios = [
            # No mesmo áudio, o subtema com elogio e o emergente com crítica, com uma fala em comum.
            _audio(1, _tema(LOTACAO, "positiva", linhas=(1,)),
                   emergentes=[_emergente("Fila no vestiário", "Ambiente > Lotação", linhas=(1, 2))]),
            # Em dois áudios do mesmo número.
            _audio(2, _tema(LOTACAO, "neutra"), numero=7),
            _audio(3, emergentes=[_emergente("Fila no vestiário", "ambiente > lotação")], numero=7),
        ]
        sinc = pd.DataFrame(_vad(_sessao(1, 1), (0, 1, 2), valencia=lambda s: 0.1 * s, linhas_por_segmento=3))

        linha = _linha_da_tabela(_agregar(audios, sinc_projeto=sinc), LOTACAO)

        self.assertEqual((linha.respondentes, linha.polaridades), (2, {"mista": 1, "negativa": 1}))
        # A fala 1, citada no subtema e no emergente, entra uma vez na voz.
        self.assertEqual(linha.voz.trechos, 2)

    def test_only_the_whole_label_of_a_subtheme_brings_the_emergent_back(self):
        # "Ambiente" é só a categoria, e "Lotação", só o nome do subtema.
        audios = [
            _audio(1, emergentes=[_emergente("Fila no vestiário", "Ambiente")]),
            _audio(2, emergentes=[_emergente("Fila na catraca", "Lotação")]),
        ]

        agregacao = _agregar(audios)

        self.assertEqual(agregacao.temas, [])
        self.assertEqual([e.rotulo for e in agregacao.emergentes], ["Fila na catraca", "Fila no vestiário"])


class VozPorTrechoTests(unittest.TestCase):
    def test_voice_of_the_theme_comes_from_the_vad_lines_of_its_segments(self):
        audios, vad = [], []
        for audio_id in range(1, 7):
            audios.append(_audio(audio_id, _tema(MAQUINA)))
            # A fala do tema (segmento 0) mais alta e mais ativada que as outras.
            vad += _vad(
                _sessao(audio_id, audio_id), (0, 1, 2),
                valencia=lambda s: 0.4 if s == 0 else -0.2,
                ativacao=lambda s: 1.0 if s == 0 else 0.0,
            )
        sinc = pd.DataFrame(vad)
        regua = rel.reguas_do_projeto(sinc).valencia

        voz = _linha_da_tabela(_agregar(audios, sinc_projeto=sinc), MAQUINA).voz

        self.assertEqual(voz.trechos, 6)
        self.assertAlmostEqual(voz.valencia, min(1.0, (0.4 - regua.media) / regua.desvio / 2))
        self.assertEqual(voz.texto(), "acima do habitual, ativação alta → contradiz")

    def test_old_files_without_segment_are_matched_by_time(self):
        # Sem segmento, a fala 2 vai de 4 s até o início da fala 3, em 8 s.
        audios, vad = [], []
        for audio_id in range(1, 6):
            sessao = _sessao(audio_id, audio_id)
            linhas = tuple(Linha(i, "Respondente", "Fala {}.".format(i), (i - 1) * 4.0) for i in range(1, 4))
            tema = {"subtema": MAQUINA, "polaridade": "negativa", "trechos": [{"linha": 2, "inicio_s": 4.0}]}
            audios.append(AudioDoProjeto(audio_id, sessao, linhas, {"temas": [tema], "emergentes": []}, INICIO))
            vad += _vad(sessao, (0, 1, 2), lambda s: 0.5 if s == 1 else -0.25, linhas_por_segmento=4,
                        com_segmento=False)
        sinc = pd.DataFrame(vad)
        regua = rel.reguas_do_projeto(sinc).valencia

        voz = _linha_da_tabela(_agregar(audios, sinc_projeto=sinc), MAQUINA).voz

        self.assertEqual(voz.trechos, 5)
        self.assertAlmostEqual(voz.valencia, min(1.0, (0.5 - regua.media) / regua.desvio / 2))

    def test_a_trecho_without_time_counts_but_has_no_voice(self):
        linhas = (Linha(1, "Respondente", "Fala."), Linha(2, "Respondente", "Outra."))
        tema = {"subtema": MAQUINA, "polaridade": "negativa", "trechos": [{"linha": 1}]}
        audios = [
            AudioDoProjeto(i, _sessao(i, i), linhas, {"temas": [tema], "emergentes": []}) for i in range(1, 6)
        ]
        vad = [linha for i in range(1, 6) for linha in _vad(_sessao(i, i), (0, 1), 0.1 * i, com_segmento=False)]

        linha_tabela = _linha_da_tabela(_agregar(audios, sinc_projeto=pd.DataFrame(vad)), MAQUINA)

        self.assertEqual((linha_tabela.respondentes, linha_tabela.voz.trechos), (5, 0))


def _indice(linhas):
    """Índice por áudio no formato de indice_combinado_por_grupo."""
    return pd.DataFrame([
        {"grupo": sessao, "trechos": 3, "texto": texto, "voz": voz, "indice": indice,
         "positivo": 0.0, "neutro": 0.0, "negativo": 0.0}
        for sessao, texto, voz, indice in linhas
    ])


class MatrizTests(unittest.TestCase):
    def setUp(self):
        # 12 áudios com índice de 0,0 a 1,1 (terço superior a partir de ~0,73) e
        # ativação de 0,0 a 1,1 (terço inferior até ~0,37).
        self.audios = [_audio(i) for i in range(1, 13)]
        self.nomes = rel.aliases(self.audios)
        textos = {1: -0.5, 2: -0.5, 3: 0.0, 9: 0.5, 10: 0.1, 11: 0.5, 12: 0.5}
        self.indice = _indice([
            (a.session_id, textos.get(a.audio_id, 0.0), 0.1, (a.audio_id - 1) / 10) for a in self.audios
        ])
        self.ativacao = {a.session_id: (a.audio_id - 1) / 10 for a in self.audios}
        self.temas = {9: ("Máquina fora de serviço", "Lotação"), 11: ("Lotação",), 12: ("Lotação",)}

    def test_groups_follow_the_project_thirds(self):
        matriz = rel.montar_matriz(self.indice, self.ativacao, self.audios, self.nomes, self.temas)

        self.assertTrue(matriz.avaliavel)
        self.assertEqual([l.alias for l in matriz.vinculo.linhas], ["Áudio 12", "Áudio 11", "Áudio 09"])
        self.assertEqual(matriz.vinculo.temas_mais_citados, (("Lotação", 3),))
        self.assertEqual([l.alias for l in matriz.risco.linhas], ["Áudio 01", "Áudio 02"])

    def test_thirds_use_the_whole_project_and_the_table_only_the_cut(self):
        recorte = [a for a in self.audios if a.audio_id != 12]

        matriz = rel.montar_matriz(self.indice, self.ativacao, recorte, self.nomes, self.temas)

        self.assertEqual([l.alias for l in matriz.vinculo.linhas], ["Áudio 11", "Áudio 09"])
        self.assertAlmostEqual(matriz.corte_indice, 0.7 + 0.1 / 3)

    def test_fewer_than_ten_audios_with_index_is_not_assessable(self):
        indice = self.indice.iloc[:9].copy()
        indice.loc[0, "indice"] = None

        matriz = rel.montar_matriz(indice.iloc[:10], self.ativacao, self.audios, self.nomes, {})

        self.assertFalse(matriz.avaliavel)
        self.assertEqual(matriz.audios_com_indice, 8)

    def test_table_shows_up_to_fifteen_rows_per_group(self):
        audios = [_audio(i) for i in range(1, 49)]
        indice = _indice([(a.session_id, 0.5, 0.1, a.audio_id / 48) for a in audios])

        matriz = rel.montar_matriz(indice, {}, audios, rel.aliases(audios), {})

        self.assertEqual((matriz.vinculo.total, len(matriz.vinculo.linhas)), (16, 15))
        self.assertIsNone(matriz.corte_ativacao)

    def test_markdown(self):
        audios = self.audios
        agregacao = _agregar(audios)
        agregacao.matriz = rel.montar_matriz(self.indice, self.ativacao, audios, self.nomes, self.temas)

        texto = rel.matriz_markdown(agregacao)

        self.assertIn("**Vínculo forte** — índice combinado no terço superior do projeto (a partir de +0,73)", texto)
        self.assertIn("| Áudio 12 | +0,50 | +0,10 | +1,10 | Lotação |", texto)
        self.assertIn("Temas mais citados no grupo: Lotação (3/3).", texto)
        self.assertIn("| Áudio 01 | -0,50 | 0,00 | — |", texto)

        agregacao.matriz = rel.Matriz(8)
        self.assertEqual(
            rel.matriz_markdown(agregacao),
            "Matriz não avaliável: 8 áudios com índice combinado no projeto, menos que os 10 necessários.",
        )


class TermometroTests(unittest.TestCase):
    def test_each_respondent_weighs_the_same(self):
        audios = [_audio(1, linhas=_linhas(10)), _audio(2, linhas=_linhas(1))]
        transcricao = pd.DataFrame(
            [
                {"session_id": audios[0].session_id, "start_s": i * 4.0, "end_s": i * 4.0 + 3.5,
                 "sentimento_texto": 0.8}
                for i in range(10)
            ]
            + [{"session_id": audios[1].session_id, "start_s": 0.0, "end_s": 3.5, "sentimento_texto": -0.4}]
        )

        termometro = _agregar(audios, transcricao_projeto=transcricao).termometro

        # Somando as falas, a entrevista longa daria +0,69 e um termômetro de +3.
        self.assertEqual((termometro.media, termometro.nota), (0.2, 1))
        self.assertEqual((termometro.positivos, termometro.neutros, termometro.negativos), (1, 0, 1))

    def test_rounding_goes_half_away_from_zero_and_stops_at_five(self):
        casos = [([0.7], 4), ([-0.1], -1), ([0.09], 0), ([0.3], 2), ([-0.18], -1), ([1.0], 5)]
        for medias, nota in casos:
            with self.subTest(medias=medias):
                self.assertEqual(rel.calcular_termometro(medias).nota, nota)

    def test_without_text_score_it_is_not_assessable(self):
        transcricao = pd.DataFrame([{"session_id": _sessao(1, 1), "Text": "Fala."}])

        agregacao = _agregar([_audio(1, _tema(MAQUINA))], transcricao_projeto=transcricao)

        self.assertFalse(agregacao.termometro.avaliavel)
        self.assertIn("Não avaliável", rel.termometro_markdown(agregacao))

    def test_in_an_interview_only_the_interviewee_counts(self):
        sessao = _sessao(1, 1)
        audio = _audio(1, linhas=_linhas(2, "Entrevistado"), respondente="Entrevistado", sessao=sessao)
        transcricao = pd.DataFrame([
            {"session_id": sessao, "SpeakerName": "Entrevistador", "start_s": 0.0, "end_s": 3.5,
             "sentimento_texto": -0.9},
            {"session_id": sessao, "SpeakerName": "Entrevistado", "start_s": 4.0, "end_s": 7.5,
             "sentimento_texto": 0.6},
        ])

        termometro = _agregar([audio], tipo=ENTREVISTA_QUALITATIVA, transcricao_projeto=transcricao).termometro

        self.assertEqual(termometro.media, 0.6)

    def test_the_whole_account_is_shown(self):
        audios = [_audio(1), _audio(2)]
        transcricao = pd.DataFrame([
            {"session_id": audios[0].session_id, "start_s": 0.0, "end_s": 3.5, "sentimento_texto": -0.36},
            {"session_id": audios[1].session_id, "start_s": 0.0, "end_s": 3.5, "sentimento_texto": 0.0},
        ])

        texto = rel.termometro_markdown(_agregar(audios, transcricao_projeto=transcricao))

        self.assertIn("**-1** (de -5 a +5): sentimento médio do texto, -0,18 × 5 = -0,9, arredondado.", texto)
        self.assertIn("Respondentes pela média do texto: 0 positivos, 1 neutro e 1 negativo.", texto)


def _analise(dias, snapshot=None, texto="Análise geral", analise_id=None):
    return {
        "id": analise_id,
        "created_at": (INICIO + timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S"),
        "analysis_text": texto,
        "temas": snapshot,
    }


class EscolherAnaliseAnteriorTests(unittest.TestCase):
    def setUp(self):
        # Um áudio por dia, do dia 1 ao 10.
        self.audios = [_audio(i, criado=INICIO + timedelta(days=i)) for i in range(1, 11)]
        self.ids = [audio.audio_id for audio in self.audios]

    def _escolher(self, analises, recorte=Recorte()):
        return rel.escolher_analise_anterior(analises, recorte, self.audios, self.ids)

    def test_snapshot_gives_the_covered_audios(self):
        anterior = self._escolher([_analise(3.5, {"audios": [1, 2, 3], "recorte": {}}, analise_id=7)])

        self.assertEqual(
            (anterior.audios_cobertos, anterior.reconstruida, anterior.analise_id),
            (frozenset({1, 2, 3}), False, 7),
        )

    def test_analysis_of_another_qr_or_origin_is_skipped_but_the_period_does_not_matter(self):
        outra = {"audios": [1, 2, 3, 4, 5], "recorte": Recorte(qr=("Centro",)).como_dict()}
        mesma = {"audios": [1, 2], "recorte": Recorte(periodo=("2026-10-01", "2026-10-02")).como_dict()}

        anterior = self._escolher([_analise(5.5, outra), _analise(2.5, mesma)])

        self.assertEqual(anterior.audios_cobertos, frozenset({1, 2}))

    def test_a_regeneration_of_the_same_wave_is_skipped(self):
        regenerada = {"audios": self.ids, "recorte": {}}
        primeira = {"audios": [1, 2, 3, 4, 5], "recorte": {}}

        anterior = self._escolher([_analise(10.5, regenerada), _analise(5.5, primeira)])

        self.assertEqual(anterior.audios_cobertos, frozenset({1, 2, 3, 4, 5}))

    def test_old_analysis_is_rebuilt_from_the_creation_dates(self):
        anterior = self._escolher([_analise(4.5)])

        self.assertEqual((anterior.audios_cobertos, anterior.reconstruida), (frozenset({1, 2, 3, 4}), True))

    def test_old_analysis_with_a_filter_cannot_be_rebuilt(self):
        filtrada = _analise(6.5, texto="**Recorte analisado:** QR code Centro · 5 de 10 áudios do projeto.\n\nTexto")

        self.assertIsNone(self._escolher([filtrada, _analise(3.5)]))

    def test_old_analysis_does_not_serve_a_filtered_report(self):
        self.assertIsNone(self._escolher([_analise(4.5)], Recorte(qr=("Centro",))))

    def test_no_previous_analysis(self):
        self.assertIsNone(self._escolher([]))


class DeltaTests(unittest.TestCase):
    def test_previous_and_new_respondents_are_counted_with_their_own_audios(self):
        audios = [
            _audio(1, _tema(MAQUINA), numero=1, criado=INICIO + timedelta(days=1)),
            _audio(2, _tema(ATENDENTE, "positiva"), numero=2, criado=INICIO + timedelta(days=2)),
            # O número 1 volta depois da análise, falando de outro tema.
            _audio(3, _tema(ATENDENTE, "positiva"), numero=1, criado=INICIO + timedelta(days=5)),
            _audio(4, _tema(MAQUINA), numero=3, criado=INICIO + timedelta(days=6)),
        ]
        anterior = AnaliseAnterior(INICIO + timedelta(days=3), frozenset({1, 2}))

        agregacao = _agregar(audios, anterior=anterior)

        delta = agregacao.delta
        self.assertEqual((delta.anteriores, delta.novos, delta.em_ambos), (2, 2, 1))
        maquina = _linha_da_tabela(agregacao, MAQUINA)
        atendente = _linha_da_tabela(agregacao, ATENDENTE)
        self.assertEqual((maquina.anteriores, maquina.novos), (1, 1))
        self.assertEqual((atendente.anteriores, atendente.novos), (1, 1))
        self.assertEqual(agregacao.n_respondentes, 3)
        self.assertIn(
            "3 respondentes (2 da análise de 04/10 e 2 novos, 1 nos dois grupos)",
            rel.cabecalho_markdown(agregacao, "Projeto", Recorte(), 4, "validada"),
        )

    def test_column_needs_five_new_respondents_and_a_trend_twenty(self):
        for novos, exibir, tendencia in ((4, False, False), (5, True, False), (19, True, False), (20, True, True)):
            with self.subTest(novos=novos):
                audios = [_audio(i, _tema(MAQUINA), criado=INICIO + timedelta(days=1)) for i in range(1, 4)]
                audios += [
                    _audio(i, _tema(MAQUINA), criado=INICIO + timedelta(days=10)) for i in range(4, 4 + novos)
                ]
                anterior = AnaliseAnterior(INICIO + timedelta(days=5), frozenset({1, 2, 3}))

                agregacao = _agregar(audios, anterior=anterior)
                tabela = rel.tabela_temas_markdown(agregacao)

                delta = agregacao.delta
                self.assertEqual((delta.novos, delta.exibir, delta.sustenta_tendencia), (novos, exibir, tendencia))
                self.assertEqual("Anteriores → novos |" in tabela, exibir)
                if exibir:
                    self.assertIn("| 3/3 → {0}/{0} |".format(novos), tabela)
                    self.assertEqual("não sustentam tendência" in tabela, not tendencia)
                else:
                    self.assertIn("chegaram 4 respondentes novos, menos que os 5 que a comparação pede", tabela)


class TrechosDeEvidenciaTests(unittest.TestCase):
    def test_up_to_six_one_per_respondent_strongest_text_first(self):
        notas = {1: -0.2, 2: -0.9, 3: -0.5, 4: None, 5: -0.7, 6: -0.3, 7: -0.8, 8: -0.6}
        audios, transcricao = [], []
        for audio_id, nota in notas.items():
            audios.append(_audio(audio_id, _tema(MAQUINA, linhas=(1, 2))))
            transcricao += [
                {"session_id": _sessao(audio_id, audio_id), "segmento_idx": 0, "sentimento_texto": nota},
                {"session_id": _sessao(audio_id, audio_id), "segmento_idx": 1,
                 "sentimento_texto": None if nota is None else 0.1},
            ]

        trechos = _agregar(audios, transcricao_projeto=pd.DataFrame(transcricao)).trechos[MAQUINA]

        self.assertEqual(
            [t.alias for t in trechos], ["Áudio 02", "Áudio 07", "Áudio 05", "Áudio 08", "Áudio 03", "Áudio 06"]
        )
        self.assertEqual({t.linha for t in trechos}, {1})

    def test_only_patterns_and_signals_get_trechos(self):
        audios = [_audio(1, _tema(MAQUINA)), _audio(2, _tema(MAQUINA)), _audio(3, _tema(MAQUINA), _tema(LOTACAO))]

        agregacao = _agregar(audios)

        self.assertEqual(list(agregacao.trechos), [MAQUINA])
        self.assertIn("### {} · sinal, 3/3".format(MAQUINA), rel.trechos_markdown(agregacao))
        self.assertIn("- (Áudio 01, 00:00) negativa: \"Fala 1.\"", rel.trechos_markdown(agregacao))

    def test_trecho_in_the_voice_text_divergence_table_is_marked(self):
        sessao = _sessao(1, 1)
        vad = [
            {"session_id": sessao, "SpeakerName": "Respondente", "start_s": s * 4.0, "end_s": s * 4.0 + 3.5,
             "segmento_idx": s, "dim_valence": -1.0 if s == 0 else 0.01 * s,
             "sentimento_texto": 0.9 if s == 0 else 0.0}
            for s in range(10)
        ]
        audios = [_audio(1, _tema(MAQUINA, "positiva", linhas=(1, 2)), linhas=_linhas(10))]
        audios += [_audio(i, _tema(MAQUINA)) for i in (2, 3)]

        agregacao = _agregar(audios, sinc_projeto=pd.DataFrame(vad))

        trecho = next(t for t in agregacao.trechos[MAQUINA] if t.audio_id == 1)
        self.assertEqual((trecho.linha, trecho.nota_texto, trecho.divergente), (1, 0.9, True))
        self.assertIn("positiva · texto +0,90 · divergência voz × texto", rel.trechos_markdown(agregacao))


class TabelaDeTemasTests(unittest.TestCase):
    def test_rows_show_fraction_percent_band_text_and_voice(self):
        audios = []
        for i in range(1, 65):
            temas = []
            if i <= 19:
                temas.append(_tema(MAQUINA, "negativa" if i <= 17 else "mista"))
            if i in (20, 21):
                temas.append(_tema(LOTACAO))
            audios.append(_audio(i, *temas))

        tabela = rel.tabela_temas_markdown(_agregar(audios))

        self.assertIn(
            "| Equipamentos > Máquina fora de serviço | 19/64 (30%) | Padrão | 17 neg · 2 mistas | "
            "não avaliável (0 respondentes com voz) |",
            tabela,
        )
        self.assertIn("| Ambiente > Lotação | 2/64 (3%) | Menção isolada | 2 neg | — |", tabela)
        self.assertIn(
            "Subtemas da taxonomia sem menção no recorte: Atendimento > Atendente da unidade; "
            "Pagamento > Preço do ciclo.",
            tabela,
        )
        self.assertNotIn("Anteriores → novos", tabela)

    def test_nobody_classified(self):
        self.assertEqual(
            rel.tabela_temas_markdown(_agregar([_audio(1, linhas=())])),
            "Nenhum respondente com fala classificada no recorte.",
        )


class CabecalhoTests(unittest.TestCase):
    def test_small_sample_is_exploratory_and_the_taxonomy_status_shows(self):
        agregacao = _agregar([_audio(i, _tema(MAQUINA)) for i in range(1, 12)])

        texto = rel.cabecalho_markdown(agregacao, "Demonstração", Recorte(), 11, "provisoria", taxonomia_da_ia=True)

        self.assertIn(
            "**Demonstração** · 11 respondentes · taxonomia provisória, gerada pela IA — a validar com a cliente",
            texto,
        )
        self.assertIn("**Leitura exploratória:** 11 respondentes, abaixo de 20.", texto)

    def test_twenty_respondents_validated_and_filtered(self):
        agregacao = _agregar([_audio(i) for i in range(1, 21)])
        recorte = Recorte(qr=("Centro",), descricao="QR code Centro")

        texto = rel.cabecalho_markdown(agregacao, "Smart", recorte, 52, "validada")

        self.assertIn("**Smart** · 20 respondentes · taxonomia validada com a cliente", texto)
        self.assertIn("Recorte: QR code Centro · 20 de 52 áudios do projeto.", texto)
        self.assertNotIn("exploratória", texto)


class SumarioExecutivoTests(unittest.TestCase):
    def test_markdown_heading_until_the_next_section(self):
        texto = "## 1. Sumário Executivo\nTom positivo.\nSaldo bom.\n\n## 2. Perfil Emocional\nOutra coisa."

        self.assertEqual(rel.sumario_executivo(texto), "Tom positivo.\nSaldo bom.")

    def test_lower_headings_inside_the_summary_stay(self):
        texto = "### Sumario executivo\nResumo.\n#### Destaque\nDetalhe.\n### 2. Perfil\nX"

        self.assertEqual(rel.sumario_executivo(texto), "Resumo.\n#### Destaque\nDetalhe.")

    def test_numbered_bold_item_with_text_on_the_same_line(self):
        texto = "1. **Sumário Executivo** — Relato curto e negativo.\nContinua.\n2. **Perfil Emocional** — Voz baixa."

        self.assertEqual(rel.sumario_executivo(texto), "Relato curto e negativo.\nContinua.")

    def test_bold_line_and_horizontal_rule(self):
        self.assertEqual(rel.sumario_executivo("**Sumário Executivo:** Resumo.\n\n**Perfil:** x"), "Resumo.")
        self.assertEqual(rel.sumario_executivo("## Sumário Executivo\nResumo.\n---\nResto"), "Resumo.")

    def test_old_prompt_without_the_title(self):
        self.assertIsNone(rel.sumario_executivo("## 1. Perfil do Entrevistado\nTexto do sumário executivo."))
        self.assertIsNone(rel.sumario_executivo("## 1. Sumário Executivo\n\n## 2. Perfil"))
        self.assertIsNone(rel.sumario_executivo(None))

    def test_long_summary_is_cut(self):
        sumario = rel.sumario_executivo("## Sumário Executivo\n" + "palavra " * 400)

        self.assertTrue(sumario.endswith("…"))
        self.assertLessEqual(len(sumario), 1201)

    def test_count(self):
        textos = ["## Sumário Executivo\nResumo.", "## 1. Perfil\nTexto antigo.", "", None, "  "]

        self.assertEqual(rel.contar_sumarios(textos), (1, 1))


class ApendiceTests(unittest.TestCase):
    def setUp(self):
        self.audios = [_audio(i, _tema(MAQUINA)) for i in range(1, 4)]
        vad = []
        for audio in self.audios:
            for s in range(3):
                vad.append({
                    "session_id": audio.session_id, "SpeakerName": "Respondente", "Text": "Fala {}.".format(s + 1),
                    "start_s": s * 4.0, "end_s": s * 4.0 + 3.5, "segmento_idx": s,
                    "dim_valence": 0.1 * s - 0.05 * audio.audio_id, "dim_arousal": 0.2 * s, "dim_dominance": 0.1,
                    "f0_media": 180.5, "sentimento_texto": 0.6 if s == 0 else -0.4,
                    "emocao_happy": 0.7, "emocao_neutral": 0.1, "emocao_sad": 0.1, "emocao_angry": 0.1,
                })
        self.sinc = pd.DataFrame(vad)
        self.agregacao = _agregar(self.audios, sinc_projeto=self.sinc)

    def test_audios_appear_by_alias_and_phones_only_in_the_legend(self):
        sem_legenda = rel.apendice_markdown(self.agregacao, self.sinc, None, incluir_legenda=False)
        com_legenda = rel.apendice_markdown(self.agregacao, self.sinc, None)

        self.assertIn("Áudio 01", sem_legenda)
        legenda = com_legenda.split("#### Legenda dos áudios", 1)[1].split("#### ", 1)[0]
        resto = com_legenda.replace(legenda, "")
        for audio in self.audios:
            self.assertNotIn(audio.session_id, sem_legenda)
            self.assertNotIn(audio.session_id, resto)
            self.assertIn(audio.session_id, legenda)

    def test_sections_and_decimal_comma(self):
        texto = rel.apendice_markdown(self.agregacao, self.sinc, None, sumarios=(1, 2))

        for secao in (
            "Voz por tema", "Médias acústicas por áudio", "Distribuição de emoções", "Momentos de maior ativação",
            "Ranking de palavras", "Sentimento do texto", "Índice combinado", "Divergências voz × texto",
            "Legenda dos áudios", "Limitações e cortes aplicados",
        ):
            with self.subTest(secao=secao):
                self.assertIn("#### " + secao + "\n", texto)
        self.assertIn("| Recorte inteiro | 9 |", texto)
        self.assertIn("180,500", texto)
        self.assertNotIn("180.500", texto)
        self.assertIn("2 de 3 análises individuais não têm Sumário Executivo", texto)
        self.assertNotIn("Perguntas com maior ativação", texto)

    def test_interview_questions_enter_only_in_interviews(self):
        perguntas = [{"question": "Como foi o atendimento?", "count": 3, "avg_arousal": 0.45, "avg_f0_var": 0.2,
                      "avg_ld_var": 0.1, "example": "\"Foi ótimo\" (Entrevistado, 00:01:02)"}]
        agregacao = _agregar(self.audios, tipo=ENTREVISTA_QUALITATIVA, sinc_projeto=self.sinc)

        texto = rel.apendice_markdown(agregacao, self.sinc, None, perguntas_ativacao=perguntas)

        self.assertIn("#### Perguntas com maior ativação", texto)
        self.assertIn("| Como foi o atendimento? | 3 | +0,45 | +0,20 | +0,10 |\n", texto)

    def test_interview_questions_leave_out_the_highlighted_answer(self):
        # A resposta mais ativada a "Qual seu nome?" é o nome do entrevistado.
        perguntas = [{"question": "Qual seu nome?", "count": 3, "avg_arousal": 0.1, "avg_f0_var": 0.2,
                      "avg_ld_var": 0.1, "example": "\"meu nome é Maria da Silva\" (B, 00:00:02)"}]
        agregacao = _agregar(self.audios, tipo=ENTREVISTA_QUALITATIVA, sinc_projeto=self.sinc)

        texto = rel.apendice_markdown(agregacao, self.sinc, None, perguntas_ativacao=perguntas)

        self.assertIn("| Qual seu nome? | 3 |", texto)
        self.assertNotIn("Maria da Silva", texto)
        self.assertNotIn("Destaque", texto)

    def test_quoted_speech_keeps_its_dots(self):
        self.assertEqual(
            rel._virgula_decimal('| 0.50 | "custa 12.50 reais" | -1.2 |'),
            '| 0,50 | "custa 12.50 reais" | -1,2 |',
        )


class SnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_every_audio_of_the_cut_and_the_table(self):
        audios = [_audio(1, _tema(MAQUINA)), _audio(2, linhas=()), _audio(3, _tema(MAQUINA))]
        recorte = Recorte(
            qr=("Centro",), origem=("whatsapp",), periodo=("2026-10-01", "2026-10-14"), descricao="QR code Centro"
        )

        dados = rel.snapshot(_agregar(audios, recorte=[1, 2]), recorte)

        self.assertEqual(dados["audios"], [1, 2])
        self.assertEqual(dados["respondentes"], 1)
        self.assertEqual(
            dados["temas"],
            [{"subtema": MAQUINA, "respondentes": 1, "faixa": MENCAO_ISOLADA, "polaridades": {"negativa": 1}}],
        )
        lido = Recorte.de_dict(json.loads(json.dumps(dados))["recorte"])
        self.assertTrue(lido.mesmo_qr_e_origem(recorte))
        self.assertEqual((lido.periodo, lido.descricao), (recorte.periodo, recorte.descricao))

    def test_the_snapshot_feeds_the_next_comparison(self):
        audios = [_audio(i, _tema(MAQUINA), criado=INICIO + timedelta(days=i)) for i in range(1, 9)]
        dados = rel.snapshot(_agregar(audios, recorte=[1, 2, 3]), Recorte())
        analise = {"id": 4, "created_at": "2026-10-04 20:00:00", "analysis_text": "x", "temas": dados}

        anterior = rel.escolher_analise_anterior([analise], Recorte(), audios, [a.audio_id for a in audios])

        self.assertEqual(anterior.audios_cobertos, frozenset({1, 2, 3}))
        self.assertEqual(_agregar(audios, anterior=anterior).delta.novos, 5)


class DatasTests(unittest.TestCase):
    def test_database_dates(self):
        self.assertEqual(rel.ler_data("2026-10-14 13:05:22"), datetime(2026, 10, 14, 13, 5, 22))
        self.assertEqual(rel.ler_data("2026-10-14T13:05:22+00:00"), datetime(2026, 10, 14, 13, 5, 22))
        self.assertIsNone(rel.ler_data(""))
        self.assertIsNone(rel.ler_data("ontem"))


if __name__ == "__main__":
    unittest.main()
