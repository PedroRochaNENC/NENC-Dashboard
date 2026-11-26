"""Capítulos, falas candidatas e reclamações do relatório por tema
(docs/SPEC_RELATORIO_POR_TEMA.md, §3, §5.1 e §14; Fase 1).

O cenário é a rede fictícia de lavanderias "Lava Já", 12 respondentes em duas
unidades, montado pela agregação de verdade (prosodia_relatorio.agregar).
"""

import dataclasses
import unittest
from datetime import datetime, timedelta

import pandas as pd

from utils import prosodia_capitulos as capitulos
from utils import prosodia_relatorio as rel
from utils.prosodia_mascara import FUNCIONARIO, preservar_do_projeto
from utils.prosodia_project_types import PESQUISA_OPINIAO
from utils.prosodia_subtemas import OUTROS_ASSUNTOS, Consolidacao, Grupo
from utils.prosodia_temas import OUTROS, Linha, parse_taxonomia

TAXONOMIA = parse_taxonomia(
    "! Alerta Crítico: furto e assédio\n"
    "Equipamentos: máquinas e secadoras\n"
    "Atendimento: atendentes e suporte\n"
    "Pagamento: cobrança e estorno\n"
    "Estacionamento: vagas\n"
    "Aplicativo"
)
MOEMA = "Moema · LJ-MOE-01"
CENTRO = "Centro · LJ-CEN-02"
PRESERVAR = tuple(preservar_do_projeto("Lava Já — Voz do Cliente", [MOEMA, CENTRO], [s.rotulo for s in TAXONOMIA.subtemas]))

CONSOLIDACAO = Consolidacao(grupos={
    "Equipamentos": [
        Grupo("Máquina parada", ("máquina travou", "fora de serviço")),
        Grupo("Máquinas novas", ("máquinas novas",)),
        Grupo(OUTROS_ASSUNTOS, ("porta da máquina",)),
    ],
    "Atendimento": [Grupo("atendente", ("atendente",)), Grupo("suporte", ("suporte",))],
    "Estacionamento": [Grupo("falta de vaga", ("falta de vaga",))],
})


def _tema(tag, detalhe, polaridade, *linhas, evidencia=""):
    return {
        "subtema": tag,
        "detalhe": detalhe,
        "polaridade": polaridade,
        "evidencia": evidencia or detalhe,
        "trechos": [{"linha": n, "segmento_idx": n, "inicio_s": 5.0 * n} for n in linhas],
    }


def _audio(audio_id, falas, *temas_do_audio, unidade=MOEMA):
    linhas = tuple(
        Linha(i, "Respondente", texto, 5.0 * i, 5.0 * i + 4, i) for i, (texto, _) in enumerate(falas, 1)
    )
    return rel.AudioDoProjeto(
        audio_id,
        "wa_+5511990{:05d}_{}".format(audio_id, audio_id),
        linhas,
        {"respondente": None, "respondente_incerto": False, "temas": list(temas_do_audio), "emergentes": []},
        datetime(2026, 10, 1) + timedelta(hours=audio_id),
        unidade=unidade,
    )


def _transcricao(audios):
    """A nota do texto de cada fala, por segmento, como a transcrição do projeto traz."""
    return pd.DataFrame(
        [
            {"session_id": a.session_id, "segmento_idx": i, "sentimento_texto": nota}
            for a, falas in audios
            for i, (_, nota) in enumerate(falas, 1)
        ]
    )


def _cenario():
    dados = [
        (1, [("A máquina travou no meio do ciclo e perdi o dinheiro da lavagem", -0.8),
             ("No geral gosto muito da loja e sempre volto aqui toda semana", 0.7)],
         [_tema("Equipamentos", "máquina travou", "negativa", 1), _tema(OUTROS, "avaliação geral", "positiva", 2)]),
        (2, [("A máquina travou de novo e ninguém apareceu para ajudar na hora", -0.7),
             ("Sumiu minha bolsa da bancada enquanto eu esperava a secadora terminar", -0.9)],
         [_tema("Equipamentos", "máquina travou", "negativa", 1, 2),
          _tema("Alerta Crítico", "", "negativa", 2, evidencia="relata o sumiço de uma bolsa")]),
        (3, [("Terceira vez que a máquina trava com a roupa molhada dentro dela", -0.6),
             ("Podiam abrir mais cedo no domingo para quem trabalha", -0.1)],
         [_tema("Equipamentos", "máquina travou", "negativa", 1), _tema(OUTROS, "horário", "neutra", 2)]),
        (4, [("A máquina travou com o ciclo pago e o estorno nunca chegou", -0.8),
             ("Mas a máquina fora de serviço foi trocada rápido pela equipe", 0.5)],
         [_tema("Equipamentos", "máquina travou", "negativa", 1), _tema("Equipamentos", "fora de serviço", "positiva", 2)]),
        (5, [("Cheguei e a máquina nove estava fora de serviço sem nenhum aviso", -0.6),
             ("E a outra máquina também travou no meio da centrifugação", -0.5)],
         [_tema("Equipamentos", "fora de serviço", "negativa", 1), _tema("Equipamentos", "máquina travou", "negativa", 2)]),
        (6, [("As máquinas novas daqui lavam rápido e deixam a roupa cheirosa", 0.8),
             ("Gosto da loja, é limpa e tem sempre máquina livre de manhã", 0.6)],
         [_tema("Equipamentos", "máquinas novas", "positiva", 1), _tema(OUTROS, "avaliação geral", "positiva", 2)]),
        (7, [("Gostei muito, tudo ótimo.", 0.9),
             ("Recomendo a loja para os vizinhos porque funciona bem e é barata", 0.7)],
         [_tema("Equipamentos", "máquinas novas", "positiva", 1), _tema(OUTROS, "avaliação geral", "positiva", 2)]),
        (8, [("A porta da máquina emperra e precisa de força para abrir no fim", -0.5)],
         [_tema("Equipamentos", "porta da máquina", "negativa", 1)]),
        (9, [("O atendente Marcos foi ótimo e me ensinou a usar o aplicativo", 0.9)],
         [_tema("Atendimento", "atendente", "positiva", 1)]),
        (10, [("O suporte respondeu rápido quando pedi ajuda pelo WhatsApp da loja", 0.6),
              ("Mas a atendente Juliana demorou dois dias para responder a segunda mensagem", -0.6)],
         [_tema("Atendimento", "suporte", "mista", 1, 2)]),
        (11, [("O suporte ajuda, mas às vezes demora um pouco para responder a mensagem", 0.3)],
         [_tema("Atendimento", "suporte", "mista", 1)]),
        (12, [("Nunca tem vaga para estacionar perto da loja no sábado de manhã", -0.6)],
         [_tema("Estacionamento", "falta de vaga", "negativa", 1)]),
    ]
    pares = []
    for audio_id, falas, temas_do_audio in dados:
        audio = _audio(audio_id, falas, *temas_do_audio, unidade=MOEMA if audio_id <= 6 else CENTRO)
        pares.append((audio, falas))
    audios = [a for a, _ in pares]
    agregacao = rel.agregar(audios, [a.audio_id for a in audios], TAXONOMIA, PESQUISA_OPINIAO, None, _transcricao(pares))
    return audios, agregacao


CABECALHO = capitulos.Cabecalho(
    projeto="Lava Já — Voz do Cliente", recorte="Todos os áudios do projeto", gerado_em="25/11/2026 10:00",
    periodo=("01/10/2026", "24/11/2026"), modelo="gpt-4.1", preservar=PRESERVAR,
    arquivo_do_anexo="Anexo_Tecnico_Lava_Ja_2026-11-25.pdf",
)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audios, cls.agregacao = _cenario()
        cls.doc = capitulos.montar_relatorio(cls.agregacao, TAXONOMIA, CONSOLIDACAO, CABECALHO)
        cls.caps = {c["tema"]: c for c in cls.doc["capitulos"]}
        cls.trechos = cls.doc["trechos"]

    def _texto(self, identificador):
        return self.trechos[identificador]["texto"]


class DocumentoTests(_Base):
    def test_the_document_follows_the_contract(self):
        self.assertEqual(capitulos.erros(self.doc), [])

    def test_chapters_from_most_cited_with_outros_last(self):
        self.assertEqual(
            [c["tema"] for c in self.doc["capitulos"]],
            ["Equipamentos", "Atendimento", "Estacionamento", capitulos.TITULO_OUTROS],
        )
        self.assertEqual([c["numero"] for c in self.doc["capitulos"]], [1, 2, 3, 4])
        self.assertEqual([v["capitulo"] for v in self.doc["visao_geral"]], ["c1", "c2", "c3", "c4"])

    def test_alert_line_is_not_a_chapter_and_not_in_sem_mencao(self):
        self.assertNotIn("Alerta Crítico", self.caps)
        self.assertEqual(self.doc["sem_mencao"], ["Pagamento", "Aplicativo"])
        self.assertEqual(len(self.doc["alertas"]), 1)
        self.assertEqual(self.doc["alertas"][0]["evidencia"], "relata o sumiço de uma bolsa")

    def test_header_and_fields_the_ai_will_write_are_empty(self):
        cab = self.doc["cabecalho"]
        self.assertEqual((cab["respondentes"], cab["audios"], cab["unidades"]), (12, 12, 2))
        self.assertTrue(cab["exploratoria"])
        self.assertEqual(self.doc["resumo"], "")
        self.assertEqual(self.doc["termometro"]["leitura"], "")
        self.assertEqual(self.doc["reclamacoes"]["abertura"], "")
        self.assertTrue(all(s["o_que_foi_dito"] == "" for c in self.doc["capitulos"] for s in c["subcapitulos"]))
        self.assertEqual(self.doc["anexo"]["arquivo"], "Anexo_Tecnico_Lava_Ja_2026-11-25.pdf")
        self.assertLessEqual(len(self.doc["como_ler"]), 3)

    def test_needs_open_tags(self):
        with self.assertRaises(ValueError):
            capitulos.montar_relatorio(self.agregacao, parse_taxonomia("Ambiente > Lotação"), CONSOLIDACAO, CABECALHO)


class AgregacaoPorCapituloTests(_Base):
    def test_tag_count_does_not_change(self):
        linha = next(t for t in self.agregacao.temas if t.subtema == "Equipamentos")
        cap = self.caps["Equipamentos"]
        self.assertEqual((cap["respondentes"], cap["polaridades"]), (linha.respondentes, linha.polaridades))
        self.assertEqual(cap["faixa"], rel.PADRAO)

    def test_group_count_is_the_union_of_its_subthemes(self):
        sub = self.caps["Equipamentos"]["subcapitulos"][0]
        # Respondentes 1 a 5: o 4 e o 5 citaram os dois subtemas do grupo e contam uma vez.
        self.assertEqual((sub["numero"], sub["nome"], sub["respondentes"]), ("1.1", "Máquina parada", 5))
        self.assertEqual(sub["subtemas"], ["máquina travou", "fora de serviço"])

    def test_group_polarities_are_joined_per_respondent(self):
        sub = self.caps["Equipamentos"]["subcapitulos"][0]
        # O respondente 4 criticou um subtema e elogiou o outro: no grupo, misto.
        self.assertEqual(sub["polaridades"], {"negativa": 4, "mista": 1})

    def test_leftover_goes_to_one_line(self):
        cap = self.caps["Equipamentos"]
        self.assertEqual([s["nome"] for s in cap["subcapitulos"]], ["Máquina parada", "Máquinas novas"])
        self.assertEqual(cap["demais"], "Outros assuntos do tema: porta da máquina 1")

    def test_isolated_chapter_has_one_fala_and_no_subchapters(self):
        cap = self.caps["Estacionamento"]
        self.assertEqual(cap["faixa"], rel.MENCAO_ISOLADA)
        self.assertEqual(cap["subcapitulos"], [])
        self.assertEqual(len(cap["falas"]), 1)
        self.assertEqual(cap["texto"], "")
        self.assertIsNone(cap["hipotese"])

    def test_outros_is_the_last_chapter_with_seal_and_candidates_as_subchapters(self):
        cap = self.caps[capitulos.TITULO_OUTROS]
        self.assertEqual((cap["tipo"], cap["selo"], cap["faixa"]), ("outros", capitulos.SELO_OUTROS, ""))
        self.assertEqual([(s["nome"], s["respondentes"]) for s in cap["subcapitulos"]], [("Avaliação geral", 3)])
        self.assertEqual(cap["demais"], "Outros assuntos: horário 1")

    def test_voice_not_evaluable_without_five_respondents_with_voice(self):
        voz = self.caps["Equipamentos"]["voz"]
        self.assertEqual((voz["avaliavel"], voz["direcao"], voz["frase"]), (False, "", ""))

    def test_mixed_taxonomy_fixed_category_becomes_one_chapter(self):
        taxonomia = parse_taxonomia("Equipamentos\nAmbiente > Fila\nAmbiente > Limpeza")
        audios = [
            _audio(1, [("Fila enorme no sábado para usar qualquer máquina da loja", -0.6)],
                   {"subtema": "Ambiente > Fila", "polaridade": "negativa", "trechos": [{"linha": 1}]}),
            _audio(2, [("Loja limpa e cheirosa, chão sempre seco perto das secadoras", 0.7)],
                   {"subtema": "Ambiente > Limpeza", "polaridade": "positiva", "trechos": [{"linha": 1}]},
                   {"subtema": "Ambiente > Fila", "polaridade": "positiva", "trechos": [{"linha": 1}]}),
        ]
        agregacao = rel.agregar(audios, [1, 2], taxonomia, PESQUISA_OPINIAO)

        doc = capitulos.montar_relatorio(agregacao, taxonomia, Consolidacao(), CABECALHO)

        cap = doc["capitulos"][0]
        self.assertEqual((cap["tema"], cap["tipo"], cap["respondentes"]), ("Ambiente", "categoria", 2))
        self.assertEqual(cap["polaridades"], {"negativa": 1, "positiva": 1})
        self.assertEqual(capitulos.erros(doc), [])


class FalasTests(_Base):
    def _candidatas(self, tema):
        return self.caps[tema]["candidatas"]

    def test_alert_fala_is_never_a_candidate(self):
        textos = [self._texto(i) for c in self.doc["capitulos"] for i in c["candidatas"]]
        self.assertFalse(any("bolsa" in t for t in textos))

    def test_short_or_generic_fala_is_not_a_candidate(self):
        textos = [self._texto(i) for i in self._candidatas("Equipamentos")]
        self.assertNotIn("Gostei muito, tudo ótimo.", textos)
        self.assertTrue(all(capitulos.fala_informativa(t) for t in textos))

    def test_candidates_are_from_different_respondents_and_at_most_twelve(self):
        ids = self._candidatas("Equipamentos")
        aliases = [self.trechos[i]["alias"] for i in ids]
        self.assertEqual(len(aliases), len(set(aliases)))
        self.assertLessEqual(len(ids), capitulos.MAX_CANDIDATAS)

    def test_each_subchapter_gets_a_candidate_before_the_first_gets_a_second(self):
        subcapitulos = {t["subcapitulo"] for t in (self.trechos[i] for i in self._candidatas("Equipamentos"))}
        self.assertIn("c1.s1", subcapitulos)
        self.assertIn("c1.s2", subcapitulos)

    def test_subchapter_with_critique_shows_a_critical_fala(self):
        sub = self.caps["Equipamentos"]["subcapitulos"][0]
        self.assertTrue(sub["critica"]["falas"])
        self.assertTrue(all(self.trechos[i]["critica"] for i in sub["critica"]["falas"]))
        self.assertIsNone(self.caps["Equipamentos"]["subcapitulos"][1]["critica"])

    def test_fala_carries_alias_time_and_note(self):
        trecho = self.trechos[self.caps["Estacionamento"]["falas"][0]]
        self.assertEqual((trecho["alias"], trecho["tempo"], trecho["nota"]), ("Áudio 12", "00:05", -0.6))
        self.assertEqual(trecho["unidade"], CENTRO)

    def test_critical_fala_is_masked_and_praise_keeps_the_name(self):
        textos = {self._texto(i) for c in self.doc["capitulos"] for i in c["candidatas"]}
        self.assertIn("Mas a atendente {} demorou dois dias para responder a segunda mensagem".format(FUNCIONARIO), textos)
        self.assertIn("O atendente Marcos foi ótimo e me ensinou a usar o aplicativo", textos)


class ReclamacoesTests(_Base):
    def test_items_and_isolated_by_band(self):
        reclamacoes = self.doc["reclamacoes"]
        self.assertEqual(
            [(i["posicao"], i["titulo"], i["origem"], i["respondentes"], i["faixa"]) for i in reclamacoes["itens"]],
            [(1, "Máquina parada", "1.1", 5, rel.PADRAO)],
        )
        self.assertEqual(
            sorted((i["assunto"], i["origem"]) for i in reclamacoes["isoladas"]),
            # Suporte (2 respondentes) vem antes de atendente (1) no capítulo 2.
            [("Falta de vaga", "3"), ("Porta da máquina", "1"), ("Suporte", "2.1")],
        )

    def test_mixed_enters_only_with_a_critical_passage(self):
        suporte = next(i for i in self.doc["reclamacoes"]["isoladas"] if i["assunto"] == "Suporte")
        self.assertIn("demorou dois dias", self._texto(suporte["fala"]))
        # O respondente 11 é misto sem trecho de nota até -0,2: fica fora e é contado.
        self.assertEqual(self.doc["reclamacoes"]["mistas_sem_trecho_critico"], 1)

    def test_unit_label_with_more_than_one_unit_in_the_cut(self):
        item = self.doc["reclamacoes"]["itens"][0]
        self.assertEqual(item["unidades"], [MOEMA])
        self.assertIsNone(item["etiqueta_voz"])

    def test_unit_label_is_left_out_with_one_unit(self):
        ids = [a.audio_id for a in self.audios if a.unidade == MOEMA]
        agregacao = rel.agregar(self.audios, ids, TAXONOMIA, PESQUISA_OPINIAO)
        doc = capitulos.montar_relatorio(agregacao, TAXONOMIA, CONSOLIDACAO, CABECALHO)
        self.assertTrue(all(i["unidades"] == [] for i in doc["reclamacoes"]["itens"]))
        self.assertTrue(all(i["unidade"] is None for i in doc["reclamacoes"]["isoladas"]))

    def test_intensity_breaks_the_tie_with_irritation_first(self):
        # Duas reclamações em padrão com 5 respondentes críticos cada; a de
        # Pagamento (capítulo depois) com a voz irritada vem primeiro.
        audios, agregacao = _cenario()
        extras = [
            _audio(20 + i, [("O estorno da cobrança duplicada demorou mais de duas semanas", -0.7)],
                   _tema("Pagamento", "estorno", "negativa", 1), unidade=CENTRO)
            for i in range(5)
        ]
        todos = audios + extras
        agregacao = rel.agregar(todos, [a.audio_id for a in todos], TAXONOMIA, PESQUISA_OPINIAO)
        irritada = rel.LeituraTrecho(voz=-0.6, ativacao=1.0, peso=1.0, nota_texto=-0.7)
        ocorrencias = dict(agregacao.ocorrencias)
        ocorrencias["Pagamento"] = [
            (resp, dataclasses.replace(trecho, nota_texto=-0.7), irritada) for resp, trecho, _ in ocorrencias["Pagamento"]
        ]
        agregacao = dataclasses.replace(agregacao, ocorrencias=ocorrencias)

        doc = capitulos.montar_relatorio(agregacao, TAXONOMIA, CONSOLIDACAO, CABECALHO)

        primeiro, segundo = doc["reclamacoes"]["itens"][:2]
        self.assertEqual((primeiro["titulo"], primeiro["etiqueta_voz"]), ("Estorno", "Irritação perceptível na voz"))
        self.assertEqual(segundo["titulo"], "Máquina parada")
        self.assertEqual(capitulos.erros(doc), [])


class MarkdownTests(_Base):
    def test_markdown_carries_titles_numbers_and_falas(self):
        texto = capitulos.markdown_do_relatorio(self.doc)
        self.assertIn("## 1. Equipamentos", texto)
        self.assertIn("8/12 (67%) · Padrão", texto)
        self.assertIn("### 1.1 Máquina parada", texto)
        self.assertIn("5 respondentes · 4 críticas · 1 mista", texto)
        self.assertIn("## Reclamações e feedbacks negativos", texto)
        self.assertIn("### 1. Máquina parada", texto)
        primeira = self.trechos[self.caps["Equipamentos"]["subcapitulos"][0]["falas"][0]]
        self.assertIn("> “{}” ({}, {})".format(primeira["texto"], primeira["alias"], primeira["tempo"]), texto)
        self.assertIn("## Notas metodológicas", texto)

    def test_empty_ai_fields_do_not_leave_empty_lines_or_labels(self):
        texto = capitulos.markdown_do_relatorio(self.doc)
        self.assertNotIn("**Crítica:** \n", texto)
        self.assertNotIn("**Hipótese:**", texto)
        self.assertNotIn("\n\n\n", texto)

    def test_snapshot_of_the_delta_does_not_change(self):
        snapshot = rel.snapshot(self.agregacao, rel.Recorte())
        self.assertEqual(
            set(snapshot), {"versao", "recorte", "audios", "respondentes", "temas", "emergentes", "outros", "alertas", "termometro"}
        )


if __name__ == "__main__":
    unittest.main()
