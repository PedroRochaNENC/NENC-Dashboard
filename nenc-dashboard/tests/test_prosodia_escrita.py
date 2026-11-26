"""Texto do relatório por tema: chamadas de capítulo e de síntese, aplicação e verificação
(docs/SPEC_RELATORIO_POR_TEMA.md, §5.2, §5.3, §7 e §14; Fase 2).

A IA é falsa: lê da mensagem os ids dos subcapítulos e das falas candidatas e
devolve o JSON do esquema. O documento é o da rede fictícia "Lava Já" de
tests/test_prosodia_capitulos.py.
"""

import copy
import json
import re
import unittest
from unittest.mock import MagicMock, patch

from tests.test_prosodia_capitulos import CABECALHO, CONSOLIDACAO, PRESERVAR, TAXONOMIA, _cenario
from utils import ai_provider
from utils import prosodia_capitulos as capitulos
from utils import prosodia_escrita as escrita
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel

_SUB = re.compile(r"^- (c\d+\.s\d+) ·", re.MULTILINE)
_FALA = re.compile(r"^- (t\d+) · [^·]+ · [^·]+ · (c\d+\.s\d+|sem subcapítulo)", re.MULTILINE)


def resposta_do_capitulo(mensagem: str) -> dict:
    """O que uma IA bem-comportada devolve para a mensagem de um capítulo."""
    subs = _SUB.findall(mensagem)
    falas = _FALA.findall(mensagem)
    primeira = subs[0] if subs else None
    resposta = {
        "texto": "",
        "falas": [],
        "subcapitulos": [
            {
                "id": sid,
                "o_que_foi_dito": "O relato descreve o problema com a máquina e o que aconteceu depois.",
                "critica": "A máquina para e o cliente perde o ciclo.",
                "falas": [t for t, s in falas if s == sid][:2],
            }
            for sid in subs
        ],
        "hipotese": None,
        "recomendacao": None,
        "acompanhar": None,
    }
    if "menção isolada" in mensagem:
        resposta["texto"] = "Um cliente reclama da falta de vaga perto da loja no fim de semana."
        resposta["falas"] = [falas[0][0]] if falas else []
    if "fora da taxonomia validada" in mensagem:
        resposta["texto"] = "Reúne elogios gerais à loja."
    if "Capítulo em padrão" in mensagem and primeira:
        resposta["recomendacao"] = {
            "tipo": "Higiene", "driver": "Retenção", "area": "Manutenção",
            "texto": "Avisar no app a máquina parada antes do pagamento.", "como_medir": "Menções na próxima análise.",
            "responde_a": [primeira],
        }
    if "Capítulo sem recomendação" in mensagem and primeira:
        resposta["acompanhar"] = {
            "texto": "Observar se a máquina parada segue nas próximas análises.",
            "como_investigar": "Perguntar em que horário a máquina parou.",
            "porque": [primeira],
        }
    return resposta


RESPOSTA_DA_SINTESE = {
    "resumo": "A máquina parada domina o capítulo 1 (8/12) e é a reclamação que puxa o ranking.",
    "termometro": "O saldo reflete as críticas do capítulo 1.",
    "transversais": [
        {"texto": "O atrito pode indicar falha de processo.", "como_validar": "Cruzar com o tempo de reparo.",
         "capitulos": ["c1", "c2"]},
        {"texto": "Leitura com um capítulo só.", "como_validar": "—", "capitulos": ["c1"]},
    ],
    "abertura_reclamacoes": "As reclamações se concentram na máquina parada (capítulo 1).",
    "matriz": [{"chave": "vinculo", "perfil": "Clientes satisfeitos.", "acao": "Pedir indicação."}],
}


class IaDeTexto:
    """O gerar do relatório: capítulo e síntese pelo esquema pedido."""

    def __init__(self, capitulo=resposta_do_capitulo, sintese=None):
        self.capitulo = capitulo
        self.sintese = sintese or (lambda mensagem: copy.deepcopy(RESPOSTA_DA_SINTESE))
        self.chamadas = []

    def __call__(self, **kwargs):
        self.chamadas.append(kwargs)
        if kwargs["schema"] is escrita.ESQUEMA_SINTESE:
            dados = self.sintese(kwargs["user_prompt"])
        else:
            dados = self.capitulo(kwargs["user_prompt"])
        return {"text": json.dumps(dados), "json": dados, "citations": [{"file_id": "f1", "filename": "lit.pdf"}],
                "search": {"searched": True}}


class _Base(unittest.TestCase):
    def setUp(self):
        _, self.agregacao = _cenario()
        self.doc = capitulos.montar_relatorio(self.agregacao, TAXONOMIA, CONSOLIDACAO, CABECALHO)

    def _escrever(self, ia):
        return escrita.escrever_relatorio_por_tema(self.doc, ia, contexto={"nome": "Lava Já"}, preservar=PRESERVAR)

    def _cap(self, tema):
        return next(c for c in self.doc["capitulos"] if c["tema"] == tema)


class EscritaTests(_Base):
    def test_fills_the_ai_fields_and_keeps_the_contract(self):
        resultado = self._escrever(IaDeTexto())

        self.assertEqual(capitulos.erros(self.doc), [])
        self.assertEqual(resultado.problemas, ["Síntese: leitura transversal descartada: precisa de dois capítulos ou mais"])
        for cap in self.doc["capitulos"]:
            for sub in cap["subcapitulos"]:
                self.assertTrue(sub["o_que_foi_dito"])
        self.assertTrue(self._cap("Estacionamento")["texto"])
        self.assertTrue(self._cap(capitulos.TITULO_OUTROS)["texto"])
        self.assertTrue(self.doc["resumo"])
        self.assertEqual(len(self.doc["transversais"]), 1)
        self.assertEqual(resultado.citacoes, [{"file_id": "f1", "filename": "lit.pdf"}])

    def test_one_call_per_chapter_and_one_synthesis_with_schema_and_limits(self):
        ia = IaDeTexto()

        resultado = self._escrever(ia)

        capitulos_ = [c for c in ia.chamadas if c["schema"] is escrita.ESQUEMA_CAPITULO]
        sintese = [c for c in ia.chamadas if c["schema"] is escrita.ESQUEMA_SINTESE]
        self.assertEqual(len(capitulos_), len(self.doc["capitulos"]))
        self.assertEqual(len(sintese), 1)
        self.assertEqual(resultado.chamadas, len(ia.chamadas))
        self.assertEqual({c["max_tokens"] for c in capitulos_}, {prompts.MAX_TOKENS_CAPITULO})
        self.assertEqual(sintese[0]["max_tokens"], prompts.MAX_TOKENS_SINTESE)
        self.assertEqual({c["temperature"] for c in ia.chamadas}, {prompts.TEMPERATURA_ANALISE_GERAL})
        self.assertEqual(capitulos_[0]["system_prompt"], prompts.sistema_capitulo(None))

    def test_exploratory_reading_has_no_recommendation_and_padrao_gets_to_follow_up(self):
        self._escrever(IaDeTexto())

        self.assertTrue(all(c["recomendacao"] is None for c in self.doc["capitulos"]))
        equipamentos = self._cap("Equipamentos")
        self.assertIsNotNone(equipamentos["acompanhar"])
        self.assertIsNone(self._cap("Estacionamento")["acompanhar"])

    def test_complaint_critique_comes_from_the_subchapter(self):
        self._escrever(IaDeTexto())

        item = self.doc["reclamacoes"]["itens"][0]
        self.assertEqual(item["critica"], "A máquina para e o cliente perde o ciclo.")

    def test_message_carries_only_the_chapter_evidence(self):
        ia = IaDeTexto()
        self._escrever(ia)

        mensagem = next(c["user_prompt"] for c in ia.chamadas if "Capítulo 3 · Estacionamento" in c["user_prompt"])
        self.assertIn("Nunca tem vaga", mensagem)
        self.assertNotIn("máquina travou", mensagem)
        self.assertIn("não leva hipótese, recomendação", mensagem)

    def test_fala_outside_the_candidates_is_dropped_and_asks_once_more(self):
        tentativas = []

        def capitulo(mensagem):
            resposta = resposta_do_capitulo(mensagem)
            if "Capítulo 1 ·" in mensagem:
                tentativas.append(mensagem)
                if len(tentativas) == 1:
                    resposta["subcapitulos"][0]["falas"] = ["t999"]
            return resposta

        resultado = self._escrever(IaDeTexto(capitulo))

        self.assertEqual(len(tentativas), 2)
        self.assertIn("## Correção", tentativas[1])
        self.assertIn("fala t999 não é candidata", tentativas[1])
        self.assertFalse(any(p.startswith("Capítulo 1") for p in resultado.problemas))
        falas = self._cap("Equipamentos")["subcapitulos"][0]["falas"]
        self.assertTrue(falas and all(f in self.doc["trechos"] for f in falas))

    def test_second_failure_is_recorded_and_not_retried_again(self):
        chamadas = []

        def capitulo(mensagem):
            resposta = resposta_do_capitulo(mensagem)
            if "Capítulo 2 ·" in mensagem:
                chamadas.append(1)
                resposta["subcapitulos"][0]["o_que_foi_dito"] = "A valência da voz cai no suporte."
            return resposta

        resultado = self._escrever(IaDeTexto(capitulo))

        self.assertEqual(len(chamadas), 2)
        self.assertTrue(any("Capítulo 2" in p and "termo técnico" in p for p in resultado.problemas))
        self.assertIn(resultado.problemas[0], self.doc["problemas"])


class AplicarTests(_Base):
    def _resposta(self, tema):
        cap = self._cap(tema)
        mensagem = escrita.mensagem_do_capitulo(self.doc, cap)
        return cap, resposta_do_capitulo(mensagem)

    def test_recommendation_is_dropped_where_the_band_does_not_allow(self):
        cap, resposta = self._resposta("Equipamentos")
        resposta["recomendacao"] = {"tipo": "Higiene", "driver": "Retenção", "area": "x", "texto": "x",
                                    "como_medir": "x", "responde_a": [cap["subcapitulos"][0]["id"]]}

        escrito, erros = escrita.aplicar_capitulo(self.doc, cap, resposta)

        self.assertIsNone(escrito["recomendacao"])
        self.assertIn("recomendação descartada: a faixa do capítulo não permite", erros)
        # A cópia: o capítulo original não muda.
        self.assertEqual(cap["subcapitulos"][0]["o_que_foi_dito"], "")

    def test_hypothesis_only_on_an_isolated_subchapter_is_rejected(self):
        cap, resposta = self._resposta("Equipamentos")
        pequeno = cap["subcapitulos"][1]
        self.assertLessEqual(pequeno["respondentes"], 2)
        resposta["hipotese"] = {"texto": "x", "como_validar": "y", "apoia_em": [pequeno["id"], "c9.s9"]}

        escrito, erros = escrita.aplicar_capitulo(self.doc, cap, resposta)

        self.assertIsNone(escrito["hipotese"])
        self.assertIn("a hipótese se apoia só em subcapítulo com 1 ou 2 respondentes", erros)
        self.assertTrue(any("c9.s9" in e for e in erros))

    def test_two_falas_from_the_same_audio_count_once(self):
        cap, resposta = self._resposta("Equipamentos")
        candidata = cap["candidatas"][0]
        resposta["subcapitulos"][0]["falas"] = [candidata, candidata]

        escrito, _ = escrita.aplicar_capitulo(self.doc, cap, resposta)

        self.assertEqual(escrito["subcapitulos"][0]["falas"], [candidata])


class VerificarTests(_Base):
    def _cap_escrito(self, texto, onde="o_que_foi_dito"):
        cap = copy.deepcopy(self._cap("Equipamentos"))
        for sub in cap["subcapitulos"]:
            sub["o_que_foi_dito"] = "Relato concreto do que aconteceu."
            if sub.get("critica") is not None:
                sub["critica"]["resumo"] = "A máquina para no meio do ciclo."
        if onde == "critica":
            cap["subcapitulos"][0]["critica"]["resumo"] = texto
        else:
            cap["subcapitulos"][0]["o_que_foi_dito"] = texto
        return cap

    def test_fraction_from_the_ficha_passes_and_an_invented_one_fails(self):
        erros, _ = escrita.verificar_capitulo(self.doc, self._cap_escrito("5/12 citam a máquina parada."))
        self.assertEqual(erros, [])
        erros, _ = escrita.verificar_capitulo(self.doc, self._cap_escrito("7/12 citam a máquina parada."))
        self.assertTrue(any("7/12" in e for e in erros))

    def test_audio_from_another_chapter_fails(self):
        erros, _ = escrita.verificar_capitulo(self.doc, self._cap_escrito("Como no Áudio 12, falta vaga."))
        self.assertTrue(any("Áudio 12" in e for e in erros))

    def test_technical_voice_term_fails_and_filler_is_a_warning(self):
        erros, avisos = escrita.verificar_capitulo(
            self.doc, self._cap_escrito("De modo geral, a ativação sobe quando a máquina trava.")
        )
        self.assertTrue(any("ativação" in e for e in erros))
        self.assertTrue(any("enchimento" in a for a in avisos))

    def test_person_name_in_critique_is_a_warning(self):
        _, avisos = escrita.verificar_capitulo(
            self.doc, self._cap_escrito("O atendente Marcos não ajudou a destravar a máquina.", "critica"), PRESERVAR
        )
        self.assertTrue(any("nome de uma pessoa" in a for a in avisos))

    def test_empty_said_text_is_an_error(self):
        erros, _ = escrita.verificar_capitulo(self.doc, self._cap_escrito(""))
        self.assertTrue(any("falta o que foi dito" in e for e in erros))


class VerificarSinteseTests(_Base):
    def _sintese(self, resumo, termometro="O saldo vem do capítulo 1."):
        self.doc["resumo"] = resumo
        self.doc["termometro"]["leitura"] = termometro
        return escrita.verificar_sintese(self.doc, PRESERVAR)

    def test_thermometer_fraction_only_in_its_own_reading(self):
        # Números que não coincidem com nenhuma fração de capítulo: a coincidência fica liberada.
        self.doc["termometro"].update(positivos=7, neutros=3, negativos=1, avaliaveis=11)
        t = self.doc["termometro"]
        fracao = "{}/{}".format(t["positivos"], t["avaliaveis"])
        erros, _ = self._sintese("O capítulo 1 tem {} respondentes positivos.".format(fracao))
        self.assertTrue(any("é do Termômetro" in e for e in erros))
        erros, _ = self._sintese("O capítulo 1 tem {} de {} respondentes positivos.".format(t["positivos"], t["avaliaveis"]))
        self.assertTrue(any("é do Termômetro" in e for e in erros))
        erros, _ = self._sintese("Sustentado por {} relatos positivos entre {} avaliados (capítulo 1).".format(
            t["positivos"], t["avaliaveis"]))
        self.assertTrue(any("são do Termômetro" in e for e in erros))
        erros, _ = self._sintese("A máquina parada domina o capítulo 1 (8/12).", "{} positivos.".format(fracao))
        self.assertEqual(erros, [])

    def test_report_names_are_not_taken_for_people(self):
        _, avisos = self._sintese("O NencBoost mostra o Termômetro de Experiência puxado por Equipamentos e Máquina parada.")
        self.assertEqual(avisos, [])


class DestinoTests(_Base):
    def test_band_and_exploratory_reading_decide_the_destination(self):
        doc = copy.deepcopy(self.doc)
        padrao = self._cap("Equipamentos")
        self.assertEqual(escrita.destino(doc, padrao), escrita.Destino(True, False, True, True))
        doc["cabecalho"]["exploratoria"] = False
        self.assertEqual(escrita.destino(doc, padrao), escrita.Destino(True, True, False, False))
        self.assertEqual(escrita.destino(doc, self._cap("Estacionamento")), escrita.Destino(False, False, False, False))
        self.assertEqual(escrita.destino(doc, self._cap(capitulos.TITULO_OUTROS)), escrita.Destino(False, False, False, False))


class ProvedorTests(unittest.TestCase):
    ESQUEMA = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {"a": {"type": "string"}}}

    def test_reads_json_with_code_fence_and_rejects_off_schema(self):
        self.assertEqual(ai_provider.ler_json('```json\n{"a": "x"}\n```', self.ESQUEMA), ({"a": "x"}, ""))
        dados, motivo = ai_provider.ler_json('{"b": 1}', self.ESQUEMA)
        self.assertIsNone(dados)
        self.assertIn("a", motivo)

    def test_other_providers_get_the_schema_in_the_prompt_and_one_more_try(self):
        respostas = iter(["não sei", '{"a": "ok"}'])
        with patch.object(ai_provider, "chat_completion", side_effect=lambda *a, **k: next(respostas)) as chat:
            resultado = ai_provider.generate_analysis("groq", "m", "sistema", "pergunta", schema=self.ESQUEMA, client="c")

        self.assertEqual(resultado["json"], {"a": "ok"})
        self.assertEqual(chat.call_count, 2)
        self.assertIn("JSON Schema", chat.call_args_list[0].args[2])
        self.assertIn("## Correcao", chat.call_args_list[1].args[3][0]["content"])
        self.assertEqual(chat.call_args_list[0].kwargs["client"], "c")

    def test_invalid_twice_raises(self):
        with patch.object(ai_provider, "chat_completion", return_value="nada"):
            with self.assertRaises(ai_provider.JsonInvalido):
                ai_provider.generate_analysis("groq", "m", "s", "u", schema=self.ESQUEMA)

    def test_openai_uses_strict_structured_output_and_the_given_client(self):
        cliente = MagicMock()
        bloco = MagicMock(type="output_text", text='{"a": "x"}', annotations=[])
        cliente.responses.create.return_value = MagicMock(output=[MagicMock(type="message", content=[bloco])])

        resultado = ai_provider.generate_analysis("openai", "gpt-4.1", "s", "u", schema=self.ESQUEMA, client=cliente)

        pedido = cliente.responses.create.call_args.kwargs
        self.assertEqual(pedido["text"]["format"]["type"], "json_schema")
        self.assertTrue(pedido["text"]["format"]["strict"])
        self.assertEqual(resultado["json"], {"a": "x"})

    def test_response_schemas_follow_the_strict_rules(self):
        def conferir(esquema, caminho="raiz"):
            if "anyOf" in esquema:
                for parte in esquema["anyOf"]:
                    conferir(parte, caminho)
                return
            if esquema.get("type") == "object":
                self.assertIs(esquema.get("additionalProperties"), False, caminho)
                self.assertEqual(sorted(esquema["required"]), sorted(esquema["properties"]), caminho)
                for nome, sub in esquema["properties"].items():
                    conferir(sub, caminho + "/" + nome)
            if esquema.get("type") == "array":
                conferir(esquema["items"], caminho + "[]")

        conferir(escrita.ESQUEMA_CAPITULO)
        conferir(escrita.ESQUEMA_SINTESE)


if __name__ == "__main__":
    unittest.main()
