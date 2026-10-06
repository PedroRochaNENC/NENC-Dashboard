"""
Base dos gráficos Plotly dos módulos por projeto (tema "nenc", superfície escura).

As cores seguem o método de visualização adotado no projeto, validadas contra a
superfície dos gráficos (#1c1e2c), não escolhidas no olho:

- identidade (marca, amostra): 8 tons em ordem FIXA. Pior par vizinho sob
  daltonismo ΔE 8,6 (meta ≥ 8), visão normal 19,3 (piso 15), todos ≥ 3:1 de
  contraste. A cor segue a entidade, nunca a posição: filtrar não repinta ninguém;
- destaque: quando a história é uma entidade só, ela em violeta e o resto em cinza;
- sequencial (heatmaps): violeta do fundo ao claro;
- divergente: azul acima da referência, vermelho abaixo.

Marcas finas, legenda sempre que há duas séries ou mais, e todo gráfico tem a
tabela equivalente na página.
"""

from typing import Dict, Sequence

import plotly.graph_objects as go

# Importado pelo efeito de registrar o template "nenc".
from utils.charts import NENC_HEATMAP  # noqa: F401
from utils.jornada_taxonomy import fold

SURFACE = "#1c1e2c"
INK = "#e9e9ed"
MUTED = "#9397ab"
DEEMPHASIS = "#595d6c"

# Ordem validada (dark, superficie #1c1e2c): violeta, laranja, agua, azul,
# amarelo, magenta, verde, vermelho.
BRAND_SEQUENCE = [
    "#9085e9", "#d95926", "#199e70", "#3987e5", "#c98500", "#d55181", "#008300", "#e66767",
]
FOCUS_COLOR = BRAND_SEQUENCE[0]
OTHER_COLOR = "#75798c"
SEQUENTIAL = [[0, "#1c1e2c"], [0.5, "#5d5294"], [1, "#d2cefd"]]
ABOVE_COLOR = "#3987e5"
BELOW_COLOR = "#e66767"
BAR_PX = 24


def color_map(names: Sequence[str], focus: str = "") -> Dict[str, str]:
    """Cor fixa por entidade: a foco no slot 1, as demais na ordem recebida.

    Passando do oitavo slot, as restantes vão para o cinza de "outras": gerar
    um nono tom colidiria com um dos oito sob daltonismo.
    """
    ordered = list(dict.fromkeys(name for name in names if name))
    chosen = next((name for name in ordered if fold(name) == fold(focus)), None) if focus else None
    if chosen:
        ordered.remove(chosen)
        ordered.insert(0, chosen)
    return {name: BRAND_SEQUENCE[index] if index < len(BRAND_SEQUENCE) else OTHER_COLOR
            for index, name in enumerate(ordered)}


def empty_figure(message: str) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(text=message, showarrow=False, xref="paper", yref="paper",
                          x=0.5, y=0.5, font=dict(color=MUTED))
    figure.update_layout(template="nenc", height=160, xaxis=dict(visible=False),
                         yaxis=dict(visible=False))
    return figure


def layout(figure: go.Figure, height: int, **kwargs) -> go.Figure:
    figure.update_layout(
        template="nenc",
        height=height,
        barcornerradius=4,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=INK)),
        hoverlabel=dict(bgcolor="#252838", font=dict(color=INK)),
        **kwargs,
    )
    return figure


def pct(value: float) -> str:
    return "" if value != value else "{:.0f}%".format(100 * value)


def label_color(hex_color: str) -> str:
    """Texto sobre um preenchimento: branco ou tinta escura, pelo contraste."""
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#0b0b0b" if luminance > 0.35 else "#ffffff"


def bar_height(rows: int, per_row: int = 44, extra: int = 110) -> int:
    return max(180, rows * per_row + extra)
