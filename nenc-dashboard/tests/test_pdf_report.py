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
        self.assertTrue(pdf_report._numeric_column(["43%", "1,5", "—", 6, "8,5 s"]))
        self.assertFalse(pdf_report._numeric_column(["Always", "43%"]))
        self.assertFalse(pdf_report._numeric_column(["—", ""]))


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
