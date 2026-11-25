"""Texto v2 e análises antigas no PDF novo, e o Anexo técnico (spec do relatório por tema, §9).

O markdown salvo vira HTML pelo template legado; o "Apêndice técnico" sai do
corpo e vira o Anexo, com a tabela de áudios e unidades. Os testes de PDF
levam o marcador `requer_weasyprint` e são pulados sem o Pango.
"""

import re
import unittest
from datetime import date, datetime

import pytest

from tests.fixtures import texto_v2 as v2
from utils import prosodia_geracao as geracao
from utils import prosodia_relatorio as rel
from utils import relatorio_html, relatorio_pdf
from utils import relatorio_markdown as markdown


def _texto_visivel(html: str) -> str:
    sem_estilo = re.sub(r"<style>.*?</style>", "", str(html), flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", sem_estilo).split())


class SepararAnexoTests(unittest.TestCase):
    def test_o_apendice_sai_do_corpo(self):
        corpo, anexo = markdown.separar_anexo(v2.texto_v2())

        self.assertIn("### 7. Termômetro de Experiência", corpo)
        self.assertNotIn("Apêndice técnico", corpo)
        self.assertNotIn("#### Voz por tema", corpo)
        self.assertTrue(anexo.startswith("#### Voz por tema"))
        self.assertIn("#### Limitações e cortes aplicados", anexo)

    def test_variacoes_do_titulo(self):
        for titulo in ("## Apendice Tecnico", "### APÊNDICE TÉCNICO", "#### Apêndice técnico ##"):
            with self.subTest(titulo=titulo):
                corpo, anexo = markdown.separar_anexo("Corpo.\n\n{}\n\nTabelas.".format(titulo))
                self.assertEqual((corpo, anexo), ("Corpo.", "Tabelas."))

    def test_texto_sem_apendice_fica_inteiro(self):
        self.assertEqual(markdown.separar_anexo("## Resultado\n\nTexto antigo."), ("## Resultado\n\nTexto antigo.", ""))
        self.assertEqual(markdown.separar_anexo(None), ("", ""))

    def test_mencao_ao_apendice_no_meio_do_texto_nao_corta(self):
        texto = "### Resumo\n\nVer o Apêndice técnico para as médias."
        self.assertEqual(markdown.separar_anexo(texto), (texto, ""))


class ConverterTests(unittest.TestCase):
    def test_o_titulo_mais_alto_vira_h2_e_entra_no_indice(self):
        convertido = markdown.converter("Abertura.\n\n### Resumo\n\nTexto.\n\n#### Detalhe\n\n### 1. Achados\n\n- A1 · item")

        html = str(convertido.html)
        self.assertIn('<h2 id="s1">Resumo</h2>', html)
        self.assertIn("<h3>Detalhe</h3>", html)
        self.assertEqual([(t.ancora, t.texto) for t in convertido.titulos], [("s1", "Resumo"), ("s2", "1. Achados")])

    def test_html_cru_da_ia_nao_passa(self):
        html = str(markdown.converter("Texto <script>alert(1)</script> e <b>negrito</b>.").html)

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<b>", html)

    def test_tabela_ganha_classe_pela_largura(self):
        estreita = "| A | B |\n|---|---|\n| 1 | 2 |"
        larga = "| " + " | ".join("C{}".format(i) for i in range(9)) + " |\n|" + "---|" * 9 + "\n| " + " | ".join("x" * 9) + " |"

        self.assertIn('<table class="tabela tabela--estreita">', str(markdown.converter(estreita).html))
        self.assertIn('<table class="tabela tabela--larga">', str(markdown.converter(larga).html))

    def test_marcas_soltas_somem_e_sublinhado_de_sessao_fica(self):
        html = str(markdown.converter("Texto com **negrito aberto e wa_5521990000001_3 no fim.").html)

        self.assertNotIn("**", html)
        self.assertIn("wa_5521990000001_3", html)

    def test_audio_fica_preso_ao_numero(self):
        html = str(markdown.converter("| Áudio | n |\n|---|---|\n| Áudio 02 | 3 |\n\nVer os Áudios 03 e 04.").html)

        self.assertIn("Áudio 02", html)
        self.assertIn("Áudios 03", html)

    def test_italico_vira_em_e_nao_vaza(self):
        html = str(markdown.converter("*Apoia-se em:* A1, A2.").html)

        self.assertIn("<em>Apoia-se em:</em>", html)
        self.assertNotIn("*", _texto_visivel(html))


class LegadoHtmlTests(unittest.TestCase):
    def setUp(self):
        self.html = relatorio_html.legado_html(
            v2.texto_v2(),
            projeto=v2.PROJETO,
            subtitulo="Unidade Moema (LJ-MOE-01)",
            gerado_em="25/11/2026",
            modelo="gpt-4.1",
            contexto=geracao.contexto_do_projeto(v2.projeto()),
            citacoes=[{"filename": "Serviços.pdf", "quote": "Trecho " * 200}],
            com_anexo=True,
        )

    def test_capa_indice_e_corpo_sem_o_apendice(self):
        texto = _texto_visivel(self.html)

        self.assertIn('<div class="capa">', self.html)
        self.assertIn("Unidade Moema (LJ-MOE-01)", texto)
        self.assertIn('<nav class="indice indice--markdown">', self.html)
        self.assertIn('href="#s1"', self.html)
        self.assertIn("7. Termômetro de Experiência", texto)
        for do_apendice in ("Voz por tema", "Médias acústicas por áudio", "Legenda dos áudios", "wa_+55"):
            self.assertNotIn(do_apendice, texto)
        self.assertIn("O detalhe técnico está no Anexo técnico", texto)

    def test_contexto_em_duas_linhas_sem_o_briefing(self):
        texto = _texto_visivel(self.html)

        self.assertIn("Lavanderias self-service — Rede de 6 unidades", texto)
        self.assertIn("Perguntas centrais: O que faz o cliente voltar?", texto)
        self.assertNotIn("Briefing longo", texto)

    def test_referencias_cortadas(self):
        self.assertIn("Referências da Base de Conhecimento", self.html)
        self.assertIn("1. Serviços.pdf", self.html)
        self.assertLess(_texto_visivel(self.html).count("Trecho"), 120)

    def test_sem_anexo_a_capa_nao_promete_um(self):
        html = relatorio_html.legado_html("## Resultado\n\nTexto antigo.", projeto="P", gerado_em="01/01/2026")

        self.assertNotIn("Anexo técnico", _texto_visivel(html))
        self.assertNotIn('class="indice', html, "um título só não pede índice")

    def test_texto_vazio_nao_quebra(self):
        self.assertIn('<main class="md">', relatorio_html.legado_html(None, projeto=""))

    def test_linhas_do_contexto(self):
        self.assertEqual(relatorio_html.linhas_do_contexto({}), [])
        self.assertEqual(relatorio_html.linhas_do_contexto({"problemas": "Por quê?"}), ["Perguntas centrais: Por quê?"])
        longa = relatorio_html.linhas_do_contexto({"especialidade": "x" * 500})[0]
        self.assertEqual(len(longa), 240)
        self.assertTrue(longa.endswith("…"))


class AnexoTests(unittest.TestCase):
    def test_anexo_do_texto_poe_a_tabela_de_unidades_antes_das_limitacoes(self):
        audios = v2.audios_do_projeto()
        anexo = geracao.anexo_do_texto(v2.texto_v2(), audios, [a["id"] for a in audios])

        self.assertLess(anexo.index("#### Áudios e unidades"), anexo.index("#### Limitações e cortes aplicados"))
        self.assertIn("| Áudio 01 | Moema · LJ-MOE-01 | Máquinas |", anexo)
        self.assertIn("| Áudio 08 | — |", anexo)

    def test_a_tabela_de_unidades_nao_traz_a_sessao(self):
        audios = v2.audios_do_projeto()
        tabela = geracao.audios_e_unidades_markdown(audios, [1, 2])

        self.assertEqual(tabela.count("\n"), 3)
        for audio in audios:
            self.assertNotIn(audio["session_id"], tabela)
        self.assertEqual(geracao.audios_e_unidades_markdown(audios, []), "Nenhum áudio no recorte.")

    def test_texto_sem_apendice_nao_tem_anexo(self):
        self.assertEqual(geracao.anexo_do_texto("## Resultado\n\nTexto antigo.", v2.audios_do_projeto(), [1]), "")

    def test_anexo_html_e_deitado_e_tem_indice(self):
        audios = v2.audios_do_projeto()
        html = relatorio_html.anexo_html(
            geracao.anexo_do_texto(v2.texto_v2(), audios, [1, 2, 3]), projeto=v2.PROJETO, gerado_em="25/11/2026"
        )

        self.assertIn("@page { size: A4 landscape; }", html)
        self.assertIn("capa--paisagem", html)
        self.assertIn("NENCBOOST · ANEXO TÉCNICO", html)
        self.assertIn(">Voz por tema</a>", html)
        self.assertIn(">Áudios e unidades</a>", html)


class NomesTests(unittest.TestCase):
    def test_nome_do_anexo(self):
        dia = date(2026, 11, 25)

        self.assertEqual(geracao.nome_do_anexo("Lava Já", "", dia), "Anexo_Tecnico_Lava_Ja_2026-11-25.pdf")
        self.assertEqual(
            geracao.nome_do_anexo("Lava Já", geracao.TODAS_AS_UNIDADES, dia), "Anexo_Tecnico_Lava_Ja_2026-11-25.pdf"
        )
        self.assertEqual(
            geracao.nome_do_anexo("Lava Já", "Moema (LJ-MOE-01)", dia),
            "Anexo_Tecnico_Lava_Ja_Moema_LJ_MOE_01_2026-11-25.pdf",
        )

    def test_nome_do_anexo_da_analise_salva(self):
        analise = {
            "created_at": "2026-11-20 09:30:00",
            "temas": {"recorte": rel.Recorte(unidade=("Moema (LJ-MOE-01)",)).como_dict()},
        }

        self.assertEqual(
            geracao.nome_do_anexo_da_analise("Lava Já", analise), "Anexo_Tecnico_Lava_Ja_Moema_LJ_MOE_01_2026-11-20.pdf"
        )
        self.assertEqual(
            geracao.nome_do_anexo_da_analise("Lava Já", {"created_at": "2026-11-20 09:30:00"}),
            "Anexo_Tecnico_Lava_Ja_2026-11-20.pdf",
        )

    def test_subtitulo_do_recorte(self):
        self.assertEqual(geracao.subtitulo_do_recorte(rel.Recorte()), "Todos os áudios do projeto")
        self.assertEqual(
            geracao.subtitulo_do_recorte(rel.Recorte(descricao="unidade Moema; de 01/10/2026 a 30/10/2026")),
            "Unidade Moema; de 01/10/2026 a 30/10/2026",
        )


_DISPONIVEL = relatorio_pdf.disponivel()


@pytest.mark.requer_weasyprint
@unittest.skipUnless(_DISPONIVEL, "WeasyPrint indisponível: {}".format(relatorio_pdf.motivo_da_indisponibilidade()))
class PdfDoLegadoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analise = {
            "id": 7,
            "created_at": "2026-11-20 09:30:00",
            "model": "gpt-4.1",
            "analysis_text": v2.texto_v2(),
            "citations": [{"filename": "Serviços.pdf", "quote": "Trecho."}],
            "temas": {"recorte": rel.Recorte().como_dict(), "audios": list(range(1, 9))},
        }
        cls.pdf, cls.anexo = geracao.arquivos_da_analise(v2.projeto(), cls.analise, v2.audios_do_projeto())

    def test_dois_pdfs(self):
        for pdf in (self.pdf, self.anexo):
            self.assertTrue(pdf.startswith(b"%PDF"))
            self.assertGreater(len(pdf), 20_000)

    def test_corpo_e_anexo_renderizados(self):
        import weasyprint

        corpo = weasyprint.HTML(
            string=relatorio_html.legado_html(self.analise["analysis_text"], projeto=v2.PROJETO, com_anexo=True),
            base_url=str(relatorio_html.PASTA_STATIC) + "/",
        ).render()
        rotulos = [marcador[1] for pagina in corpo.pages for marcador in pagina.bookmarks]
        self.assertIn("Resumo Executivo", rotulos)
        self.assertIn("7. Termômetro de Experiência", rotulos)
        self.assertNotIn("Voz por tema", rotulos)
        self.assertEqual(corpo.pages[0].bookmarks, [], "a capa não entra nos marcadores")

        anexo = weasyprint.HTML(
            string=relatorio_html.anexo_html(
                geracao.anexo_do_texto(self.analise["analysis_text"], v2.audios_do_projeto(), range(1, 9)),
                projeto=v2.PROJETO,
            ),
            base_url=str(relatorio_html.PASTA_STATIC) + "/",
        ).render()
        primeira = anexo.pages[0]
        self.assertGreater(primeira.width, primeira.height, "o anexo é deitado")
        rotulos = [marcador[1] for pagina in anexo.pages for marcador in pagina.bookmarks]
        for esperado in ("Voz por tema", "Áudios e unidades", "Limitações e cortes aplicados"):
            self.assertIn(esperado, rotulos)

    def test_analise_antiga_sem_apendice_nao_tem_anexo(self):
        antiga = dict(self.analise, analysis_text="## Resultado\n\nTexto livre da análise antiga.", temas=None)
        pdf, anexo = geracao.arquivos_da_analise(v2.projeto(), antiga, v2.audios_do_projeto())

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIsNone(anexo)


if __name__ == "__main__":
    unittest.main()
