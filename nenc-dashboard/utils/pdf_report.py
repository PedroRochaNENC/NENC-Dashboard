"""
Peças de relatório em PDF (fpdf2 com as fontes padrão do PDF).

As fontes padrão cobrem o Windows-1252: acentos, travessão, aspas curvas,
reticências e marcador saem certos. O que fica fora dele (≥, δ, setas) é
trocado por um equivalente legível antes de codificar, em vez de virar "?".

Tudo é desenhado com primitivas (texto, linhas e retângulos): nada de imagem
de gráfico, então o arquivo é leve e o texto continua selecionável.
"""

import math
import re
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from fpdf import FPDF

ENCODING = "windows-1252"
FONT = "Helvetica"

# Tinta e linhas para papel branco (paleta de referência, modo claro).
INK = (11, 11, 11)
SECONDARY = (82, 81, 78)
MUTED = (137, 135, 129)
RULE = (225, 224, 217)
ACCENT = (93, 82, 148)  # violeta NENC (accent-700): marca foco e destaques
BASE_BAR = (163, 166, 179)  # cinza: as demais barras

PAGE_WIDTH = 210.0
MARGIN = 15.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

_REPLACEMENTS = {
    "≥": ">=",
    "≤": "<=",
    "≠": "!=",
    "δ": "delta",
    "→": "->",
    "←": "<-",
    "↑": "^",
    "↓": "v",
    "−": "-",
    "✕": "x",
    "✓": "v",
    " ": " ",
    " ": " ",
    "​": "",
    "﻿": "",
}


def sanitize(text) -> str:
    """Texto que as fontes padrão do PDF desenham, sem perder o sentido."""
    if text is None:
        return ""
    if isinstance(text, float) and math.isnan(text):
        return ""
    value = str(text)
    for source, target in _REPLACEMENTS.items():
        value = value.replace(source, target)
    return value.encode(ENCODING, errors="replace").decode(ENCODING)


class ReportPDF(FPDF):
    """A4 retrato com rodapé discreto: título do relatório e página."""

    def __init__(self, title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.core_fonts_encoding = ENCODING
        self.report_title = title
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=16)
        self.alias_nb_pages()
        self.set_title(sanitize(title))
        self.set_creator("NENC Insights")

    def footer(self):
        self.set_y(-11)
        self.set_font(FONT, "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, sanitize("{} · página {}/{{nb}}".format(self.report_title, self.page_no())),
                  align="R")
        self.set_text_color(*INK)


def output_bytes(pdf: FPDF) -> bytes:
    return bytes(pdf.output())


def ensure_space(pdf: FPDF, height: float) -> None:
    """Quebra a página antes de um bloco que não cabe inteiro no resto dela."""
    if pdf.get_y() + height > pdf.h - pdf.b_margin:
        pdf.add_page()


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------

# Espaço que um título reserva para o que vem logo abaixo dele: título sozinho
# no pé da página vai para a página seguinte junto com o conteúdo.
_KEEP_WITH_NEXT = {1: 45.0, 2: 30.0, 3: 20.0}


def heading(pdf: FPDF, text: str, level: int = 1) -> None:
    size = {1: 14, 2: 11.5, 3: 10}.get(level, 10)
    ensure_space(pdf, _KEEP_WITH_NEXT.get(level, 20.0))
    pdf.ln(4 if level == 1 else 2.5)
    pdf.set_font(FONT, "B", size)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, size * 0.5, sanitize(text), new_x="LMARGIN", new_y="NEXT")
    if level == 1:
        y = pdf.get_y() + 0.8
        pdf.set_draw_color(*ACCENT)
        pdf.set_line_width(0.5)
        pdf.line(pdf.l_margin, y, pdf.l_margin + 18, y)
        pdf.set_line_width(0.2)
        pdf.ln(3)
    else:
        pdf.ln(1)


def paragraph(pdf: FPDF, text: str, size: float = 9.5, color=SECONDARY, style: str = "") -> None:
    pdf.set_text_color(*color)
    height = size * 0.48
    if "**" in str(text):
        # Campo do projeto e trecho citado podem vir com markdown colado de
        # outro lugar. Sem tratar aqui, o leitor ve os asteriscos crus.
        _write_inline(pdf, text, size, height, base=style)
        pdf.ln(height)
    else:
        pdf.set_font(FONT, style, size)
        pdf.multi_cell(0, height, sanitize(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)


def _write_inline(pdf: FPDF, text: str, size: float, height: float, base: str = "") -> None:
    """Escreve uma linha com **negrito** inline, quebrando na margem.

    `base` e o estilo de fora do negrito — um trecho citado em italico segue
    italico entre os asteriscos.
    """
    forte = "B" if "B" in base else ""
    for index, part in enumerate(str(text).split("**")):
        if not part:
            continue
        estilo = base if index % 2 == 0 else base.replace(forte, "") + "B"
        pdf.set_font(FONT, estilo, size)
        pdf.write(height, sanitize(part))


def bullets(pdf: FPDF, items: Iterable[str], size: float = 9.5, marker: str = "•") -> None:
    height = size * 0.48
    left = pdf.l_margin
    pdf.set_text_color(*INK)
    for item in items:
        ensure_space(pdf, height * 2)
        pdf.set_x(left)
        pdf.set_font(FONT, "", size)
        pdf.cell(4.5, height, sanitize(marker))
        pdf.set_left_margin(left + 4.5)
        _write_inline(pdf, item, size, height)
        pdf.ln(height + 0.9)
        pdf.set_left_margin(left)
    pdf.set_x(left)


def render_markdown_lite(pdf: FPDF, text: str, size: float = 9.5) -> None:
    """Markdown do texto da IA: títulos, listas, **negrito** e tabelas simples."""
    height = size * 0.48
    table: List[List[str]] = []

    def flush_table():
        if not table:
            return
        header, body = table[0], table[1:]
        # Tabela escrita pela IA e prosa, nao numero: quebra em linhas e tem a
        # largura medida na fonte, em vez de partes iguais que cortariam texto.
        prose_table(
            pdf,
            header,
            [row + [""] * (len(header) - len(row)) for row in body],
        )
        table.clear()

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= set("-: ") for cell in cells):
                continue
            table.append([cell.replace("**", "") for cell in cells])
            continue
        flush_table()
        if not line:
            pdf.ln(height * 0.6)
            continue
        if line.startswith("#"):
            level = min(3, len(line) - len(line.lstrip("#")))
            heading(pdf, line.lstrip("#").strip().replace("**", ""), level=level + 1 if level < 3 else 3)
            continue
        if line in ("---", "***", "___"):
            y = pdf.get_y() + 1
            pdf.set_draw_color(*RULE)
            pdf.line(pdf.l_margin, y, pdf.l_margin + CONTENT_WIDTH, y)
            pdf.ln(3)
            continue
        if line[:2] in ("- ", "* ", "• "):
            bullets(pdf, [line[2:].strip()], size=size)
            continue
        number, dot, rest = line.partition(". ")
        if dot and number.isdigit() and len(number) <= 2:
            bullets(pdf, [rest.strip()], size=size, marker="{}.".format(number))
            continue
        ensure_space(pdf, height * 2)
        pdf.set_text_color(*INK)
        _write_inline(pdf, line, size, height)
        pdf.ln(height + 1)
    flush_table()


# ---------------------------------------------------------------------------
# Números
# ---------------------------------------------------------------------------

def kpi_row(pdf: FPDF, items: Sequence[Tuple[str, str]]) -> None:
    """Uma fileira de números com rótulo (até duas linhas), em caixas de contorno fino."""
    if not items:
        return
    gap = 3.0
    label_height = 3.4
    width = (CONTENT_WIDTH - gap * (len(items) - 1)) / len(items)
    pdf.set_font(FONT, "", 7.5)
    labels = [_wrap(pdf, label, width - 6, 2) for label, _ in items]
    label_lines = max(len(lines) for lines in labels)
    height = 12.5 + label_lines * label_height
    ensure_space(pdf, height + 4)
    top = pdf.get_y()
    pdf.set_draw_color(*RULE)
    for index, (lines, (_, value)) in enumerate(zip(labels, items)):
        x = pdf.l_margin + index * (width + gap)
        pdf.rect(x, top, width, height)
        pdf.set_font(FONT, "", 7.5)
        pdf.set_text_color(*MUTED)
        for number, line in enumerate(lines):
            pdf.set_xy(x + 3, top + 2.3 + number * label_height)
            pdf.cell(width - 6, label_height, line)
        pdf.set_xy(x + 3, top + 3.3 + label_lines * label_height)
        pdf.set_font(FONT, "B", 14)
        pdf.set_text_color(*INK)
        pdf.cell(width - 6, 7, _fit(pdf, value, width - 6))
    pdf.set_text_color(*INK)
    pdf.set_xy(pdf.l_margin, top + height + 4)


def _wrap(pdf: FPDF, text, width: float, max_lines: int) -> List[str]:
    """Linhas do texto na largura (fonte atual), quebrando só entre palavras;
    a última linha leva o resto, com reticências se não couber."""
    lines: List[str] = []
    current = ""
    for word in sanitize(text).split():
        candidate = "{} {}".format(current, word) if current else word
        if current and pdf.get_string_width(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return [_fit(pdf, line, width) for line in lines]


def _fit(pdf: FPDF, text, width: float) -> str:
    """Corta com reticências o texto que não cabe na largura (fonte atual)."""
    value = sanitize(text)
    if pdf.get_string_width(value) <= width:
        return value
    ellipsis = sanitize("…")
    while value and pdf.get_string_width(value + ellipsis) > width:
        value = value[:-1]
    return value.rstrip() + ellipsis


def hbar_chart(
    pdf: FPDF,
    rows: Sequence[Tuple[str, float, bool]],
    *,
    value_format: Callable[[float], str],
    max_value: Optional[float] = None,
    label_width: float = 52.0,
    bar_height: float = 4.2,
    gap: float = 2.0,
    title: str = "",
    note: str = "",
) -> None:
    """Barras horizontais: rótulo à esquerda, valor na ponta, destaque em violeta.

    `rows` = (rótulo, valor, destacar). Valores ausentes aparecem sem barra, com
    traço no lugar do número.
    """
    if not rows:
        return
    title_height = 6 if title else 0
    note_height = 5 if note else 0
    total = title_height + len(rows) * (bar_height + gap) + note_height + 3
    ensure_space(pdf, total)
    if title:
        pdf.set_font(FONT, "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(0, 5, sanitize(title), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
    values = [value for _, value, _ in rows if value == value and value is not None]
    scale_max = max_value if max_value else (max(values) if values else 1.0)
    scale_max = scale_max or 1.0
    value_room = 16.0
    bar_room = CONTENT_WIDTH - label_width - value_room - 2
    left = pdf.l_margin
    for label, value, highlight in rows:
        y = pdf.get_y()
        pdf.set_font(FONT, "B" if highlight else "", 8.5)
        pdf.set_text_color(*INK)
        pdf.set_xy(left, y - 0.4)
        pdf.cell(label_width - 2, bar_height + 0.8, _fit(pdf, label, label_width - 3), align="R")
        missing = value is None or value != value
        length = 0.0 if missing else max(0.0, min(1.0, float(value) / scale_max)) * bar_room
        if length > 0:
            pdf.set_fill_color(*(ACCENT if highlight else BASE_BAR))
            pdf.rect(left + label_width, y, length, bar_height, style="F")
        pdf.set_xy(left + label_width + length + 1.5, y - 0.4)
        pdf.set_font(FONT, "", 8)
        pdf.set_text_color(*SECONDARY)
        pdf.cell(value_room, bar_height + 0.8, sanitize("—" if missing else value_format(value)))
        pdf.set_xy(left, y + bar_height + gap)
    # Linha de base, onde as barras nascem.
    pdf.set_draw_color(*RULE)
    top = pdf.get_y() - len(rows) * (bar_height + gap)
    pdf.line(left + label_width, top - 0.5, left + label_width, pdf.get_y() - gap + 0.5)
    if note:
        pdf.set_font(FONT, "", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 4.5, sanitize(note), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.ln(2)


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------

def simple_table(
    pdf: FPDF,
    headers: Sequence[str],
    rows: Sequence[Sequence],
    widths: Sequence[float],
    *,
    align: Optional[Sequence[str]] = None,
    size: float = 8.0,
    row_height: float = 5.2,
) -> None:
    """Cabeçalho em negrito sobre linha fina, linhas separadas por fio; o
    cabeçalho quebra em até duas linhas e se repete quando a tabela atravessa a
    página. Colunas de números alinham à direita, as de texto à esquerda. Texto
    longo é cortado com reticências (a tabela completa está no Excel)."""
    if not rows:
        return
    align = list(align or [
        "R" if numeric_column([row[index] for row in rows]) else "L" for index in range(len(headers))
    ])
    line_height = row_height * 0.78

    def draw_header():
        pdf.set_font(FONT, "B", size)
        pdf.set_text_color(*SECONDARY)
        wrapped = [_wrap(pdf, header, width - 1.5, 2) for header, width in zip(headers, widths)]
        lines = max(len(item) for item in wrapped)
        ensure_space(pdf, lines * line_height + row_height * 2)
        top = pdf.get_y()
        x = pdf.l_margin
        for header_lines, width, how in zip(wrapped, widths, align):
            # Cabeçalho de uma linha fica rente à linha de baixo, como os de duas.
            offset = (lines - len(header_lines)) * line_height
            for number, text in enumerate(header_lines):
                pdf.set_xy(x, top + offset + number * line_height)
                pdf.cell(width, line_height, text, align=how)
            x += width
        pdf.set_xy(pdf.l_margin, top + lines * line_height + 1.2)
        y = pdf.get_y()
        pdf.set_draw_color(*SECONDARY)
        pdf.line(pdf.l_margin, y, pdf.l_margin + sum(widths), y)
        pdf.set_text_color(*INK)

    ensure_space(pdf, row_height * 3)
    draw_header()
    pdf.set_draw_color(*RULE)
    for row in rows:
        if pdf.get_y() + row_height > pdf.h - pdf.b_margin:
            pdf.add_page()
            draw_header()
        pdf.set_font(FONT, "", size)
        for value, width, how in zip(row, widths, align):
            pdf.cell(width, row_height, _fit(pdf, value, width - 1.5), align=how)
        pdf.ln(row_height)
        y = pdf.get_y()
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, y, pdf.l_margin + sum(widths), y)
    pdf.ln(3)


def measured_widths(
    pdf: FPDF,
    headers: Sequence[str],
    rows: Sequence[Sequence],
    total: float = CONTENT_WIDTH,
    *,
    size: float = 8.0,
    padding: float = 3.0,
) -> List[float]:
    """Larguras de coluna medidas na fonte real, não em partes iguais.

    O piso de cada coluna é a palavra mais larga que ela contém — sem isso um
    cabeçalho como "Frequência" quebra em "Frequênci/a". O que sobra é
    repartido pela extensão média do conteúdo, para a coluna de texto longo
    ficar larga e a de rótulo curto, estreita.
    """
    pisos, pesos = _pisos_e_pesos(pdf, headers, rows, size, padding)

    if sum(pisos) >= total:  # nem os pisos cabem: proporcional e segue
        fator = total / sum(pisos)
        return [piso * fator for piso in pisos]

    sobra = total - sum(pisos)
    peso_total = sum(pesos)
    return [piso + sobra * peso / peso_total for piso, peso in zip(pisos, pesos)]


def _pisos_e_pesos(pdf, headers, rows, size, padding):
    """Por coluna: a largura da palavra mais longa e o peso do conteúdo."""
    pisos: List[float] = []
    pesos: List[float] = []
    for indice in range(len(headers)):
        maior = 0.0
        soma = 0
        for numero, linha in enumerate([list(headers)] + [list(r) for r in rows]):
            if indice >= len(linha):
                continue
            texto = sanitize(linha[indice])
            pdf.set_font(FONT, "B" if numero == 0 else "", size)
            soma += len(texto)
            for palavra in texto.split():
                maior = max(maior, pdf.get_string_width(palavra))
        pisos.append(maior + padding)
        pesos.append(max(soma / (len(rows) + 1), 6) ** 0.72)
    return pisos, pesos


def fit_size(
    pdf: FPDF,
    headers: Sequence[str],
    rows: Sequence[Sequence],
    total: float = CONTENT_WIDTH,
    *,
    size: float = 8.0,
    minimo: float = 6.0,
    padding: float = 3.0,
) -> float:
    """O maior corpo em que nenhuma palavra precisa quebrar no meio.

    Tabela larga — as métricas acústicas têm oito colunas e IDs como
    `wa_+5521975310982_37` — não cabe no corpo padrão. Diminuir o texto
    preserva o identificador inteiro; insistir no corpo o parte ao meio.
    """
    tentativa = size
    while tentativa > minimo:
        pisos, _ = _pisos_e_pesos(pdf, headers, rows, tentativa, padding)
        if sum(pisos) <= total:
            return tentativa
        tentativa -= 0.5
    return minimo


def prose_table(
    pdf: FPDF,
    headers: Sequence[str],
    rows: Sequence[Sequence],
    widths: Optional[Sequence[float]] = None,
    *,
    size: float = 8.0,
    line_height: float = 4.2,
    padding: float = 1.4,
) -> None:
    """Tabela de texto corrido: a célula quebra em linhas em vez de ser cortada.

    `simple_table` corta com reticências porque serve a números, com o detalhe
    no Excel. As tabelas que a IA escreve — matriz de destaques, recomendações
    por driver — são prosa: cortar apaga o conteúdo.
    """
    if not rows:
        return
    if widths is None:
        # O piso reserva o recuo que a celula desenha MAIS a margem interna
        # que o multi_cell aplica dentro da largura recebida (c_margin de cada
        # lado). Sem esse segundo termo a palavra parece caber e quebra assim
        # mesmo — foi o que partia `wa_+5521975310982_37` ao meio.
        folga = 2 * padding + 2 * pdf.c_margin + 0.4
        # Encolhe o corpo antes de deixar uma palavra partir ao meio.
        size = fit_size(pdf, headers, rows, size=size, padding=folga)
        line_height = max(line_height * size / 8.0, 3.4)
        larguras = measured_widths(pdf, headers, rows, size=size, padding=folga)
    else:
        larguras = list(widths)

    def altura_da_linha(valores, estilo):
        pdf.set_font(FONT, estilo, size)
        alturas = []
        for valor, largura in zip(valores, larguras):
            alturas.append(
                pdf.multi_cell(
                    largura - 2 * padding, line_height, sanitize(valor),
                    dry_run=True, output="HEIGHT",
                )
            )
        return max(alturas + [line_height])

    def desenha(valores, estilo, cor, quebrar=True):
        altura = altura_da_linha(valores, estilo)
        if quebrar:
            ensure_space(pdf, altura + 1.5)
        topo = pdf.get_y()
        x = pdf.l_margin
        pdf.set_font(FONT, estilo, size)
        pdf.set_text_color(*cor)
        # A quebra automatica no meio de uma celula deslocaria as colunas
        # seguintes: a altura ja foi medida e a pagina, garantida acima.
        automatica = pdf.auto_page_break
        pdf.set_auto_page_break(False)
        for indice, largura in enumerate(larguras):
            valor = valores[indice] if indice < len(valores) else ""
            pdf.set_xy(x + padding, topo)
            pdf.multi_cell(largura - 2 * padding, line_height, sanitize(valor))
            x += largura
        pdf.set_auto_page_break(automatica, pdf.b_margin)
        pdf.set_xy(pdf.l_margin, topo + altura + 1)
        return pdf.get_y()

    def cabecalho():
        y = desenha(list(headers), "B", SECONDARY, quebrar=False)
        pdf.set_draw_color(*SECONDARY)
        pdf.line(pdf.l_margin, y - 0.6, pdf.l_margin + sum(larguras), y - 0.6)

    ensure_space(pdf, altura_da_linha(list(headers), "B") + line_height * 2)
    cabecalho()

    for linha in rows:
        altura = altura_da_linha(list(linha), "")
        if pdf.get_y() + altura + 1.5 > pdf.h - pdf.b_margin:
            pdf.add_page()
            cabecalho()  # tabela que atravessa a pagina repete o cabecalho
        y = desenha(list(linha), "", INK, quebrar=False)
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, y - 0.6, pdf.l_margin + sum(larguras), y - 0.6)
    pdf.ln(2.5)
    pdf.set_text_color(*INK)


_NUMBER = re.compile(r"^[-+]?[\d.,]+\s?(%|s|×|x)?$")


def numeric_column(values: Sequence) -> bool:
    """Coluna de números: todos os valores preenchidos parecem número (—, vazio não contam)."""
    filled = [sanitize(value).strip() for value in values]
    filled = [value for value in filled if value not in ("", "—", "-")]
    return bool(filled) and all(_NUMBER.match(value) for value in filled)
