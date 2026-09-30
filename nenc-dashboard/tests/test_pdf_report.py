"""PDF: texto que as fontes padrão desenham e relatório da Jornada que abre."""

import re
import unittest
import zlib

from tests.test_jornada_model import SAMPLES, SECONDS, _bundle, _file, _frames
from utils import pdf_report
from utils.jornada_metrics import compute_all
from utils.jornada_model import build_model
from utils.jornada_pdf import build_pdf
from utils.jornada_quality import run_quality


def _pdf_text(data: bytes) -> str:
    """Conteúdo das páginas (descomprimido), para procurar texto no teste."""
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            chunks.append(zlib.decompress(match.group(1)))
        except zlib.error:
            chunks.append(match.group(1))
    return b"\n".join(chunks).decode("cp1252", errors="replace")


class SanitizeTests(unittest.TestCase):
    def test_windows_1252_punctuation_survives(self):
        self.assertEqual(pdf_report.sanitize("a — “b” • c…"), "a — “b” • c…")

    def test_symbols_outside_the_font_become_readable_text(self):
        self.assertEqual(pdf_report.sanitize("n ≥ 5, δ = 0,3 → ok"), "n >= 5, delta = 0,3 -> ok")
        self.assertEqual(pdf_report.sanitize("日本"), "??")
        self.assertEqual(pdf_report.sanitize(None), "")
        self.assertEqual(pdf_report.sanitize(float("nan")), "")

    def test_numeric_columns_align_right(self):
        self.assertTrue(pdf_report.numeric_column(["43%", "1,5", "—", 6, "8,5 s"]))
        self.assertFalse(pdf_report.numeric_column(["Always", "43%"]))
        self.assertFalse(pdf_report.numeric_column(["—", ""]))


class PieceTests(unittest.TestCase):
    def setUp(self):
        self.pdf = pdf_report.ReportPDF("Teste")
        self.pdf.add_page()

    def test_markdown_lite_draws_titles_lists_bold_and_tables(self):
        pdf_report.render_markdown_lite(self.pdf, (
            "# Título\n\nTexto com **negrito**.\n\n- item\n1. primeiro\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n---\nFim"
        ))
        text = _pdf_text(pdf_report.output_bytes(self.pdf))
        for expected in ("Título", "negrito", "item", "primeiro", "Fim"):
            self.assertIn(expected, text)

    def test_a_long_table_repeats_its_header_on_the_next_page(self):
        rows = [["Linha {}".format(i), i] for i in range(120)]
        pdf_report.simple_table(self.pdf, ["Coluna de texto", "Valor"], rows, [120, 60])
        self.assertGreater(self.pdf.page_no(), 1)
        text = _pdf_text(pdf_report.output_bytes(self.pdf))
        self.assertGreaterEqual(text.count("Coluna de texto"), self.pdf.page_no())

    def test_headers_wrap_between_words_only(self):
        self.pdf.set_font(pdf_report.FONT, "B", 8)
        self.assertEqual(pdf_report._wrap(self.pdf, "Examinou", 14.5, 2), ["Examinou"])
        lines = pdf_report._wrap(self.pdf, "Tempo médio por participante em segundos", 20, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))

    def test_bars_accept_missing_values(self):
        pdf_report.hbar_chart(self.pdf, [("A", 0.5, True), ("B", float("nan"), False)],
                              value_format=lambda v: "{:.0%}".format(v), title="Barras")
        self.assertIn("Barras", _pdf_text(pdf_report.output_bytes(self.pdf)))


class ParagraphTests(unittest.TestCase):
    """Campo do projeto e trecho citado chegam com markdown colado de fora."""

    def setUp(self):
        self.pdf = pdf_report.ReportPDF("Teste")
        self.pdf.add_page()

    def test_inline_bold_is_applied_not_printed(self):
        pdf_report.paragraph(self.pdf, "Relatos com **pitch moderado**, sem extremos.")
        texto = _pdf_text(pdf_report.output_bytes(self.pdf))

        self.assertNotIn("**", texto)
        self.assertIn("pitch moderado", texto)

    def test_an_italic_paragraph_keeps_italic_around_the_bold(self):
        pdf_report.paragraph(self.pdf, "Citação com **ênfase** no meio.", style="I")
        fluxo = _pdf_text(pdf_report.output_bytes(self.pdf))

        self.assertNotIn("**", fluxo)
        self.assertIn("ênfase", fluxo)

    def test_plain_text_still_goes_through_multi_cell(self):
        pdf_report.paragraph(self.pdf, "Texto sem marcação nenhuma.")

        self.assertIn("Texto sem marcação nenhuma.",
                      _pdf_text(pdf_report.output_bytes(self.pdf)))


class MeasuredWidthsTests(unittest.TestCase):
    """Partes iguais quebram palavra no meio; a largura sai da fonte real."""

    def setUp(self):
        self.pdf = pdf_report.ReportPDF("Teste")
        self.pdf.add_page()

    def _cabe(self, headers, rows, widths):
        for indice, largura in enumerate(widths):
            for numero, linha in enumerate([list(headers)] + [list(r) for r in rows]):
                self.pdf.set_font(pdf_report.FONT, "B" if numero == 0 else "", 8.0)
                for palavra in pdf_report.sanitize(linha[indice]).split():
                    if self.pdf.get_string_width(palavra) > largura:
                        return False
        return True

    def test_no_word_is_wider_than_its_column(self):
        headers = ["Ordem", "Reclamação / Dor", "Frequência", "Evidências"]
        rows = [["1", "Equipamento com mau funcionamento", "Pontual",
                 "wa_+5521980007572_35"]]

        widths = pdf_report.measured_widths(self.pdf, headers, rows)

        self.assertTrue(self._cabe(headers, rows, widths))

    def test_the_widths_fill_the_content_width(self):
        headers = ["A", "B", "C"]
        rows = [["1", "2", "3"]]

        widths = pdf_report.measured_widths(self.pdf, headers, rows)

        self.assertAlmostEqual(sum(widths), pdf_report.CONTENT_WIDTH, places=3)

    def test_the_column_with_more_text_gets_more_room(self):
        headers = ["Nº", "Descrição"]
        rows = [["1", "Um texto bem mais longo do que o rótulo ao lado dele"]]

        estreita, larga = pdf_report.measured_widths(self.pdf, headers, rows)

        self.assertGreater(larga, estreita * 2)

    def test_it_degrades_instead_of_overflowing(self):
        """Nem os pisos cabem: reparte o que ha, sem estourar a pagina."""
        headers = ["Coluna", "Coluna", "Coluna", "Coluna", "Coluna", "Coluna"]
        rows = [["Palavraextremamentelongaquenaocabe"] * 6]

        widths = pdf_report.measured_widths(self.pdf, headers, rows)

        self.assertAlmostEqual(sum(widths), pdf_report.CONTENT_WIDTH, places=3)


class ProseTableTests(unittest.TestCase):
    """A tabela da IA e prosa: cortar com reticencias apagaria o conteudo."""

    def setUp(self):
        self.pdf = pdf_report.ReportPDF("Teste")
        self.pdf.add_page()

    def test_long_text_wraps_instead_of_being_cut(self):
        fim = "e termina aqui"
        rows = [["Higiene", "Corrigir prontamente as falhas de infraestrutura "
                            "apontadas pelos respondentes, {}".format(fim)]]

        pdf_report.prose_table(self.pdf, ["Classificação", "Recomendação"], rows)
        texto = _pdf_text(pdf_report.output_bytes(self.pdf))

        self.assertIn(fim, texto)
        self.assertNotIn("…", texto)

    def test_a_table_that_does_not_fit_breaks_the_page(self):
        rows = [["Tema {}".format(i), "Descrição razoavelmente longa " * 8]
                for i in range(40)]

        pdf_report.prose_table(self.pdf, ["Tema", "Descrição"], rows)
        texto = _pdf_text(pdf_report.output_bytes(self.pdf))

        self.assertGreater(self.pdf.page_no(), 1)
        # A ultima linha sobrevive a quebra: o y nao pode ficar preso na
        # pagina anterior, que foi o defeito que este teste pegou.
        self.assertIn("Tema 39", texto)

    def test_the_header_comes_back_after_a_page_break(self):
        rows = [["Tema {}".format(i), "Descrição razoavelmente longa " * 8]
                for i in range(40)]

        pdf_report.prose_table(self.pdf, ["Tema", "Classificação"], rows)
        texto = _pdf_text(pdf_report.output_bytes(self.pdf))

        self.assertGreaterEqual(texto.count("Classificação"), self.pdf.page_no())

    def test_empty_rows_draw_nothing(self):
        antes = self.pdf.get_y()

        pdf_report.prose_table(self.pdf, ["A", "B"], [])

        self.assertEqual(self.pdf.get_y(), antes)

    def test_a_wide_table_shrinks_instead_of_breaking_a_word(self):
        """As métricas acústicas têm 8 colunas e IDs longos."""
        headers = ["Áudio", "f0_media", "f0_variacao", "loudness_media",
                   "speaking_rate", "dim_arousal", "dim_valence", "dim_dominance"]
        rows = [["wa_+5521976287276_32", "193.597", "34.539", "0.895",
                 "5.251", "-0.071", "-0.006", "0.085"]]

        # Recuo da celula (1,4 de cada lado) + margem interna do multi_cell.
        folga = 2 * 1.4 + 2 * self.pdf.c_margin + 0.4
        corpo = pdf_report.fit_size(self.pdf, headers, rows, padding=folga)
        larguras = pdf_report.measured_widths(self.pdf, headers, rows,
                                              size=corpo, padding=folga)

        self.pdf.set_font(pdf_report.FONT, "", corpo)
        util = larguras[0] - 2 * 1.4 - 2 * self.pdf.c_margin
        self.assertLess(self.pdf.get_string_width("wa_+5521976287276_32"), util)

    def test_the_audio_id_survives_the_rendered_table(self):
        sid = "wa_+5521975310982_37"
        pdf_report.render_markdown_lite(self.pdf, (
            "| Áudio | f0_media | f0_variacao | loudness_media | speaking_rate "
            "| dim_arousal | dim_valence | dim_dominance |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| {} | 93.512 | 7.104 | 0.779 | 4.380 | -0.244 | 0.013 | -0.061 |\n"
        ).format(sid))
        texto = _pdf_text(pdf_report.output_bytes(self.pdf))

        self.assertIn(sid, texto)

    def test_markdown_tables_go_through_the_prose_table(self):
        """render_markdown_lite dividia em partes iguais e cortava o texto."""
        fim = "conclusao preservada"
        pdf_report.render_markdown_lite(self.pdf, (
            "| Insight | Recomendação |\n|---|---|\n"
            "| Elogios à equipe geram picos de ativação | "
            "Reforçar o reconhecimento em treinamentos, {} |\n".format(fim)
        ))

        self.assertIn(fim, _pdf_text(pdf_report.output_bytes(self.pdf)))


class JornadaReportTests(unittest.TestCase):
    def setUp(self):
        files = [
            _file(1, "DSP1234-INDIVIDUAL2.csv", SAMPLES),
            _file(2, "ATACADO-INDIVIDUAL2.csv", SECONDS),
            _file(3, "Pt01-JLivre-DSP1234.csv", _frames(200, 0.05)),
        ]
        self.bundle = _bundle(files)
        self.bundle["project"]["questions"] = "Visibilidade da marca foco\nComunicação da embalagem"
        self.model = build_model(self.bundle)
        self.metrics = compute_all(self.model, {})

    def test_the_report_opens_and_carries_every_section(self):
        data, name = build_pdf(
            self.bundle["project"], self.model, self.metrics,
            quality=run_quality(self.model, self.bundle["project"]),
            analysis={"model": "m", "mode": "rapida", "data_version": 2,
                      "analysis_text": "## Síntese\n\nMarca A **lidera**.",
                      "citations": [{"filename": "briefing.docx", "quote": "trecho"}]},
            generated_at="01/01/2026 10:00",
        )
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertTrue(name.endswith(".pdf"))
        text = _pdf_text(data)
        for expected in ("Principais achados", "Gôndola", "Amostra e qualidade", "Limitações",
                         "Perguntas do estudo", "Análise de IA", "Síntese", "briefing.docx",
                         # A analise e da versao 2; o projeto esta na 3.
                         "Os dados do projeto mudaram"):
            self.assertIn(expected, text)

    def test_an_empty_project_still_gives_a_report(self):
        model = build_model(_bundle([]))
        data, _ = build_pdf({"id": 5, "name": "Vazio"}, model, compute_all(model, {}))
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertIn("Sem achados", _pdf_text(data))


if __name__ == "__main__":
    unittest.main()
