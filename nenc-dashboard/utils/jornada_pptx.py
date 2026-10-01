"""
Apresentação (PPTX) da Análise Geral da Jornada de Compra.

16:9 e fundo branco, com gráficos nativos do PowerPoint (botão direito →
Editar dados mexe nos números) e tabelas nativas. Mesmo recorte e mesmos
números da página e do PDF; as tabelas completas ficam no Excel.

Cor por função: a marca foco em violeta NENC e as demais em cinza (quem diz a
marca é o rótulo, não a cor); o funil usa a rampa de acento do claro para o
escuro, na ordem notou → examinou → retornou; atributos usam azul, laranja e
verde-água, os três primeiros tons da paleta de referência, que se separam
entre si também para quem tem daltonismo. Valores ausentes ficam sem barra
(nunca viram zero).
"""

import io
import math
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from utils.jornada_export import export_filename, filters_text
from utils.jornada_format import fmt_number, fmt_pct
from utils.jornada_ingest import TASK_LABELS
from utils.jornada_metrics import decision_by_store, time_kpi
from utils.jornada_taxonomy import fold
from utils.pdf_report import numeric_column

SLIDE_WIDTH = Inches(13.333)
SLIDE_HEIGHT = Inches(7.5)
LEFT = Inches(0.6)
CONTENT_WIDTH = Inches(12.13)
BODY_TOP = Inches(1.55)
BODY_BOTTOM = Inches(6.85)
FONT = "Calibri"


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#").upper())


INK = _rgb("#1b1c24")
SECONDARY = _rgb("#52514e")
MUTED = _rgb("#898781")
RULE = _rgb("#e1e0d9")
ACCENT = _rgb("#5d5294")  # NENC accent-700: marca foco e títulos
FUNNEL = (_rgb("#b5abfc"), _rgb("#9184d9"), _rgb("#5d5294"))  # accent-400 → accent → accent-700
BASE_BAR = _rgb("#a3a6b3")
CATEGORICAL = (_rgb("#2a78d6"), _rgb("#eb6834"), _rgb("#1baf7a"))  # azul, laranja, verde-água
OTHER = _rgb("#a3a6b3")
HEADER_FILL = _rgb("#eeedf6")
BAND_FILL = _rgb("#f7f7f9")
WHITE = _rgb("#ffffff")

MAX_TABLE_ROWS = 12
_CONTROL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


# ---------------------------------------------------------------------------
# Valores
# ---------------------------------------------------------------------------

def _clean(value) -> str:
    """Texto que o XML do Office aceita."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return _CONTROL.sub("", str(value))


def _number(value) -> Optional[float]:
    """NaN, infinito e vazio viram None: o gráfico deixa a barra em branco."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _dash(text) -> str:
    return text if text not in ("", None) else "—"


def _pct(value) -> str:
    return _dash(fmt_pct(value))


def _num(value, digits: int = 1) -> str:
    return _dash(fmt_number(value, digits))


def _frame(value) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _luminance(color: RGBColor) -> float:
    def channel(value: int) -> float:
        value = value / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = color
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _label_on(fill: RGBColor) -> RGBColor:
    """Branco ou tinta, o que tiver mais contraste com o preenchimento."""
    lum = _luminance(fill)
    return WHITE if (1.05 / (lum + 0.05)) >= ((lum + 0.05) / (_luminance(INK) + 0.05)) else INK


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------

def _style_run(run, size: float, *, bold: bool = False, color: RGBColor = INK) -> None:
    font = run.font
    font.name = FONT
    font.size = Pt(size)
    font.bold = bold
    font.color.rgb = color


def _add_runs(paragraph, text, size: float, *, color: RGBColor = INK, bold: bool = False) -> None:
    """Texto com **negrito** inline, em runs."""
    for index, part in enumerate(_clean(text).split("**")):
        if part:
            run = paragraph.add_run()
            run.text = part
            _style_run(run, size, bold=bold or index % 2 == 1, color=color)


def _textbox(slide, left, top, width, height, text="", *, size: float = 14, bold: bool = False,
             color: RGBColor = INK, align=None, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    if text:
        paragraph = frame.paragraphs[0]
        if align is not None:
            paragraph.alignment = align
        _add_runs(paragraph, text, size, color=color, bold=bold)
    return box


def _bullet(paragraph, indent=Inches(0.28)) -> None:
    """Marcador com recuo pendente, na cor de acento."""
    properties = paragraph._p.get_or_add_pPr()
    properties.set("marL", str(int(indent)))
    properties.set("indent", str(-int(indent)))
    color = OxmlElement("a:buClr")
    rgb = OxmlElement("a:srgbClr")
    rgb.set("val", str(ACCENT))
    color.append(rgb)
    properties.append(color)
    char = OxmlElement("a:buChar")
    char.set("char", "•")
    properties.append(char)


def _write_items(frame, items: Sequence[Tuple[str, str]], size: float) -> None:
    """Itens ("h" título, "b" marcador, "p" parágrafo) numa caixa de texto."""
    for index, (kind, text) in enumerate(items):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        if kind == "h":
            paragraph.space_before = Pt(0 if index == 0 else 10)
            paragraph.space_after = Pt(4)
            _add_runs(paragraph, text, size + 2, color=ACCENT, bold=True)
        elif kind == "b":
            paragraph.space_after = Pt(6)
            _bullet(paragraph)
            _add_runs(paragraph, text, size)
        else:
            paragraph.space_after = Pt(8)
            _add_runs(paragraph, text, size)


def _markdown_items(text: str) -> List[Tuple[str, str]]:
    items = []
    in_table = False
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            in_table = False
        if not line or line in ("---", "***", "___"):
            continue
        if line.startswith("|"):
            cells = [cell.strip().replace("**", "") for cell in line.strip("|").split("|")]
            if all(set(cell) <= set("-: ") for cell in cells):
                continue
            text_line = " · ".join(cells)
            items.append(("p", text_line if in_table else "**{}**".format(text_line)))
            in_table = True
        elif line.startswith("#"):
            items.append(("h", line.lstrip("#").strip().replace("**", "")))
        elif line[:2] in ("- ", "* ", "• "):
            items.append(("b", line[2:].strip()))
        else:
            items.append(("p", line))
    return items


# ---------------------------------------------------------------------------
# Deck
# ---------------------------------------------------------------------------

class _Deck:
    def __init__(self, footer: str):
        self.prs = Presentation()
        self.prs.slide_width = SLIDE_WIDTH
        self.prs.slide_height = SLIDE_HEIGHT
        self.layout = self.prs.slide_layouts[6]  # em branco
        self.footer = footer

    def slide(self, title: str, subtitle: str = ""):
        slide = self.prs.slides.add_slide(self.layout)
        _textbox(slide, LEFT, Inches(0.42), CONTENT_WIDTH, Inches(0.6), title, size=26, bold=True)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, LEFT, Inches(1.04), Inches(0.55), Inches(0.05))
        bar.fill.solid()
        bar.fill.fore_color.rgb = ACCENT
        bar.line.fill.background()
        if subtitle:
            _textbox(slide, LEFT, Inches(1.14), CONTENT_WIDTH, Inches(0.35), subtitle, size=12, color=SECONDARY)
        _textbox(slide, LEFT, Inches(7.02), Inches(10.5), Inches(0.3), self.footer, size=9, color=MUTED)
        _textbox(slide, Inches(11.73), Inches(7.02), Inches(1.0), Inches(0.3), str(len(self.prs.slides)),
                 size=9, color=MUTED, align=PP_ALIGN.RIGHT)
        return slide

    def flow(self, title: str, items: Sequence[Tuple[str, str]], *, subtitle: str = "",
             size: float = 16) -> None:
        """Itens de texto distribuídos em quantos slides precisarem.

        A conta é em linhas do corpo: a caixa tem 5,3 pol (381 pt) e cada linha
        ocupa 1,2 × o tamanho da fonte; cabem ~1.700/tamanho caracteres por
        linha. Uma linha fica de folga para a estimativa não estourar a caixa.
        """
        line_pt = size * 1.2
        capacity = (BODY_BOTTOM - BODY_TOP) / 12700 / line_pt - 1.0
        chars_per_line = int(1700 / size)
        spacing = {"h": 14 / line_pt, "b": 6 / line_pt, "p": 8 / line_pt}
        pages: List[List[Tuple[str, str]]] = []
        current: List[Tuple[str, str]] = []
        used = 0.0
        for kind, text in items:
            lines = max(1, math.ceil(len(_clean(text)) / chars_per_line))
            cost = (lines * (size + 2) / size if kind == "h" else lines) + spacing.get(kind, 0.4)
            if current and used + cost > capacity:
                pages.append(current)
                current, used = [], 0.0
            current.append((kind, text))
            used += cost
        if current:
            pages.append(current)
        for index, page in enumerate(pages):
            slide = self.slide(title if index == 0 else "{} (continuação)".format(title),
                               subtitle if index == 0 else "")
            box = _textbox(slide, LEFT, BODY_TOP, CONTENT_WIDTH, BODY_BOTTOM - BODY_TOP)
            _write_items(box.text_frame, page, size)

    def table_slides(self, title: str, headers: Sequence[str], rows: Sequence[Sequence],
                     widths: Sequence[float], *, subtitle: str = "") -> None:
        for start in range(0, max(1, len(rows)), MAX_TABLE_ROWS):
            chunk = rows[start:start + MAX_TABLE_ROWS]
            if not chunk:
                return
            slide = self.slide(title if start == 0 else "{} (continuação)".format(title),
                               subtitle if start == 0 else "")
            _table(slide, LEFT, BODY_TOP, headers, chunk, widths)

    def save(self) -> bytes:
        buffer = io.BytesIO()
        self.prs.save(buffer)
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# Tabelas e números-chave
# ---------------------------------------------------------------------------

def _cell(cell, value, size: float, *, bold: bool = False, fill: RGBColor = WHITE, align: str = "L") -> None:
    cell.fill.solid()
    cell.fill.fore_color.rgb = fill
    cell.margin_left = cell.margin_right = Inches(0.08)
    cell.margin_top = cell.margin_bottom = Inches(0.03)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    paragraph = cell.text_frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.RIGHT if align == "R" else PP_ALIGN.LEFT
    run = paragraph.add_run()
    run.text = _clean(value)
    _style_run(run, size, bold=bold, color=INK)


def _table(slide, left, top, headers: Sequence[str], rows: Sequence[Sequence], widths: Sequence[float],
           *, size: float = 11, row_height: float = 0.34) -> None:
    """Tabela nativa: cabeçalho em tom de acento, linhas alternadas, números à direita."""
    align = ["R" if numeric_column([row[index] for row in rows]) else "L" for index in range(len(headers))]
    shape = slide.shapes.add_table(len(rows) + 1, len(headers), left, top,
                                   Inches(sum(widths)), Inches(row_height * (len(rows) + 1)))
    table = shape.table
    for index, width in enumerate(widths):
        table.columns[index].width = Inches(width)
    for row in table.rows:
        row.height = Inches(row_height)
    for index, header in enumerate(headers):
        _cell(table.cell(0, index), header, size, bold=True, fill=HEADER_FILL, align=align[index])
    for row_index, row in enumerate(rows, start=1):
        fill = WHITE if row_index % 2 else BAND_FILL
        for index, value in enumerate(row):
            _cell(table.cell(row_index, index), value, size, fill=fill, align=align[index])


def _kpis(slide, top, items: Sequence[Tuple[str, str]], *, height=Inches(1.25)) -> None:
    if not items:
        return
    gap = Inches(0.25)
    width = int((CONTENT_WIDTH - gap * (len(items) - 1)) / len(items))
    for index, (label, value) in enumerate(items):
        x = LEFT + index * (width + gap)
        box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, top, width, height)
        box.fill.solid()
        box.fill.fore_color.rgb = WHITE
        box.line.color.rgb = RULE
        box.line.width = Pt(0.75)
        box.shadow.inherit = False
        _textbox(slide, x + Inches(0.18), top + Inches(0.12), width - Inches(0.36), Inches(0.45), label,
                 size=12, color=SECONDARY)
        _textbox(slide, x + Inches(0.18), top + Inches(0.6), width - Inches(0.36), Inches(0.55), value,
                 size=28, bold=True)


# ---------------------------------------------------------------------------
# Gráficos nativos
# ---------------------------------------------------------------------------

def _chart_base(chart, title: str, *, legend: bool) -> None:
    chart.font.name = FONT
    chart.font.size = Pt(11)
    chart.font.color.rgb = SECONDARY
    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(11)
    if title:
        chart.has_title = True
        frame = chart.chart_title.text_frame
        frame.text = _clean(title)
        for run in frame.paragraphs[0].runs:
            _style_run(run, 13, bold=True)
    else:
        chart.has_title = False


def _axes(chart, maximum: Optional[float] = None) -> None:
    categories = chart.category_axis
    categories.reverse_order = True  # primeira categoria no topo, como na tela
    categories.has_major_gridlines = False
    categories.tick_labels.font.size = Pt(11)
    categories.format.line.color.rgb = RULE
    values = chart.value_axis
    values.has_major_gridlines = False
    values.visible = False  # os valores estão nos rótulos
    values.minimum_scale = 0
    if maximum:
        values.maximum_scale = maximum


def _gap_width(height, categories: int, per_category: int = 1, *, legend: bool = False,
               bar_in: float = 0.3) -> int:
    """Espaço entre grupos (% da barra) para cada barra ter ~`bar_in` polegadas."""
    plot_in = height / 914400 - 0.45 - (0.4 if legend else 0.0)
    band = plot_in / max(1, categories)
    return int(max(40, min(400, (band / bar_in - per_category) * 100)))


def _bar_chart(slide, box, categories, values, *, highlight=None, number_format: str = "0%",
               maximum: Optional[float] = None, title: str = "", color: RGBColor = ACCENT):
    x, y, width, height = box
    data = CategoryChartData(number_format=number_format)
    data.categories = [_clean(c) for c in categories]
    data.add_series("Valor", [_number(v) for v in values])
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, x, y, width, height, data).chart
    _chart_base(chart, title, legend=False)
    plot = chart.plots[0]
    plot.gap_width = _gap_width(height, len(categories))
    plot.vary_by_categories = False
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = number_format
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size = Pt(11)
    labels.font.color.rgb = SECONDARY
    series = plot.series[0]
    series.format.fill.solid()
    series.format.fill.fore_color.rgb = color
    if highlight is not None:
        for index, flag in enumerate(highlight):
            fill = series.points[index].format.fill
            fill.solid()
            fill.fore_color.rgb = ACCENT if flag else BASE_BAR
    _axes(chart, maximum)
    return chart


def _clustered_chart(slide, box, categories, series: Sequence[Tuple[str, Sequence]], colors, *,
                     number_format: str = "0%", maximum: Optional[float] = None, title: str = ""):
    x, y, width, height = box
    data = CategoryChartData(number_format=number_format)
    data.categories = [_clean(c) for c in categories]
    for name, values in series:
        data.add_series(_clean(name), [_number(v) for v in values])
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, x, y, width, height, data).chart
    _chart_base(chart, title, legend=True)
    plot = chart.plots[0]
    plot.gap_width = _gap_width(height, len(categories), len(series), legend=True, bar_in=0.22)
    plot.overlap = -8  # fresta entre as barras do grupo
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = number_format
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size = Pt(10)
    labels.font.color.rgb = SECONDARY
    for chart_series, color in zip(plot.series, colors):
        chart_series.format.fill.solid()
        chart_series.format.fill.fore_color.rgb = color
    _axes(chart, maximum)
    return chart


def _stacked_chart(slide, box, categories, series: Sequence[Tuple[str, Sequence]], colors, *, title: str = ""):
    """Barras 100% empilhadas; rótulo só em segmento que cabe (≥ 8%)."""
    x, y, width, height = box
    data = CategoryChartData(number_format="0%")
    data.categories = [_clean(c) for c in categories]
    cleaned = [(name, [_number(v) for v in values]) for name, values in series]
    for name, values in cleaned:
        data.add_series(_clean(name), values)
    chart = slide.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED_100, x, y, width, height, data).chart
    _chart_base(chart, title, legend=True)
    plot = chart.plots[0]
    plot.gap_width = _gap_width(height, len(categories), legend=True, bar_in=0.45)
    plot.overlap = 100
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = "0%"
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.CENTER
    labels.font.size = Pt(11)
    labels.font.bold = True
    totals = [sum(values[i] or 0 for _, values in cleaned) for i in range(len(categories))]
    for (name, values), chart_series, color in zip(cleaned, plot.series, colors):
        chart_series.format.fill.solid()
        chart_series.format.fill.fore_color.rgb = color
        # Fresta branca entre os segmentos.
        chart_series.format.line.color.rgb = WHITE
        chart_series.format.line.width = Pt(1.5)
        series_labels = chart_series.data_labels
        series_labels.show_value = True
        series_labels.position = XL_LABEL_POSITION.CENTER
        series_labels.number_format = "0%"
        series_labels.number_format_is_linked = False
        series_labels.font.color.rgb = _label_on(color)
        series_labels.font.size = Pt(11)
        series_labels.font.bold = True
        for index, value in enumerate(values):
            if value is None or not totals[index] or value / totals[index] < 0.08:
                chart_series.points[index].data_label.text_frame.text = ""
    _axes(chart, 1.0)
    return chart


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------

def _cover(deck: _Deck, project: Dict, model: Dict, metrics: Dict, generated_at: str) -> None:
    slide = deck.prs.slides.add_slide(deck.layout)
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.22), SLIDE_HEIGHT)
    band.fill.solid()
    band.fill.fore_color.rgb = ACCENT
    band.line.fill.background()
    focus = (model.get("meta") or {}).get("focus_brand") or ""
    _textbox(slide, Inches(0.9), Inches(2.1), Inches(11.6), Inches(0.45), "JORNADA DE COMPRA · ANÁLISE GERAL",
             size=14, bold=True, color=ACCENT)
    _textbox(slide, Inches(0.9), Inches(2.6), Inches(11.6), Inches(1.1), project.get("name") or "Projeto",
             size=44, bold=True)
    details = ["Marca foco: {}".format(focus)] if focus else []
    details.append("Gerado em {}".format(generated_at))
    if project.get("data_version") is not None:
        details.append("dados na versão {}".format(project["data_version"]))
    _textbox(slide, Inches(0.9), Inches(3.85), Inches(11.6), Inches(0.4), " · ".join(details), size=16,
             color=SECONDARY)
    _textbox(slide, Inches(0.9), Inches(4.35), Inches(11.6), Inches(0.6),
             "Recorte: {}".format(filters_text(metrics.get("filters"), model)), size=13, color=MUTED)


def _key_numbers(deck: _Deck, project: Dict, model: Dict, metrics: Dict) -> None:
    slide = deck.slide("Números-chave", "Só entram as gravações incluídas na análise.")
    sample = metrics.get("sample") or {}
    recordings = _frame(model.get("recordings"))
    uncoded = int((recordings["status"] == "nao_codificada").sum()) if not recordings.empty else 0
    time_label, time_value = time_kpi(metrics)
    _kpis(slide, BODY_TOP, [
        ("Participantes na análise", str(sample.get("participants", 0))),
        ("Gravações incluídas", str(sample.get("recordings", 0))),
        ("Não codificadas (fora)", str(uncoded)),
        (time_label, "{} s".format(fmt_number(time_value, 1)) if time_value == time_value else "—"),
    ])
    top = BODY_TOP + Inches(1.55)
    focus = (model.get("meta") or {}).get("focus_brand") or ""
    brand = _frame(metrics.get("brand"))
    if focus and not brand.empty and brand["is_focus"].any():
        _textbox(slide, LEFT, top, CONTENT_WIDTH, Inches(0.3),
                 "Share visual de {} por célula (média por participante)".format(focus), size=12, color=SECONDARY)
        _kpis(slide, top + Inches(0.38), [
            ("{} · n={}".format(row["cell"], int(row["n"])), _pct(row["share_mean"]))
            for _, row in brand[brand["is_focus"]].head(4).iterrows()
        ])
        top += Inches(1.95)
    questions = [line.strip(" -•\t") for line in str(project.get("questions") or "").splitlines()
                 if line.strip(" -•\t")]
    if questions:
        box = _textbox(slide, LEFT, top + Inches(0.1), CONTENT_WIDTH, BODY_BOTTOM - top - Inches(0.1))
        _write_items(box.text_frame, [("h", "Perguntas do estudo")] + [("b", q) for q in questions[:5]], 14)


def _findings(deck: _Deck, metrics: Dict) -> None:
    findings = metrics.get("findings") or []
    areas = (("Gôndola", ("gondola",)), ("Navegação e decisão", ("preco", "navegacao", "decisao")),
             ("Embalagens", ("embalagem",)))
    items: List[Tuple[str, str]] = []
    for area, sections in areas:
        chosen = [f for f in findings if f.get("section") in sections][:6]
        if chosen:
            items.append(("h", area))
            items += [("b", "{}{}".format(f["text"], " (descritivo)" if f.get("strength") == "descritivo" else ""))
                      for f in chosen]
    deck.flow("Principais achados", items or [("p", "Sem achados para este recorte.")],
              subtitle="Frases geradas das métricas, com o n de cada célula.", size=15)


def _gondola(deck: _Deck, metrics: Dict, focus: str) -> None:
    brand = _frame(metrics.get("brand"))
    if brand.empty:
        return
    cells = list(brand.groupby("cell", sort=False))
    maximum = min(1.0, math.ceil((brand["share_mean"].max() or 0.1) * 10 + 0.5) / 10)
    gap = Inches(0.3)
    width = int((CONTENT_WIDTH - gap) / 2)
    height = Inches(2.55)
    for start in range(0, len(cells), 4):
        slide = deck.slide(
            "Share visual por marca",
            "Média, por participante, da fração da atenção às marcas que cada uma levou. "
            "{} em destaque; mesma escala em todos os quadros.".format(focus or "Marca foco"),
        )
        for index, (cell, rows) in enumerate(cells[start:start + 4]):
            ranked = rows.sort_values("share_mean", ascending=False)
            n = int(rows["n"].iloc[0])
            box = (LEFT + (index % 2) * (width + gap), BODY_TOP + (index // 2) * (height + Inches(0.12)),
                   width, height)
            _bar_chart(slide, box, ranked["brand"].tolist(), ranked["share_mean"].tolist(),
                       highlight=ranked["is_focus"].astype(bool).tolist(), maximum=maximum,
                       title="{} · n={}{}".format(cell, n, " (descritivo)" if n < 5 else ""))

    headers = ["Célula", "Marca", "n", "Share", "Notou", "Examinou", "Retornou", "1ª notada", "TTFF rel. (s)",
               "Índice"]
    widths = [3.1, 1.6, 0.6, 0.95, 0.95, 1.05, 1.05, 1.05, 1.1, 0.95]

    def table_rows(frame):
        return [[row["cell"], row["brand"], int(row["n"]), _pct(row["share_mean"]), _pct(row["reach"]),
                 _pct(row["examined"]), _pct(row["revisit"]), _pct(row["first_noticed"]),
                 _num(row["rel_ttff_median"]), _num(row["presence_index"], 2)] for _, row in frame.iterrows()]

    note = ("TTFF relativo = segundos depois da primeira marca vista. Índice = share ÷ fração da gôndola da "
            "marca (acima de 1, rende mais atenção que o espaço que ocupa).")
    focus_rows = brand[brand["is_focus"]] if focus else brand.iloc[0:0]
    if not focus_rows.empty:
        together = len(focus_rows) <= 6
        slide = deck.slide(
            "Funil de atenção — {}".format(focus),
            "Notou = olhou; examinou = ao menos o limiar de exame na marca; retornou = voltou a uma AOI dela.",
        )
        table_height = Inches(0.34 * (len(focus_rows) + 1)) if together else 0
        chart_height = BODY_BOTTOM - BODY_TOP - (table_height + Inches(0.55) if together else 0)
        _clustered_chart(
            slide, (LEFT, BODY_TOP, CONTENT_WIDTH, chart_height),
            focus_rows["cell"].tolist(),
            [("Notou", focus_rows["reach"].tolist()), ("Examinou", focus_rows["examined"].tolist()),
             ("Retornou", focus_rows["revisit"].tolist())],
            FUNNEL, maximum=1.0,
        )
        if together:
            top = BODY_TOP + chart_height + Inches(0.1)
            _textbox(slide, LEFT, top, CONTENT_WIDTH, Inches(0.3), note, size=10, color=MUTED)
            _table(slide, LEFT, top + Inches(0.38), headers, table_rows(focus_rows), widths)
        else:
            deck.table_slides("Primeira olhada e presença — {}".format(focus), headers,
                              table_rows(focus_rows), widths, subtitle=note)
    else:
        ordered = pd.concat([rows.sort_values("share_mean", ascending=False) for _, rows in cells])
        deck.table_slides("Funil, primeira olhada e presença", headers, table_rows(ordered), widths,
                          subtitle=note)

    sku = _frame(metrics.get("sku"))
    if not sku.empty:
        top = pd.concat([rows.sort_values("share_mean", ascending=False).head(5)
                         for _, rows in sku.groupby("cell", sort=False)])
        deck.table_slides(
            "Produtos mais vistos",
            ["Célula", "Produto", "Marca", "Share", "Alcance", "Tempo médio (s)"],
            [[row["cell"], row["product"], row["brand"], _pct(row["share_mean"]), _pct(row["reach"]),
              _num(row["dwell_mean_s"], 2)] for _, row in top.iterrows()],
            [3.2, 3.9, 1.7, 1.0, 1.0, 1.3],
            subtitle="Os cinco produtos com maior share em cada loja.",
        )


def _navigation(deck: _Deck, model: Dict, metrics: Dict) -> None:
    attributes = _frame(metrics.get("attributes"))
    if not attributes.empty:
        configured = (model.get("meta") or {}).get("dimensions") or {}
        dimensions = [d for d in configured if d in set(attributes["dimension"])] or sorted(
            attributes["dimension"].unique())
        gap = Inches(0.3)
        width = int((CONTENT_WIDTH - gap) / 2)
        for start in range(0, len(dimensions), 2):
            slide = deck.slide("Atributos dos produtos",
                               "Fração da atenção entre os produtos em que o atributo aparece no nome da AOI.")
            for index, dimension in enumerate(dimensions[start:start + 2]):
                rows = attributes[attributes["dimension"] == dimension]
                cells = list(dict.fromkeys(rows["cell"]))
                order = [v for v in configured.get(dimension, []) if v in set(rows["value"])]
                order += sorted(set(rows["value"]) - set(order))
                pivot = rows.pivot_table(index="cell", columns="value", values="share_mean", aggfunc="first")
                colors = [CATEGORICAL[i] if i < len(CATEGORICAL) else OTHER for i in range(len(order))]
                _stacked_chart(
                    slide, (LEFT + index * (width + gap), BODY_TOP, width, BODY_BOTTOM - BODY_TOP),
                    cells, [(value, [pivot.loc[c, value] if value in pivot.columns else None for c in cells])
                            for value in order],
                    colors, title=str(dimension).capitalize(),
                )

        if "presence" in attributes:
            deck.table_slides(
                "Atributos: atenção × presença na gôndola",
                ["Célula", "Atributo", "Valor", "n", "Share", "Presença", "Índice"],
                [[row["cell"], row["dimension"], row["value"], int(row["n_defined"]), _pct(row["share_mean"]),
                  _pct(row["presence"]), _num(row["presence_index"], 2)] for _, row in attributes.iterrows()],
                [3.4, 1.6, 1.6, 0.7, 1.3, 1.5, 1.2],
                subtitle="Índice = share ÷ presença do valor na gôndola: acima de 1, atenção além do espaço "
                         "que o valor ocupa; perto de 1, proporcional.",
            )

    price = _frame(metrics.get("price"))
    if not price.empty:
        deck.table_slides(
            "Etiquetas de preço",
            ["Célula", "Produto", "Viu o preço", "Tempo médio (s)", "Preço ÷ (preço + produto)"],
            [[row["cell"], row["product"], _pct(row["reach"]), _num(row["dwell_mean_s"], 2),
              _pct(row["price_fraction"])] for _, row in price.iterrows()],
            [3.3, 3.8, 1.5, 1.6, 2.0],
            subtitle="Atenção às etiquetas de preço, onde estão mapeadas.",
        )

    decision = _frame(metrics.get("decision"))
    if not decision.empty:
        slide = deck.slide(
            "Tempo até a decisão",
            "Mediana por tarefa e loja, em segundos: o tempo de compra do registro de campo e, onde ele não "
            "existe, o Tempo da planilha.",
        )
        stores = decision_by_store(decision)
        if not stores.empty:
            _bar_chart(slide, (LEFT, BODY_TOP, Inches(5.6), Inches(3.4)), stores["label"].tolist(),
                       stores["median_s"].tolist(), number_format='0.0" s"', title="Mediana por tarefa e loja")
        table = decision[decision["group_type"] == "loja"]
        _table(slide, LEFT + Inches(6.0), BODY_TOP, ["Tarefa", "Fonte", "Loja", "n", "Mediana (s)"],
               [[row["task_label"], row["source_label"], row["group"], int(row["n"]), _num(row["median_s"])]
                for _, row in table.head(MAX_TABLE_ROWS).iterrows()],
               [1.5, 1.85, 1.15, 0.45, 1.15])


def _packaging(deck: _Deck, metrics: Dict, focus: str) -> None:
    packaging = metrics.get("packaging") or {}
    elements = _frame(packaging.get("elements"))
    if elements.empty:
        return
    brands = _frame(packaging.get("brands"))
    coverage = _frame(packaging.get("coverage"))
    group = "Todos" if (elements["group"] == "Todos").any() else elements["group"].iloc[0]
    rows = elements[elements["group"] == group]
    subtitle = "Agregado por perfil, sem variação entre participantes · base: {}, n={}".format(
        rows["profile"].iloc[0], int(rows["n_group"].max()))
    cov = coverage[coverage["group"] == group] if not coverage.empty else coverage
    if not cov.empty and cov["aoi_coverage"].notna().any():
        subtitle += " · elementos somam {} do tempo gravado".format(_pct(cov["aoi_coverage"].iloc[0]))
    slide = deck.slide("Embalagens", subtitle)
    focus_rows = rows[rows["brand"].map(fold) == fold(focus)] if focus else rows.iloc[0:0]
    if not focus_rows.empty:
        ranked = focus_rows.sort_values("element_share", ascending=False)
        _bar_chart(slide, (LEFT, BODY_TOP, Inches(6.2), BODY_BOTTOM - BODY_TOP), ranked["element_label"].tolist(),
                   ranked["element_share"].tolist(), title="Onde o olhar cai na embalagem de {}".format(focus))
    summary = []
    for brand_name, brand_rows in rows.groupby("brand", sort=False):
        top = brand_rows.sort_values("element_share", ascending=False).iloc[0]
        logo = brands[(brands["group"] == group) & (brands["brand"] == brand_name)] if not brands.empty else brands
        summary.append([brand_name, top["element_label"], _pct(top["element_share"]),
                        _pct(logo["logo_reach"].iloc[0]) if not logo.empty else "—"])
    _textbox(slide, LEFT + Inches(6.6), BODY_TOP, Inches(5.5), Inches(0.35), "Elemento que mais atrai",
             size=13, bold=True)
    _table(slide, LEFT + Inches(6.6), BODY_TOP + Inches(0.45), ["Marca", "Elemento mais visto", "Share", "Logo visto"],
           summary[:MAX_TABLE_ROWS], [1.45, 1.95, 0.9, 1.2])

    if focus and not brands.empty:
        logos = brands[brands["brand"].map(fold) == fold(focus)]
        if len(logos) > 1:
            slide = deck.slide("Logo de {} visto, por perfil".format(focus),
                               "Fração do grupo que olhou o elemento de marca da embalagem.")
            _bar_chart(slide, (LEFT, BODY_TOP, CONTENT_WIDTH, Inches(4.2)),
                       ["{} (n={})".format(row["profile"], int(row["n_group"])) for _, row in logos.iterrows()],
                       logos["logo_reach"].tolist(), highlight=(logos["group"] == "Todos").tolist(), maximum=1.0)


def _channel(deck: _Deck, metrics: Dict) -> None:
    confounds = metrics.get("confounds") or []
    comparisons = _frame(metrics.get("comparisons"))
    if not confounds and comparisons.empty:
        return
    slide = deck.slide("Canal e perfil", "Com menos de 5 participantes por grupo a comparação é só descritiva.")
    top = BODY_TOP
    if confounds:
        box = _textbox(slide, LEFT, top, CONTENT_WIDTH, Inches(0.4 + 0.45 * len(confounds)))
        _write_items(box.text_frame, [
            ("b", "**{} e {} andam juntos nesta amostra** ({}): uma diferença entre um pode ser do outro, e a "
                  "comparação não isola o efeito.".format(first, second.lower(), confound["mapping"]))
            for confound in confounds for first, second in [confound["labels"]]
        ], 14)
        top += Inches(0.55 + 0.45 * len(confounds))
    if not comparisons.empty:
        _table(slide, LEFT, top,
               ["Tarefa", "Por", "Grupo A", "Grupo B", "Média A", "Média B", "δ de Cliff", "p", "Método"],
               [[TASK_LABELS.get(row["task"], row["task"]), row["by"],
                 "{} (n={})".format(row["group_a"], row["n_a"]), "{} (n={})".format(row["group_b"], row["n_b"]),
                 _pct(row["mean_a"]), _pct(row["mean_b"]), _num(row["cliffs_delta"], 2), _num(row["p_value"], 3),
                 row["method"]] for _, row in comparisons.head(MAX_TABLE_ROWS - 2).iterrows()],
               [1.6, 0.7, 2.1, 2.1, 0.95, 0.95, 1.05, 0.7, 1.95])


def _sample(deck: _Deck, model: Dict, metrics: Dict, quality: Optional[Dict]) -> None:
    subtitle = ""
    summary = _frame((quality or {}).get("summary"))
    if not summary.empty:
        tally = summary["quality"].value_counts()
        subtitle = "Qualidade das gravações: {} OK, {} com atenção, {} com problema.".format(
            int(tally.get("pass", 0)), int(tally.get("warn", 0)), int(tally.get("fail", 0)))
    slide = deck.slide("Amostra e qualidade", subtitle)
    by_cell = (metrics.get("sample") or {}).get("by_cell") or []
    if by_cell:
        _table(slide, LEFT, BODY_TOP, ["Célula", "Participantes"],
               [[item["cell"], item["n"]] for item in by_cell[:MAX_TABLE_ROWS]], [3.6, 1.5])
    recordings = _frame(model.get("recordings"))
    if not recordings.empty:
        titles = {"incluida": "Incluída", "nao_codificada": "Não codificada", "excluida": "Excluída",
                  "agregado": "Só agregado", "sem_aoi": "Sem AOI"}
        counts = recordings.groupby(["task_label", "status"]).size().unstack(fill_value=0)
        statuses = [s for s in titles if s in counts.columns]
        _table(slide, LEFT + Inches(5.5), BODY_TOP, ["Tarefa"] + [titles[s] for s in statuses],
               [[task] + [int(counts.loc[task, s]) for s in statuses] for task in counts.index],
               [1.9] + [4.7 / max(1, len(statuses))] * len(statuses))
    issues = [i for i in model.get("issues") or [] if i.get("level") in ("warn", "error")]
    if issues:
        box = _textbox(slide, LEFT, Inches(4.6), CONTENT_WIDTH, Inches(2.2))
        items = [("h", "Avisos dos dados")] + [("b", i["message"]) for i in issues[:5]]
        if len(issues) > 5:
            items.append(("p", "Mais {} aviso(s) no Excel (aba Avisos).".format(len(issues) - 5)))
        _write_items(box.text_frame, items, 12)


def _analysis(deck: _Deck, analysis: Dict, data_version) -> None:
    modes = {"rapida": "modo rápido", "aprofundada": "modo aprofundado"}
    details = [analysis.get("model") or "", modes.get(analysis.get("mode"), analysis.get("mode") or ""),
               "gerada em {}".format(analysis["created_at"]) if analysis.get("created_at") else ""]
    subtitle = " · ".join(d for d in details if d)
    if analysis.get("data_version") is not None and analysis.get("data_version") != data_version:
        subtitle += " · os dados mudaram depois desta análise"
    items = _markdown_items(analysis.get("analysis_text") or "")
    citations = analysis.get("citations") or []
    if citations:
        items.append(("h", "Referências"))
        for citation in citations[:8]:
            data = citation if isinstance(citation, dict) else {"quote": str(citation)}
            quote = str(data.get("quote") or "").strip().replace("**", "")
            if len(quote) > 200:
                quote = quote[:200].rstrip() + "…"
            name = str(data.get("filename") or "Documento").replace("**", "")
            items.append(("b", "**{}**{}".format(name, " — “{}”".format(quote) if quote else "")))
    deck.flow("Análise de IA", items, subtitle=subtitle, size=14)


def build_pptx(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    analysis: Optional[Dict] = None,
    quality: Optional[Dict] = None,
    generated_at: Optional[str] = None,
) -> Tuple[bytes, str]:
    """Apresentação da Análise Geral no recorte da página; devolve os bytes e o nome."""

    generated_at = generated_at or datetime.now().strftime("%d/%m/%Y %H:%M")
    focus = (model.get("meta") or {}).get("focus_brand") or ""
    deck = _Deck("{} · Jornada de Compra · {}".format(project.get("name") or "Projeto", generated_at))
    _cover(deck, project, model, metrics, generated_at)
    _key_numbers(deck, project, model, metrics)
    _findings(deck, metrics)
    _gondola(deck, metrics, focus)
    _navigation(deck, model, metrics)
    _packaging(deck, metrics, focus)
    _channel(deck, metrics)
    _sample(deck, model, metrics, quality)
    if analysis and analysis.get("analysis_text"):
        _analysis(deck, analysis, project.get("data_version"))
    deck.flow("Limitações", [("b", note) for note in metrics.get("limitations") or ["Sem limitações registradas."]],
              size=15)
    return deck.save(), export_filename(project, "pptx", "jornada_apresentacao")
