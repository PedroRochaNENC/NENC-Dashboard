"""Classificação das falas de cada áudio na taxonomia do projeto.

A contagem de temas da Análise Geral sai do código, a partir desta
classificação. Por isso a resposta da IA não é aceita como vem: rótulo fora da
taxonomia vira emergente, linha que não existe é descartada, tema sem trecho
cai, e na entrevista a fala do entrevistador não conta como menção. A IA aqui
é sempre simulada.
"""

import json
import threading
import unittest
from unittest.mock import patch

import pandas as pd

from utils import prosodia_temas as temas
from utils.prosodia_project_types import ENTREVISTA_QUALITATIVA, PESQUISA_OPINIAO
from utils.prosodia_temas import (
    AudioParaClassificar,
    Linha,
    RespostaInvalida,
    chave_classificacao,
    classificar_audio,
    classificar_audios,
    contar_emergentes,
    linhas_da_transcricao,
    parse_taxonomia,
    propor_taxonomia,
)

TAXONOMIA = parse_taxonomia(
    "Equipamentos > Máquina fora de serviço: parada, quebrada ou em manutenção\n"
    "Atendimento > Atendente da unidade\n"
    "Ambiente > Lotação: espera por máquina livre"
)


class IaFalsa:
    """Responde sempre o mesmo texto (ou uma lista, uma resposta por chamada)."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self._trava = threading.Lock()

    def __call__(self, sistema, usuario, max_tokens):
        with self._trava:
            self.chamadas.append((sistema, usuario, max_tokens))
            if len(self.respostas) > 1:
                return self.respostas.pop(0)
            return self.respostas[0]


def _resposta(**campos):
    campos.setdefault("locutor_respondente", "Respondente")
    campos.setdefault("temas", [])
    campos.setdefault("emergentes", [])
    return json.dumps(campos, ensure_ascii=False)


def _linhas(*falas, locutor="Respondente"):
    """Linhas numeradas a partir de 1; fala como tupla (locutor, texto) troca o locutor."""
    linhas = []
    for numero, fala in enumerate(falas, 1):
        quem, texto = fala if isinstance(fala, tuple) else (locutor, fala)
        linhas.append(Linha(numero, quem, texto, inicio_s=float(numero * 5), segmento_idx=numero + 10))
    return linhas


class LinhasDaTranscricaoTests(unittest.TestCase):
    def test_numbers_the_lines_and_keeps_segment_and_time(self):
        transcricao = pd.DataFrame(
            {
                "SpeakerName": ["Entrevistado", None, "Entrevistado", "Entrevistado"],
                "Text": ["A máquina   parou.", "  ", "Perdi o ciclo.", "A atendente ajudou."],
                "seconds": [0.0, 2.0, 4.256, 3725.0],
                "end_s": [1.5, None, 6.0, 3730.0],
                "segmento_idx": pd.array([7, 8, 9, 10], dtype="Int64"),
            }
        )

        linhas = linhas_da_transcricao(transcricao)

        self.assertEqual([l.numero for l in linhas], [1, 2, 3])
        self.assertEqual(
            linhas[0], Linha(1, "Entrevistado", "A máquina parou.", 0.0, 1.5, 7)
        )
        self.assertEqual((linhas[1].inicio_s, linhas[1].segmento_idx), (4.26, 9))
        self.assertEqual(linhas[0].para_prompt(), "[1] (00:00) Entrevistado: A máquina parou.")
        self.assertEqual(linhas[2].para_prompt(), "[3] (1:02:05) Entrevistado: A atendente ajudou.")

    def test_lines_of_the_same_segment_count_once(self):
        # Transcrição tirada do Sincronizado: uma linha por segmento do VAD.
        transcricao = pd.DataFrame(
            {
                "SpeakerName": ["Respondente"] * 3,
                "Text": ["Fala um.", "Fala um.", "Fala dois."],
                "seconds": [0.0, 1.0, 2.0],
                "segmento_idx": pd.array([0, 0, 1], dtype="Int64"),
            }
        )

        self.assertEqual([l.texto for l in linhas_da_transcricao(transcricao)], ["Fala um.", "Fala dois."])

    def test_old_files_without_segment_index_keep_the_time(self):
        transcricao = pd.DataFrame(
            {
                "SpeakerName": ["Respondente", float("nan")],
                "Text": ["Gostei.", "Voltaria."],
                "seconds": [float("nan"), 3.0],
                "start_s": [1.25, None],
            }
        )

        linhas = linhas_da_transcricao(transcricao)

        self.assertEqual(
            linhas,
            [
                Linha(1, "Respondente", "Gostei.", 1.25, None, None),
                Linha(2, "?", "Voltaria.", 3.0, None, None),
            ],
        )
        self.assertEqual(Linha(3, "?", "Sem tempo.").para_prompt(), "[3] ?: Sem tempo.")

    def test_empty_transcript(self):
        self.assertEqual(linhas_da_transcricao(pd.DataFrame()), [])
        self.assertEqual(linhas_da_transcricao(None), [])


class ChaveDaClassificacaoTests(unittest.TestCase):
    def test_changes_with_whatever_the_classification_depends_on(self):
        linhas = _linhas("A máquina parou.")
        base = chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, linhas)

        self.assertEqual(base, chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, list(linhas)))
        outra_taxonomia = parse_taxonomia("Equipamentos > Máquina fora de serviço")
        self.assertNotEqual(base, chave_classificacao(outra_taxonomia, PESQUISA_OPINIAO, linhas))
        self.assertNotEqual(base, chave_classificacao(TAXONOMIA, ENTREVISTA_QUALITATIVA, linhas))
        self.assertNotEqual(base, chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, _linhas("Outra fala.")))
        with patch.object(temas, "VERSAO_CLASSIFICACAO", "teste"):
            self.assertNotEqual(base, chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, linhas))

    def test_reordering_the_taxonomy_keeps_the_key(self):
        linhas = _linhas("A máquina parou.")
        reordenada = parse_taxonomia(
            "Ambiente > Lotação: espera por máquina livre\n"
            "Atendimento > Atendente da unidade\n"
            "Equipamentos > Máquina fora de serviço: parada, quebrada ou em manutenção"
        )

        self.assertEqual(
            chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, linhas),
            chave_classificacao(reordenada, PESQUISA_OPINIAO, linhas),
        )


class ClassificarAudioTests(unittest.TestCase):
    def _classifica(self, resposta, linhas=None, tipo=PESQUISA_OPINIAO, emergentes=()):
        ia = IaFalsa(resposta)
        linhas = linhas or _linhas(
            "A máquina estava parada de novo.",
            "Perdi o ciclo que já tinha pago.",
            "A Ana, da recepção, me ajudou.",
            "Fim de semana é difícil achar máquina.",
        )
        return classificar_audio(linhas, TAXONOMIA, tipo, list(emergentes), ia), ia

    def test_accepts_labels_without_case_or_accents_and_keeps_taxonomy_order(self):
        resultado, _ = self._classifica(
            _resposta(
                temas=[
                    {"subtema": "ambiente > lotacao", "polaridade": "negativa", "linhas": [4], "evidencia": "fila no fim de semana"},
                    {"subtema": "Equipamentos > Maquina fora de servico", "polaridade": "Negativo", "linhas": [1, 2]},
                ]
            )
        )

        self.assertEqual(
            [(t["subtema"], t["polaridade"]) for t in resultado["temas"]],
            [
                ("Equipamentos > Máquina fora de serviço", "negativa"),
                ("Ambiente > Lotação", "negativa"),
            ],
        )
        self.assertEqual(resultado["temas"][1]["evidencia"], "fila no fim de semana")
        self.assertEqual(
            resultado["temas"][0]["trechos"],
            [
                {"linha": 1, "segmento_idx": 11, "inicio_s": 5.0},
                {"linha": 2, "segmento_idx": 12, "inicio_s": 10.0},
            ],
        )

    def test_unknown_subtheme_becomes_emergent_and_emergent_that_exists_becomes_theme(self):
        resultado, _ = self._classifica(
            _resposta(
                temas=[{"subtema": "Ambiente > Estacionamento", "polaridade": "negativa", "linhas": [4]}],
                emergentes=[
                    {"rotulo": "Atendente da unidade", "polaridade": "positiva", "linhas": [3]},
                    {"rotulo": "Cheiro do sabão", "categoria": "Produtos", "polaridade": "positiva", "linhas": "2"},
                ],
            )
        )

        self.assertEqual([t["subtema"] for t in resultado["temas"]], ["Atendimento > Atendente da unidade"])
        self.assertEqual(
            [(e["rotulo"], e["categoria"], e["polaridade"]) for e in resultado["emergentes"]],
            [("Estacionamento", "Ambiente", "negativa"), ("Cheiro do sabão", "Produtos", "positiva")],
        )

    def test_lines_that_do_not_exist_are_dropped_and_a_theme_without_lines_falls(self):
        resultado, _ = self._classifica(
            _resposta(
                temas=[
                    {"subtema": "Ambiente > Lotação", "polaridade": "negativa", "linhas": [99, "x"]},
                    {"subtema": "Atendimento > Atendente da unidade", "polaridade": "positiva", "linhas": [3, 3, 42]},
                ]
            )
        )

        self.assertEqual(
            [(t["subtema"], [x["linha"] for x in t["trechos"]]) for t in resultado["temas"]],
            [("Atendimento > Atendente da unidade", [3])],
        )

    def test_same_theme_twice_is_merged_and_praise_plus_criticism_is_mixed(self):
        resultado, _ = self._classifica(
            _resposta(
                temas=[
                    {"subtema": "Atendimento > Atendente da unidade", "polaridade": "positiva", "linhas": [3]},
                    {"subtema": "Atendimento > Atendente da unidade", "polaridade": "negativa", "linhas": [1]},
                    {"subtema": "Ambiente > Lotação", "polaridade": "neutra", "linhas": [4]},
                    {"subtema": "Ambiente > Lotação", "polaridade": "negativa", "linhas": [4]},
                    {"subtema": "Equipamentos > Máquina fora de serviço", "polaridade": "???", "linhas": [2]},
                ]
            )
        )

        polaridades = {t["subtema"]: t["polaridade"] for t in resultado["temas"]}
        self.assertEqual(polaridades["Atendimento > Atendente da unidade"], "mista")
        self.assertEqual(polaridades["Ambiente > Lotação"], "negativa")
        self.assertEqual(polaridades["Equipamentos > Máquina fora de serviço"], "neutra")

    def test_lines_per_theme_are_capped(self):
        linhas = _linhas(*["Fala {}.".format(n) for n in range(1, 16)])
        resultado, _ = self._classifica(
            _resposta(temas=[{"subtema": "Ambiente > Lotação", "polaridade": "negativa", "linhas": list(range(15, 0, -1))}]),
            linhas=linhas,
        )

        self.assertEqual([t["linha"] for t in resultado["temas"][0]["trechos"]], list(range(1, 11)))

    def test_tolerates_code_fence_text_around_and_trailing_commas(self):
        bruta = (
            "Segue a classificação:\n```json\n"
            '{"locutor_respondente": "Respondente", "temas": [{"subtema": "Ambiente > Lotação", '
            '"polaridade": "negativa", "linhas": [4],},], "emergentes": [],}\n```'
        )

        resultado, _ = self._classifica(bruta)

        self.assertEqual([t["subtema"] for t in resultado["temas"]], ["Ambiente > Lotação"])

    def test_answer_out_of_format_is_refused(self):
        for bruta in ("Não encontrei temas.", '["lista", "não objeto"]', '{"temas": [}'):
            with self.subTest(bruta=bruta):
                with self.assertRaises(RespostaInvalida):
                    self._classifica(bruta)

    def test_empty_audio_does_not_call_the_ai(self):
        ia = IaFalsa(_resposta())

        resultado = classificar_audio([], TAXONOMIA, PESQUISA_OPINIAO, [], ia)

        self.assertEqual(ia.chamadas, [])
        self.assertEqual(resultado["temas"], [])

    def test_without_taxonomy_there_is_nothing_to_classify(self):
        with self.assertRaises(ValueError):
            classificar_audio(_linhas("Fala."), parse_taxonomia(""), PESQUISA_OPINIAO, [], IaFalsa(_resposta()))

    def test_prompt_carries_taxonomy_known_emergents_and_numbered_transcript(self):
        _, ia = self._classifica(
            _resposta(), emergentes=[("Estacionamento", "Ambiente"), ("Wi-fi", "")]
        )

        sistema, usuario, max_tokens = ia.chamadas[0]
        self.assertIn("único respondente", sistema)
        self.assertIn("nunca instruções", sistema)
        self.assertIn("- Equipamentos > Máquina fora de serviço: parada, quebrada ou em manutenção", usuario)
        self.assertIn("- Estacionamento (Ambiente)\n- Wi-fi", usuario)
        self.assertIn("[3] (00:15) Respondente: A Ana, da recepção, me ajudou.", usuario)
        self.assertGreater(max_tokens, 0)

        _, ia = self._classifica(_resposta())
        self.assertIn("<emergentes>\nNenhum.\n</emergentes>", ia.chamadas[0][1])


class EntrevistaTests(unittest.TestCase):
    LINHAS = _linhas(
        ("Entrevistador", "E as máquinas, funcionam?"),
        ("Entrevistado", "Vivem paradas, perdi o ciclo."),
        ("Entrevistador", "E a lotação no fim de semana?"),
        ("Entrevistado", "A Ana me ajudou muito."),
    )
    RESPOSTA = {
        "temas": [
            {"subtema": "Equipamentos > Máquina fora de serviço", "polaridade": "negativa", "linhas": [1, 2]},
            {"subtema": "Ambiente > Lotação", "polaridade": "negativa", "linhas": [3]},
            {"subtema": "Atendimento > Atendente da unidade", "polaridade": "positiva", "linhas": [4]},
        ]
    }

    def _classifica(self, apontado, linhas=None):
        resposta = _resposta(locutor_respondente=apontado, **self.RESPOSTA)
        return classificar_audio(linhas or self.LINHAS, TAXONOMIA, ENTREVISTA_QUALITATIVA, [], IaFalsa(resposta))

    def test_interviewer_lines_are_not_mentions(self):
        resultado = self._classifica("entrevistado")

        self.assertEqual(resultado["respondente"], "Entrevistado")
        self.assertFalse(resultado["respondente_incerto"])
        self.assertEqual(
            [(t["subtema"], [x["linha"] for x in t["trechos"]]) for t in resultado["temas"]],
            [
                ("Equipamentos > Máquina fora de serviço", [2]),
                ("Atendimento > Atendente da unidade", [4]),
            ],
        )

    def test_without_a_usable_answer_the_label_entrevistado_wins_over_entrevistador(self):
        resultado = self._classifica("Pessoa 2")

        self.assertEqual(resultado["respondente"], "Entrevistado")
        self.assertEqual(len(resultado["temas"]), 2)

    def test_when_the_respondent_is_unknown_every_line_stays(self):
        linhas = [
            Linha(l.numero, "SPEAKER_00" if l.locutor == "Entrevistador" else "SPEAKER_01", l.texto)
            for l in self.LINHAS
        ]

        resultado = self._classifica("", linhas=linhas)

        self.assertIsNone(resultado["respondente"])
        self.assertTrue(resultado["respondente_incerto"])
        self.assertEqual(len(resultado["temas"]), 3)

    def test_the_interview_rule_goes_to_the_prompt(self):
        ia = IaFalsa(_resposta())
        classificar_audio(self.LINHAS, TAXONOMIA, ENTREVISTA_QUALITATIVA, [], ia)

        self.assertIn("pergunta ou comentário do entrevistador não é menção", ia.chamadas[0][0])


class EntrevistaLongaTests(unittest.TestCase):
    def test_long_transcript_goes_in_parts_and_each_part_only_cites_its_lines(self):
        linhas = _linhas(
            "Máquina parada outra vez.",
            "A atendente resolveu.",
            "Lotação no sábado.",
            "Máquina parada no domingo.",
        )
        primeira = _resposta(
            temas=[
                {"subtema": "Equipamentos > Máquina fora de serviço", "polaridade": "negativa", "linhas": [1]},
                {"subtema": "Atendimento > Atendente da unidade", "polaridade": "positiva", "linhas": [2, 4]},
            ]
        )
        segunda = _resposta(
            temas=[
                {"subtema": "Equipamentos > Máquina fora de serviço", "polaridade": "negativa", "linhas": [4, 1]},
                {"subtema": "Ambiente > Lotação", "polaridade": "negativa", "linhas": [3]},
            ]
        )
        ia = IaFalsa(primeira, segunda)

        with patch.object(temas, "_PALAVRAS_POR_PARTE", 8):
            resultado = classificar_audio(linhas, TAXONOMIA, PESQUISA_OPINIAO, [], ia)

        self.assertEqual(len(ia.chamadas), 2)
        self.assertIn("[1]", ia.chamadas[0][1])
        self.assertNotIn("[3]", ia.chamadas[0][1])
        self.assertIn("[3]", ia.chamadas[1][1])
        self.assertEqual(
            {t["subtema"]: [x["linha"] for x in t["trechos"]] for t in resultado["temas"]},
            {
                # A linha 4 da primeira parte e a 1 da segunda não existiam ali.
                "Equipamentos > Máquina fora de serviço": [1, 4],
                "Atendimento > Atendente da unidade": [2],
                "Ambiente > Lotação": [3],
            },
        )


class ClassificarAudiosTests(unittest.TestCase):
    def _audios(self, n):
        return [
            AudioParaClassificar(audio_id, tuple(_linhas("Fala do áudio {}.".format(audio_id))))
            for audio_id in range(1, n + 1)
        ]

    def test_reuses_what_is_saved_and_classifies_the_rest(self):
        audios = self._audios(3)
        chave_do_1 = chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, audios[0].linhas)
        ia = IaFalsa(_resposta(temas=[{"subtema": "Ambiente > Lotação", "polaridade": "negativa", "linhas": [1]}]))
        salvos, progresso = {}, []

        resumo = classificar_audios(
            audios,
            TAXONOMIA,
            PESQUISA_OPINIAO,
            {1: chave_do_1, 2: "chave-antiga"},
            [],
            ia,
            lambda audio_id, chave, classificacao: salvos.update({audio_id: (chave, classificacao)}),
            ao_avancar=lambda feitos, total: progresso.append((feitos, total)),
            paralelo=1,
        )

        self.assertEqual(resumo.reaproveitados, [1])
        self.assertEqual(sorted(resumo.classificados), [2, 3])
        self.assertEqual(resumo.falhas, {})
        self.assertEqual(len(ia.chamadas), 2)
        self.assertEqual(
            salvos[2][0], chave_classificacao(TAXONOMIA, PESQUISA_OPINIAO, audios[1].linhas)
        )
        self.assertEqual(salvos[3][1]["temas"][0]["subtema"], "Ambiente > Lotação")
        self.assertEqual(progresso, [(0, 2), (1, 2), (2, 2)])

    def test_a_failure_is_recorded_and_the_others_are_saved(self):
        def responder(sistema, usuario, max_tokens):
            return "não é JSON" if "áudio 2" in usuario else _resposta()

        salvos = []
        resumo = classificar_audios(
            self._audios(3), TAXONOMIA, PESQUISA_OPINIAO, {}, [], responder,
            lambda audio_id, chave, classificacao: salvos.append(audio_id), paralelo=2,
        )

        self.assertEqual(sorted(salvos), [1, 3])
        self.assertEqual(list(resumo.falhas), [2])
        self.assertIn("JSON", resumo.falhas[2])

    def test_parallel_calls_save_every_audio(self):
        salvos = []
        resumo = classificar_audios(
            self._audios(12), TAXONOMIA, PESQUISA_OPINIAO, {}, [], IaFalsa(_resposta()),
            lambda audio_id, chave, classificacao: salvos.append(audio_id), paralelo=4,
        )

        self.assertEqual(sorted(salvos), list(range(1, 13)))
        self.assertEqual(sorted(resumo.classificados), list(range(1, 13)))

    def test_an_error_while_saving_stops_the_batch(self):
        def salvar(audio_id, chave, classificacao):
            raise RuntimeError("banco fora do ar")

        with self.assertRaises(RuntimeError):
            classificar_audios(
                self._audios(3), TAXONOMIA, PESQUISA_OPINIAO, {}, [], IaFalsa(_resposta()), salvar, paralelo=1
            )

    def test_without_taxonomy_nothing_runs(self):
        with self.assertRaises(ValueError):
            classificar_audios(
                self._audios(1), parse_taxonomia(""), PESQUISA_OPINIAO, {}, [], IaFalsa(_resposta()),
                lambda *args: None,
            )


class ContarEmergentesTests(unittest.TestCase):
    def test_counts_audios_merging_case_and_accents(self):
        classificacoes = [
            {"emergentes": [{"rotulo": "Estacionamento", "categoria": "Ambiente"}, {"rotulo": "estacionamento"}]},
            {"emergentes": [{"rotulo": "ESTACIONAMENTO"}, {"rotulo": "Cheiro do sabão", "categoria": "Produtos"}]},
            {"emergentes": [{"rotulo": "Wi-fi"}, {"rotulo": "Cheiro do sabao"}, "texto solto", {"rotulo": ""}]},
            {},
            None,
        ]

        self.assertEqual(
            contar_emergentes(classificacoes),
            [
                ("Cheiro do sabão", "Produtos", 2),
                ("Estacionamento", "Ambiente", 2),
                ("Wi-fi", "", 1),
            ],
        )


class ProporTaxonomiaTests(unittest.TestCase):
    def test_returns_the_parsed_taxonomy_ignoring_lines_out_of_format(self):
        ia = IaFalsa(
            "Aqui está a taxonomia:\n"
            "- Equipamentos > Máquina fora de serviço: parada ou quebrada\n"
            "Atendimento > Atendente da unidade: ajuda e cordialidade\n"
        )

        taxonomia = propor_taxonomia(
            [_linhas("A máquina parou."), [], _linhas("A atendente ajudou.")],
            PESQUISA_OPINIAO,
            {"nome": "Lavanderia", "especialidade": "Rede self-service", "problemas": ""},
            ia,
        )

        self.assertEqual(
            [s.rotulo for s in taxonomia.subtemas],
            ["Equipamentos > Máquina fora de serviço", "Atendimento > Atendente da unidade"],
        )
        sistema, usuario, _ = ia.chamadas[0]
        self.assertIn("recado de um único respondente", sistema)
        self.assertIn("Projeto: Lavanderia\nContexto: Rede self-service\n</contexto>", usuario)
        self.assertIn("(2 de 2 áudios com fala)", usuario)
        self.assertIn("### Áudio 1\nRespondente: A máquina parou.", usuario)

    def test_long_audios_and_many_audios_are_cut(self):
        ia = IaFalsa("Ambiente > Lotação: fila")
        transcricoes = [_linhas("um dois três", "quatro cinco seis", "sete oito nove") for _ in range(3)]

        with patch.object(temas, "_PALAVRAS_POR_AUDIO_NA_PROPOSTA", 6), patch.object(
            temas, "_PALAVRAS_NA_PROPOSTA", 12
        ):
            propor_taxonomia(transcricoes, ENTREVISTA_QUALITATIVA, {}, ia)

        sistema, usuario, _ = ia.chamadas[0]
        self.assertIn("só as falas do entrevistado", sistema)
        self.assertIn("(2 de 3 áudios com fala)", usuario)
        self.assertNotIn("sete oito nove", usuario)
        self.assertIn("Sem contexto cadastrado.", usuario)

    def test_nothing_to_read_or_nothing_usable_back(self):
        with self.assertRaises(ValueError):
            propor_taxonomia([[], []], PESQUISA_OPINIAO, {}, IaFalsa("Ambiente > Lotação"))
        with self.assertRaises(RespostaInvalida):
            propor_taxonomia([_linhas("Fala.")], PESQUISA_OPINIAO, {}, IaFalsa("Não sei montar."))


if __name__ == "__main__":
    unittest.main()
