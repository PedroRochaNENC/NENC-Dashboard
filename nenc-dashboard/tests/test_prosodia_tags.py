"""Taxonomia por tags (fase 3 de docs/SPEC_UNIDADES_E_TAGS.md, §4).

Linha sem ">" é tag aberta: a tag é fixa e a IA cria o subtema dentro dela,
que vira detalhe da linha. O que não cabe em nenhuma tag vai para Outros, e
os subtemas de Outros que se repetem viram candidatos a tag nova. "!" no
começo marca a linha de alerta crítico: qualquer menção vai para um bloco no
topo do relatório, com a paráfrase e nunca a fala. Toda linha da taxonomia
aparece na tabela, as sem menção também.
"""

import ast
import hashlib
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel
from utils import prosodia_temas as temas
from utils.prosodia_project_types import PESQUISA_OPINIAO
from utils.prosodia_temas import OUTROS, Linha, Subtema, parse_taxonomia

PAGES_DIR = Path(__file__).resolve().parent.parent / "modules" / "prosodia"

SMART_FIT = """\
! Alerta Crítico: Assédio (moral, sexual, discriminação), acidentes graves/lesões e furtos/roubos
Staff: Atendimento na recepção, suporte dos professores, presença do líder/gerente
Limpeza: Higiene e limpeza de áreas de treino, recepção, sanitários e vestiários
Lotação: Superlotação, filas e espera para aparelhos em horários de pico
Aulas Coletivas: Qualidade das aulas em grupo, grade de horários, instrutores de coletivas
Manutenção de Equipamentos de Treino: Defeitos mecânicos e manutenção de máquinas, esteiras, bikes, halteres
Quantidade e Qualidade de Equipamentos de Treino: Disponibilidade e qualidade de máquinas, esteiras, bikes, halteres
Facilities e Predial: Estrutura física: piso, infiltrações, pintura, portas, teto, iluminação, chuveiros
Climatização / Ar Condicionado: Calor, ambiente abafado, ventilação, ar-condicionado quebrado/desligado
Estrutura de Tecnologia Local: Wi-Fi, catracas, biometria, totens, balança digital, body scan, som
Sistemas Financeiro: Cobranças indevidas, reajustes, multas, cancelamento de plano
Aplicativo: Erros de login, bugs, falhas no agendamento, ficha de treino
Atendimento Canais Digital: Suporte via WhatsApp, Fale Conosco, Reclame Aqui, Redes Sociais
Addons: Serviços complementares: Nutri, Energy, Cadeira de Massagem, Coach
Serviços Agregados: Estacionamento, segurança patrimonial, vending machines
Total Pass: Problemas de acesso, validação de tokens, regras do TotalPass
"""


class ParseComTagsTests(unittest.TestCase):
    def test_smart_fit_taxonomy_is_sixteen_open_tags_with_one_alert(self):
        taxonomia = parse_taxonomia(SMART_FIT)

        self.assertEqual(len(taxonomia.subtemas), 16)
        self.assertEqual(taxonomia.linhas_invalidas, ())
        self.assertTrue(all(s.aberta for s in taxonomia.subtemas))
        self.assertEqual(taxonomia.alertas, {"Alerta Crítico"})
        # A definição começa no primeiro dois-pontos: os de dentro dela ficam nela.
        facilities = next(s for s in taxonomia.subtemas if s.categoria == "Facilities e Predial")
        self.assertEqual(facilities.definicao, "Estrutura física: piso, infiltrações, pintura, portas, teto, iluminação, chuveiros")

    def test_formats_live_together_and_alert_works_in_both(self):
        taxonomia = parse_taxonomia(
            "Staff: recepção e professores\n"
            "! Segurança > Furto no vestiário\n"
            "- ! Alerta Crítico\n"
            "Equipamentos > Máquina parada"
        )

        self.assertEqual(
            taxonomia.subtemas,
            (
                Subtema("Staff", "", "recepção e professores"),
                Subtema("Segurança", "Furto no vestiário", "", True),
                Subtema("Alerta Crítico", "", "", True),
                Subtema("Equipamentos", "Máquina parada"),
            ),
        )
        self.assertEqual([s.rotulo for s in taxonomia.subtemas],
                         ["Staff", "Segurança > Furto no vestiário", "Alerta Crítico", "Equipamentos > Máquina parada"])
        self.assertTrue(taxonomia.tem_tags_abertas)
        self.assertEqual(taxonomia.alertas, {"Segurança > Furto no vestiário", "Alerta Crítico"})

    def test_open_tag_and_fixed_subtheme_of_the_same_category_do_not_live_together(self):
        primeiro_a_tag = parse_taxonomia("Staff\nStaff > Recepção\nLimpeza")
        primeiro_o_subtema = parse_taxonomia("staff > Recepção\nStaff\nLimpeza")

        self.assertEqual([s.rotulo for s in primeiro_a_tag.subtemas], ["Staff", "Limpeza"])
        self.assertEqual(primeiro_a_tag.linhas_invalidas, ("Staff > Recepção",))
        self.assertEqual([s.rotulo for s in primeiro_o_subtema.subtemas], ["staff > Recepção", "Limpeza"])
        self.assertEqual(primeiro_o_subtema.linhas_invalidas, ("Staff",))

    def test_repeated_tag_counts_once(self):
        taxonomia = parse_taxonomia("Lotação: fila\nlotacao\nLimpeza")

        self.assertEqual([s.rotulo for s in taxonomia.subtemas], ["Lotação", "Limpeza"])
        # A tag repetida sai com a grafia da primeira vez, como a categoria do subtema fixo.
        self.assertEqual(taxonomia.repetidos, ("Lotação",))

    def test_format_round_trip_keeps_tags_and_alert(self):
        taxonomia = parse_taxonomia("!  Alerta Crítico:  assédio\nStaff\nEquipamentos > Máquina: parada")

        texto = temas.formatar_taxonomia(taxonomia)

        self.assertEqual(texto, "! Alerta Crítico: assédio\nStaff\nEquipamentos > Máquina: parada")
        self.assertEqual(parse_taxonomia(texto).subtemas, taxonomia.subtemas)


class VersaoEChaveTests(unittest.TestCase):
    def test_v2_taxonomy_keeps_its_version(self):
        # A fórmula de antes das tags: rótulo e definição, ordenados.
        texto = "Ambiente > Lotação: fila\nAtendimento > Cordialidade"
        itens = sorted(["Ambiente > Lotação\tfila", "Atendimento > Cordialidade\t"])
        antiga = hashlib.sha256("\n".join(itens).encode("utf-8")).hexdigest()[:16]

        self.assertEqual(temas.versao_taxonomia(parse_taxonomia(texto)), antiga)

    def test_version_changes_with_the_alert_and_with_the_line_type(self):
        base = temas.versao_taxonomia(parse_taxonomia("Staff\nLimpeza"))

        self.assertNotEqual(base, temas.versao_taxonomia(parse_taxonomia("! Staff\nLimpeza")))
        self.assertNotEqual(
            temas.versao_taxonomia(parse_taxonomia("Staff > Geral\nLimpeza > Geral")),
            temas.versao_taxonomia(parse_taxonomia("Staff\nLimpeza")),
        )

    def test_only_taxonomies_with_open_tags_use_the_new_classification_version(self):
        linhas = [Linha(1, "Respondente", "A academia vive cheia.")]
        fixa = parse_taxonomia("Ambiente > Lotação")
        aberta = parse_taxonomia("Lotação")

        antes = (temas.chave_classificacao(fixa, PESQUISA_OPINIAO, linhas),
                 temas.chave_classificacao(aberta, PESQUISA_OPINIAO, linhas))
        with patch.object(temas, "VERSAO_CLASSIFICACAO_TAGS", "outra"):
            depois = (temas.chave_classificacao(fixa, PESQUISA_OPINIAO, linhas),
                      temas.chave_classificacao(aberta, PESQUISA_OPINIAO, linhas))

        self.assertEqual(antes[0], depois[0])
        self.assertNotEqual(antes[1], depois[1])


def _linhas(n=4):
    return [Linha(i, "Respondente", "Fala {}.".format(i), (i - 1) * 4.0, (i - 1) * 4.0 + 3.5, i - 1)
            for i in range(1, n + 1)]


class _IaQueResponde:
    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = []

    def __call__(self, sistema, usuario, max_tokens):
        self.chamadas.append((sistema, usuario))
        return json.dumps(self.resposta)


class ClassificacaoComTagsTests(unittest.TestCase):
    TAXONOMIA = parse_taxonomia("! Alerta Crítico: assédio\nStaff\nLimpeza\nEquipamentos > Máquina parada")

    def _classificar(self, resposta, **kwargs):
        ia = _IaQueResponde(resposta)
        resultado = temas.classificar_audio(_linhas(), self.TAXONOMIA, PESQUISA_OPINIAO, [], ia, **kwargs)
        return resultado, ia

    def test_open_tag_gets_the_subtheme_as_detail_and_the_rest_goes_to_outros(self):
        resultado, _ = self._classificar({"temas": [
            {"subtema": "Staff > Cordialidade da recepção", "polaridade": "positiva", "linhas": [1]},
            {"subtema": "staff > professor ausente", "polaridade": "negativa", "linhas": [2]},
            {"subtema": "Preço > Mensalidade cara", "polaridade": "negativa", "linhas": [3]},
            {"subtema": "Outros > Horário de funcionamento", "polaridade": "neutra", "linhas": [4]},
            {"subtema": "Limpeza", "polaridade": "negativa", "linhas": [2]},
            {"subtema": "Equipamentos > Máquina parada", "polaridade": "negativa", "linhas": [3]},
        ]})

        self.assertEqual(
            [(t["subtema"], t.get("detalhe")) for t in resultado["temas"]],
            [
                ("Staff", "Cordialidade da recepção"),
                ("Staff", "professor ausente"),
                ("Limpeza", None),
                ("Equipamentos > Máquina parada", None),
                (OUTROS, "Horário de funcionamento"),
                (OUTROS, "Mensalidade cara"),
            ],
        )
        self.assertEqual(resultado["emergentes"], [])

    def test_same_subtheme_twice_merges_and_an_emergent_with_a_tag_category_goes_to_the_tag(self):
        resultado, _ = self._classificar({
            "temas": [
                {"subtema": "Staff > Recepção", "polaridade": "positiva", "linhas": [1]},
                {"subtema": "Staff > recepcao", "polaridade": "negativa", "linhas": [2]},
            ],
            "emergentes": [{"rotulo": "Professor ausente", "categoria": "Staff", "polaridade": "negativa", "linhas": [3]}],
        })

        self.assertEqual(
            [(t["subtema"], t["detalhe"], t["polaridade"], [x["linha"] for x in t["trechos"]]) for t in resultado["temas"]],
            [("Staff", "Professor ausente", "negativa", [3]), ("Staff", "Recepção", "mista", [1, 2])],
        )

    def test_prompt_of_open_tags_lists_known_subthemes_and_hides_the_alert_mark(self):
        _, ia = self._classificar(
            {"temas": []}, subtemas_conhecidos={"Staff": ["Recepção", "Professores"], OUTROS: ["Preço"]}
        )

        sistema, usuario = ia.chamadas[0]
        self.assertIn('"Tag > subtema"', sistema)
        self.assertIn('"Outros > subtema"', sistema)
        self.assertIn("- Staff > Recepção\n- Staff > Professores\n- Outros > Preço", usuario)
        self.assertIn("- Alerta Crítico: assédio", usuario)
        self.assertNotIn("!", usuario.split("<taxonomia>")[1].split("</taxonomia>")[0])

    def test_v2_taxonomy_keeps_the_v2_prompt(self):
        ia = _IaQueResponde({"temas": [], "emergentes": []})

        temas.classificar_audio(_linhas(), parse_taxonomia("Ambiente > Lotação"), PESQUISA_OPINIAO, [("Preço", "Plano")], ia)

        sistema, usuario = ia.chamadas[0]
        self.assertEqual(
            sistema,
            temas.SISTEMA_CLASSIFICACAO.format(
                regra_respondente=temas._REGRA_RESPONDENTE[PESQUISA_OPINIAO], max_linhas=temas._MAX_LINHAS_POR_TEMA
            ),
        )
        self.assertIn("<emergentes>\n- Preço (Plano)\n</emergentes>", usuario)

    def test_ai_proposal_ignores_loose_lines(self):
        ia = lambda sistema, usuario, max_tokens: "Atendimento\nAtendimento > Recepção: balcão\nLimpeza > Vestiário"

        taxonomia = temas.propor_taxonomia([_linhas()], PESQUISA_OPINIAO, {}, ia)

        self.assertEqual([s.rotulo for s in taxonomia.subtemas], ["Atendimento > Recepção", "Limpeza > Vestiário"])


class ContagensDaClassificacaoTests(unittest.TestCase):
    def test_details_count_once_per_audio_most_cited_first(self):
        classificacoes = [
            {"temas": [{"subtema": "Staff", "detalhe": "Recepção"}, {"subtema": "Staff", "detalhe": "recepcao"},
                       {"subtema": OUTROS, "detalhe": "Preço"}]},
            {"temas": [{"subtema": "Staff", "detalhe": "Professores"}, {"subtema": OUTROS, "detalhe": "preço"}]},
            {"temas": [{"subtema": "Staff", "detalhe": "Recepção"}, {"subtema": "Limpeza"}]},
        ]

        self.assertEqual(
            temas.contar_detalhes(classificacoes),
            {"Staff": [("Recepção", 2), ("Professores", 1)], OUTROS: [("Preço", 2)]},
        )

    def test_alert_seal_reads_the_saved_classification(self):
        taxonomia = parse_taxonomia("! Alerta Crítico\nStaff")
        salvas = {
            1: {"temas": {"temas": [{"subtema": "Alerta Crítico"}]}},
            2: {"temas": {"temas": [{"subtema": "Staff", "detalhe": "Recepção"}]}},
            3: {"temas": None},
        }

        self.assertEqual(temas.audios_com_alerta(salvas, taxonomia), {1})
        self.assertEqual(temas.audios_com_alerta(salvas, parse_taxonomia("Alerta Crítico\nStaff")), set())


TAXONOMIA = parse_taxonomia("! Alerta Crítico: assédio\nStaff\nLimpeza\nLotação\nEquipamentos > Máquina parada")


def _tema(subtema, detalhe=None, polaridade="negativa", evidencia="", inicio=4.0):
    tema = {"subtema": subtema, "polaridade": polaridade, "evidencia": evidencia,
            "trechos": [{"linha": 2, "segmento_idx": 1, "inicio_s": inicio, "fim_s": inicio + 3.5}]}
    if detalhe:
        tema["detalhe"] = detalhe
    return tema


def _audio(audio_id, *temas_do_audio, numero=None, unidade="", chegada=""):
    return rel.AudioDoProjeto(
        audio_id,
        "wa_+552199{:06d}_{}".format(audio_id if numero is None else numero, audio_id),
        tuple(_linhas()),
        {"respondente": None, "respondente_incerto": False, "temas": list(temas_do_audio), "emergentes": []},
        datetime(2026, 10, 1) + timedelta(hours=audio_id),
        chegada=chegada,
        unidade=unidade,
    )


class AgregacaoComTagsTests(unittest.TestCase):
    def setUp(self):
        self.audios = [
            _audio(1, _tema("Staff", "Cordialidade da recepção", "positiva"), _tema(OUTROS, "Preço da mensalidade")),
            _audio(2, _tema("Staff", "Cordialidade da recepção", "positiva"), _tema("Staff", "Professor ausente"),
                   _tema(OUTROS, "preço da mensalidade")),
            _audio(3, _tema("Staff", "Professor ausente"), _tema("Limpeza")),
            _audio(4, _tema("Alerta Crítico", evidencia="relata assédio de um aluno no vestiário", inicio=14.0),
                   _tema("Limpeza"), unidade="Moema", chegada="23/10 19:40"),
            _audio(5, _tema(OUTROS, "Preço da mensalidade"), _tema("Equipamentos > Máquina parada")),
            # Fora do recorte: conta só nos candidatos a tag nova, que olham o projeto.
            _audio(6, _tema(OUTROS, "Horário de funcionamento")),
        ]
        self.agregacao = rel.agregar(self.audios, [1, 2, 3, 4, 5], TAXONOMIA, PESQUISA_OPINIAO)

    def _linha(self, rotulo):
        return next(t for t in self.agregacao.temas if t.subtema == rotulo)

    def test_open_tag_counts_by_respondent_with_its_subthemes(self):
        staff = self._linha("Staff")

        self.assertEqual((staff.respondentes, staff.aberta), (3, True))
        self.assertEqual(staff.subtemas, [("Cordialidade da recepção", 2), ("Professor ausente", 2)])
        self.assertEqual(self._linha("Equipamentos > Máquina parada").subtemas, [])

    def test_alert_line_has_no_band_and_no_finding(self):
        alerta = self._linha("Alerta Crítico")

        self.assertEqual((alerta.faixa, alerta.alerta, alerta.respondentes), (rel.ALERTA, True, 1))
        self.assertNotIn("Alerta Crítico", [t.subtema for _, t in rel.achados_esperados(self.agregacao)])
        self.assertNotIn("Alerta Crítico", self.agregacao.trechos)

    def test_outros_counts_apart_and_candidates_look_at_the_project(self):
        outros = self.agregacao.outros

        self.assertEqual((outros.respondentes, outros.subtemas), (3, [("Preço da mensalidade", 3)]))
        self.assertNotIn(OUTROS, [t.subtema for t in self.agregacao.temas])
        self.assertEqual(self.agregacao.candidatos, [("Preço da mensalidade", 3)])

    def test_lines_without_mention_stay_listed(self):
        self.assertEqual(self.agregacao.sem_mencao, ["Lotação"])

    def test_each_alert_is_listed_with_the_paraphrase_unit_and_time(self):
        (alerta,) = self.agregacao.alertas

        self.assertEqual(
            (alerta.alias, alerta.tema, alerta.unidade, alerta.chegada, alerta.inicio_s, alerta.evidencia),
            ("Áudio 04", "Alerta Crítico", "Moema", "23/10 19:40", 14.0, "relata assédio de um aluno no vestiário"),
        )

    def test_table_has_every_line_the_subthemes_column_and_outros(self):
        tabela = rel.tabela_temas_markdown(self.agregacao)
        cabecalho = tabela.splitlines()[0]

        self.assertIn("| Tema | Respondentes | Faixa | Texto | Voz nos trechos | Subtemas |", cabecalho)
        self.assertIn("Cordialidade da recepção 2 · Professor ausente 2", tabela)
        self.assertIn("| Alerta Crítico | 1/5 (20%) | Alerta crítico | 1 neg | — |", tabela)
        self.assertIn("| Outros | 3/5 (60%) | — | 3 neg | — | Preço da mensalidade 3 |", tabela)
        self.assertIn("| Lotação | 0/5 | — | sem menções espontâneas nesta amostra |", tabela)
        self.assertLess(tabela.index("| Outros |"), tabela.index("| Lotação |"))
        self.assertIn("\"Outros\" reúne o que não coube em nenhuma tag.", tabela)
        self.assertIn("Alerta crítico não tem faixa", tabela)

    def test_alert_block_lists_the_paraphrase_and_never_the_speech(self):
        bloco = rel.alertas_markdown(self.agregacao)

        self.assertIn("| Áudio 04 | Alerta Crítico | Moema | 23/10 19:40 | 00:14 | relata assédio de um aluno no vestiário |", bloco)
        self.assertNotIn("Fala 2.", bloco)
        self.assertEqual(rel.alertas_markdown(rel.agregar(self.audios[:3], [1, 2, 3], TAXONOMIA, PESQUISA_OPINIAO)), "")

    def test_the_alert_block_opens_the_report_and_the_ai_only_gets_the_count(self):
        texto_da_ia = rel.separar_secoes(
            "### Resumo Executivo\nHá 1 alerta crítico.\n### Achados\n- **A1** · Staff · menção isolada, 3/5\n"
            "### Hipóteses\nNenhuma.\n### Recomendações\nNenhuma."
        )

        relatorio = rel.montar_relatorio(self.agregacao, "**Piloto**", texto_da_ia, "Apêndice")
        mensagem = rel.mensagem_para_ia(self.agregacao, {"nome": "Piloto"}, "**Piloto**")

        self.assertLess(relatorio.index("### Alertas críticos"), relatorio.index("### Resumo Executivo"))
        self.assertIn("Há 1 menção a temas de alerta crítico", mensagem)
        self.assertNotIn("relata assédio", mensagem.split("## Tarefa")[0].split("<trechos>")[1])

    def test_verification_flags_ids_on_alert_and_outros_and_accepts_tag_subthemes(self):
        texto_da_ia = rel.separar_secoes(
            "### Resumo Executivo\nStaff > Cordialidade da recepção aparece em 2/5.\n"
            "### Achados\n- **A1** · Alerta Crítico · alerta crítico, 1/5 — relato.\n"
            "- **A2** · Outros · sinal, 3/5 — preço.\n"
            "### Hipóteses\n- **H1** — x. *Apoia-se em:* A1. *Como validar:* y.\n"
            "### Recomendações\nNenhuma."
        )

        problemas = rel.verificar_referencias(self.agregacao, texto_da_ia)

        self.assertTrue(any("A1 é de Alerta Crítico, alerta crítico" in p for p in problemas), problemas)
        self.assertTrue(any("A2 é de Outros" in p for p in problemas), problemas)
        self.assertFalse(any("Cordialidade" in p for p in problemas), problemas)


class FalaDeAlertaForaDasCitacoesTests(unittest.TestCase):
    """A fala de alerta só aparece pela paráfrase do bloco do topo.

    A mesma fala pode estar também noutra tag (furto no vestiário em Alerta
    Crítico e em Serviços Agregados), e o apêndice cita fala nos momentos, nas
    divergências e no ranking de palavras.
    """

    def setUp(self):
        linhas = (
            Linha(1, "Respondente", "Bom dia.", 0.0, 3.5, 0),
            Linha(2, "Respondente", "Assediaram minha filha no vestiario.", 4.0, 7.5, 1),
        )
        alerta = rel.AudioDoProjeto(
            4, "wa_+552199000004_4", linhas,
            {"temas": [_tema("Alerta Crítico", evidencia="relata assédio no vestiário"), _tema("Limpeza")],
             "emergentes": []},
            datetime(2026, 10, 1, 4),
        )
        self.audios = [_audio(1, _tema("Limpeza")), _audio(2, _tema("Limpeza")), _audio(3, _tema("Limpeza")), alerta]
        self.agregacao = rel.agregar(self.audios, [1, 2, 3, 4], TAXONOMIA, PESQUISA_OPINIAO)

    def test_evidence_for_the_ai_skips_the_alert_line_of_another_tag(self):
        self.assertEqual(self.agregacao.falas_de_alerta, frozenset({(4, 2)}))
        self.assertTrue(self.agregacao.trechos["Limpeza"])
        self.assertNotIn(4, [t.audio_id for t in self.agregacao.trechos["Limpeza"]])

    def test_appendix_word_ranking_skips_the_alert_line(self):
        apendice = rel.apendice_markdown(self.agregacao, None, None)

        self.assertNotIn("assediaram", apendice.lower())
        self.assertIn("| bom |", apendice.lower())

    def test_frames_lose_the_alert_line_by_segment_or_by_time(self):
        import pandas as pd

        por_segmento = pd.DataFrame({
            "session_id": ["wa_+552199000004_4"] * 3 + ["wa_+552199000001_1"],
            "segmento_idx": [0, 1, 1, 1],
            "start_s": [0.0, 4.0, 5.0, 4.0],
        })
        por_tempo = pd.DataFrame({"session_id": ["wa_+552199000004_4"] * 3, "seconds": [0.0, 4.5, 8.0]})

        self.assertEqual(
            rel.sem_falas_de_alerta(por_segmento, self.agregacao)[["segmento_idx", "start_s"]].values.tolist(),
            [[0, 0.0], [1, 4.0]],
        )
        self.assertEqual(rel.sem_falas_de_alerta(por_tempo, self.agregacao)["seconds"].tolist(), [0.0, 8.0])


class TarefaComAlertasTests(unittest.TestCase):
    def test_task_mentions_the_alerts_only_when_there_are_any(self):
        com = prompts.tarefa_analise_geral([], False, [], False, alertas=2)
        sem = prompts.tarefa_analise_geral([], False, [], False)

        self.assertIn("Há 2 menções a temas de alerta crítico", com)
        self.assertNotIn("alerta crítico", sem)


class PaginasComTagsTests(unittest.TestCase):
    def _nomes_importados(self, pagina):
        arvore = ast.parse((PAGES_DIR / pagina).read_text(encoding="utf-8"))
        return {
            alias.name
            for no in ast.walk(arvore)
            if isinstance(no, ast.ImportFrom) and no.module == "utils.prosodia_temas"
            for alias in no.names
        }

    def test_audios_list_and_audio_page_read_the_alert_seal(self):
        self.assertIn("audios_com_alerta", self._nomes_importados("entrevistas.py"))
        self.assertIn("tem_alerta", self._nomes_importados("audio.py"))

    def test_preparation_lists_the_candidates_for_a_new_tag(self):
        self.assertLessEqual({"contar_detalhes", "OUTROS", "MIN_CANDIDATO"}, self._nomes_importados("preparacao.py"))


if __name__ == "__main__":
    unittest.main()
