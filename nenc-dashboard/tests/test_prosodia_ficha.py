"""Ficha de contexto do áudio (docs/SPEC_RELATORIO_POR_TEMA.md, §10 e §14; Fase 4)."""

import unittest

from utils import prosodia_ficha as ficha
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel
from utils.prosodia_project_types import ENTREVISTA_QUALITATIVA, PESQUISA_OPINIAO

RESPOSTA_DA_IA = """\
## Ficha de contexto
### Situação
Cliente da unidade Moema relata, num sábado de manhã, a máquina 9 parada depois do pagamento.
### Temas tocados
- máquina parada (negativa): parou depois do pagamento
### Gatilho e causa
O aviso de manutenção só apareceu com a roupa dentro.
### Expectativa ou pedido
Estorno do ciclo.
### Vínculo e risco
Não disse.
### O que a fala explica
O prejuízo inclui a manhã perdida.
"""

CLASSIFICACAO = {
    "temas": [
        {"subtema": "Equipamentos", "detalhe": "máquina travou", "polaridade": "negativa", "evidencia": "máquina parou"},
        {"subtema": "Alerta Crítico", "polaridade": "negativa", "evidencia": "relata furto de uma bolsa"},
    ]
}


class PromptTests(unittest.TestCase):
    def test_quick_prompts_ask_for_the_card_without_voice_reading(self):
        for prompt in (prompts.PROMPT_PESQUISA_OPINIAO, prompts.PROMPT_ENTREVISTA):
            for secao in prompts.SECOES_DA_FICHA:
                self.assertIn("### " + secao, prompt)
            self.assertIn("Não leia a voz", prompt)
            self.assertNotIn("Perfil Emocional pela Voz", prompt)
            self.assertNotIn("Plano de Ação", prompt)
        self.assertIn("Neutralize o entrevistador", prompts.PROMPT_ENTREVISTA)

    def test_quick_mode_resolves_to_the_card_and_two_step_modes_stay(self):
        self.assertIs(prompts.get_prosodia_system_prompt(PESQUISA_OPINIAO), prompts.PROMPT_PESQUISA_OPINIAO)
        self.assertIs(prompts.get_prosodia_system_prompt(ENTREVISTA_QUALITATIVA), prompts.PROMPT_ENTREVISTA)
        self.assertIn("estatística", prompts.get_prosodia_system_prompt(PESQUISA_OPINIAO, "estatistica").casefold())


class FichaTests(unittest.TestCase):
    def test_recognizes_the_card_and_reads_its_sections(self):
        self.assertTrue(ficha.eh_ficha(RESPOSTA_DA_IA))
        self.assertFalse(ficha.eh_ficha("## Sumário Executivo\nTexto."))
        lidas = ficha.secoes(RESPOSTA_DA_IA)
        self.assertEqual(list(lidas)[:2], ["Situação", "Temas tocados"])
        self.assertEqual(lidas["Expectativa ou pedido"], "Estorno do ciclo.")

    def test_card_as_saved_has_fixed_titles_label_and_missing_sections(self):
        texto = ficha.montar_ficha(RESPOSTA_DA_IA)
        self.assertTrue(texto.startswith("## Ficha de contexto\n\n*" + prompts.ROTULO_DA_FICHA + "*"))
        titulos = [l[4:] for l in texto.splitlines() if l.startswith("### ")]
        self.assertEqual(tuple(titulos), prompts.SECOES_DA_FICHA)
        # A IA não escreveu "Lacunas": fica "Não disse.".
        self.assertEqual(ficha.secoes(texto)["Lacunas"], "Não disse.")

    def test_themes_come_from_the_saved_classification_and_the_alert_loses_its_evidence(self):
        temas = ficha.temas_do_codigo(CLASSIFICACAO, alertas={"Alerta Crítico"})
        texto = ficha.montar_ficha(RESPOSTA_DA_IA, temas)

        tocados = ficha.secoes(texto)["Temas tocados"]
        self.assertIn("- Equipamentos > máquina travou (negativa): máquina parou", tocados)
        self.assertIn("- Alerta Crítico (negativa)", tocados)
        self.assertNotIn("furto", tocados)
        self.assertNotIn("máquina parada (negativa)", tocados)

    def test_summary_for_the_page_is_the_situation(self):
        self.assertTrue(ficha.situacao(RESPOSTA_DA_IA).startswith("Cliente da unidade Moema"))


class GeralTests(unittest.TestCase):
    def test_the_general_analysis_gets_the_whole_card_compact_without_empty_sections(self):
        texto = ficha.montar_ficha(RESPOSTA_DA_IA)

        compacta = rel.sumario_executivo(texto)

        self.assertIn("**Situação:** Cliente da unidade Moema", compacta)
        self.assertIn("**Gatilho e causa:**", compacta)
        self.assertNotIn("Vínculo e risco", compacta)
        self.assertNotIn(prompts.ROTULO_DA_FICHA, compacta)

    def test_size_limit_applies(self):
        texto = ficha.montar_ficha(RESPOSTA_DA_IA.replace("Estorno do ciclo.", "palavra " * 400))
        self.assertLessEqual(len(rel.sumario_executivo(texto, 300)), 301)

    def test_old_analysis_still_uses_the_executive_summary(self):
        self.assertEqual(rel.sumario_executivo("## Sumário Executivo\nResumo antigo.\n## Perfil\nx"), "Resumo antigo.")
        self.assertEqual(rel.contar_sumarios([ficha.montar_ficha(RESPOSTA_DA_IA), "## Perfil\nx"]), (1, 1))


class RespostaTests(unittest.TestCase):
    def test_empty_answer_stays_empty_and_an_answer_becomes_a_card(self):
        self.assertEqual(ficha.ficha_da_resposta(""), "")
        self.assertEqual(ficha.ficha_da_resposta(None), "")
        self.assertTrue(ficha.eh_ficha(ficha.ficha_da_resposta(RESPOSTA_DA_IA)))


class PaginaDoAudioTests(unittest.TestCase):
    def test_the_audio_page_left_fpdf2_and_saves_the_card(self):
        from pathlib import Path

        fonte = (Path(__file__).resolve().parent.parent / "modules" / "prosodia" / "audio.py").read_text(encoding="utf-8")
        self.assertNotIn("pdf_report", fonte)
        self.assertIn("relatorio_pdf.ficha_pdf", fonte)
        self.assertEqual(fonte.count("_ficha_da_ia("), 3)
        for pagina in ("audios.py", "entrevistas.py", "whatsapp_monitor.py"):
            texto = (Path(__file__).resolve().parent.parent / "modules" / "prosodia" / pagina).read_text(encoding="utf-8")
            with self.subTest(pagina=pagina):
                self.assertIn("ficha_da_resposta(", texto)


try:
    from utils import relatorio_pdf as _pdf

    _COM_PDF = _pdf.disponivel()
except Exception:  # noqa: BLE001
    _COM_PDF = False


@unittest.skipUnless(_COM_PDF, "WeasyPrint sem o Pango")
class PdfDaFichaTests(unittest.TestCase):
    def test_renders_the_card_without_a_cover(self):
        from utils import relatorio_html

        html = relatorio_html.ficha_html(ficha.montar_ficha(RESPOSTA_DA_IA), projeto="Lava Já", titulo="Áudio #12",
                                         subtitulo="Moema", gerado_em="26/11/2026", modelo="gpt-4.1")
        self.assertIn("FICHA DO ÁUDIO", html)
        self.assertNotIn('class="capa', html)
        pdf = _pdf.ficha_pdf(ficha.montar_ficha(RESPOSTA_DA_IA), projeto="Lava Já", titulo="Áudio #12")
        self.assertTrue(pdf.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
