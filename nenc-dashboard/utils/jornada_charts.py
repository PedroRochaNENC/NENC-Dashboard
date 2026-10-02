"""
Gráficos da Jornada de Compra (Plotly, tema "nenc").

As cores seguem o método de visualização adotado no projeto, validadas contra
a superfície escura dos gráficos (#1c1e2c) — não escolhidas no olho:

- marcas (identidade): 8 tons em ordem FIXA, a marca foco sempre no violeta.
  Pior par vizinho sob daltonismo ΔE 8,6 (meta ≥ 8), visão normal 19,3
  (piso 15), todos ≥ 3:1 de contraste. A cor segue a marca, nunca a posição:
  filtrar não repinta ninguém.
- destaque: quando a história é uma marca só, ela em violeta e o resto em cinza.
- ordinal (etapas do funil): uma rampa de violeta, clara → escura.
- sequencial (heatmaps): violeta do fundo ao claro, mais atenção = mais claro.
- divergente (índice de presença): azul acima de 1, vermelho abaixo.

Marcas finas (barras ≤ 24 px, ponta arredondada), vão de 2 px na cor da
superfície entre segmentos, legenda sempre que há duas séries ou mais e
rótulos só onde cabem. Todo gráfico tem tabela equivalente na página.
"""

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
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
FUNNEL_COLORS = {"notou": "#d2cefd", "examinou": "#9085e9", "retornou": "#5d5294"}
SEQUENTIAL = [[0, "#1c1e2c"], [0.5, "#5d5294"], [1, "#d2cefd"]]
ABOVE_COLOR = "#3987e5"
BELOW_COLOR = "#e66767"
BAR_PX = 24


def brand_color_map(brands: Sequence[str], focus_brand: str = "") -> Dict[str, str]:
    """Cor fixa por marca: a foco no slot 1, as demais na ordem do projeto.

    Passando do oitavo slot, as marcas restantes vão para o cinza de "outras":
    gerar um nono tom colidiria com um dos oito sob daltonismo.
    """

    ordered = list(dict.fromkeys(b for b in brands if b))
    focus = next((b for b in ordered if fold(b) == fold(focus_brand)), None) if focus_brand else None
    if focus:
        ordered.remove(focus)
        ordered.insert(0, focus)
    colors = {}
    for index, brand in enumerate(ordered):
        colors[brand] = BRAND_SEQUENCE[index] if index < len(BRAND_SEQUENCE) else OTHER_COLOR
    return colors


def _empty(message: str) -> go.Figure:
    figure = go.Figure()
    figure.add_annotation(text=message, showarrow=False, xref="paper", yref="paper",
                          x=0.5, y=0.5, font=dict(color=MUTED))
    figure.update_layout(template="nenc", height=160, xaxis=dict(visible=False),
                         yaxis=dict(visible=False))
    return figure


def _layout(figure: go.Figure, height: int, **kwargs) -> go.Figure:
    figure.update_layout(
        template="nenc",
        height=height,
        barcornerradius=4,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=INK)),
        hoverlabel=dict(bgcolor="#252838", font=dict(color=INK)),
        **kwargs,
    )
    return figure


def _pct(value: float) -> str:
    return "" if value != value else "{:.0f}%".format(100 * value)


def _label_color(hex_color: str) -> str:
    """Texto sobre um preenchimento: branco ou tinta escura, pelo contraste."""

    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#0b0b0b" if luminance > 0.35 else "#ffffff"


def _bar_height(rows: int, per_row: int = 44, extra: int = 110) -> int:
    return max(180, rows * per_row + extra)


# ---------------------------------------------------------------------------
# Gondola
# ---------------------------------------------------------------------------

def share_stacked(brand_df: pd.DataFrame, colors: Dict[str, str], value: str = "share_mean") -> go.Figure:
    """Share visual por marca, 100% por célula (parte-do-todo)."""

    data = brand_df.dropna(subset=[value]) if not brand_df.empty else brand_df
    if data.empty:
        return _empty("Sem gravações incluídas nesta seleção.")
    cells = list(dict.fromkeys(data["cell"]))
    figure = go.Figure()
    for brand in [b for b in colors if b in set(data["brand"])]:
        rows = data[data["brand"] == brand].set_index("cell").reindex(cells)
        fill = colors[brand]
        texts = [_pct(v) if v == v and v >= 0.12 else "" for v in rows[value]]
        figure.add_trace(go.Bar(
            y=cells, x=rows[value], name=brand, orientation="h",
            marker=dict(color=fill, line=dict(color=SURFACE, width=2)),
            width=0.5,
            text=texts, textposition="inside", insidetextanchor="middle",
            textfont=dict(color=_label_color(fill), size=11),
            customdata=np.stack([rows["n"].fillna(0), rows["n_pos"].fillna(0)], axis=-1),
            hovertemplate="<b>%{fullData.name}</b> · %{y}<br>%{x:.0%} da atenção"
                          "<br>n=%{customdata[0]:.0f} (com olhar: %{customdata[1]:.0f})<extra></extra>",
        ))
    _layout(figure, _bar_height(len(cells)), barmode="stack",
            xaxis=dict(tickformat=".0%", range=[0, 1], title=None),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=220, r=24, t=60, b=40))
    # A legenda segue a ordem das marcas (a foco primeiro), como os segmentos.
    figure.update_layout(legend=dict(traceorder="normal"))
    return figure


def store_brand_heatmap(brand_df: pd.DataFrame, value: str = "share_mean") -> go.Figure:
    data = brand_df.dropna(subset=[value]) if not brand_df.empty else brand_df
    if data.empty:
        return _empty("Sem dados para o mapa.")
    pivot = data.pivot_table(index="cell", columns="brand", values=value, aggfunc="first")
    pivot = pivot.reindex(list(dict.fromkeys(data["cell"])))
    text = [[_pct(v) for v in row] for row in pivot.to_numpy()]
    figure = go.Figure(go.Heatmap(
        z=pivot.to_numpy(), x=list(pivot.columns), y=list(pivot.index), colorscale=SEQUENTIAL,
        zmin=0, zmax=max(0.6, float(np.nanmax(pivot.to_numpy()))), text=text,
        texttemplate="%{text}", textfont=dict(size=12), xgap=2, ygap=2,
        colorbar=dict(tickformat=".0%", title=None, outlinewidth=0),
        hovertemplate="%{y}<br>%{x}: %{z:.0%}<extra></extra>",
    ))
    _layout(figure, _bar_height(len(pivot), 48, 90), yaxis=dict(autorange="reversed"),
            margin=dict(l=220, r=24, t=30, b=40))
    return figure


def choice_heatmap(choice_df: pd.DataFrame, brands_order: Sequence[str] = ()) -> go.Figure:
    """Quem escolheu cada marca em cada grupo: cor = fração, texto = contagem ("4/6").

    Com poucos participantes por loja a contagem diz mais que a porcentagem.
    Quem escolheu duas marcas conta nas duas, por isso não há barra empilhada.
    """

    if choice_df.empty:
        return _empty("Sem escolhas registradas nesta seleção.")
    present = list(dict.fromkeys(choice_df["brand"]))
    brands = [b for b in brands_order if b in present] + [b for b in present if b not in brands_order]
    groups = list(dict.fromkeys(choice_df["group"]))
    shares = choice_df.pivot_table(index="group", columns="brand", values="share", aggfunc="first")
    counts = choice_df.pivot_table(index="group", columns="brand", values="chose_n", aggfunc="first")
    shares = shares.reindex(index=groups, columns=brands)
    counts = counts.reindex(index=groups, columns=brands)
    sizes = choice_df.groupby("group", sort=False)["n"].first().reindex(groups)
    text = [["{}/{}".format(int(count), int(size)) if count == count else "" for count in row]
            for row, size in zip(counts.to_numpy(), sizes)]
    figure = go.Figure(go.Heatmap(
        z=shares.to_numpy(), x=brands, y=groups, colorscale=SEQUENTIAL, zmin=0, zmax=1, text=text,
        texttemplate="%{text}", textfont=dict(size=13), xgap=2, ygap=2,
        colorbar=dict(tickformat=".0%", title=None, outlinewidth=0),
        hovertemplate="%{y}<br>%{x}: %{text} (%{z:.0%})<extra></extra>",
    ))
    _layout(figure, _bar_height(len(groups), 52, 100), yaxis=dict(autorange="reversed"),
            margin=dict(l=160, r=24, t=30, b=40))
    return figure


def funnel_bars(brand_df: pd.DataFrame, brands_order: Sequence[str]) -> go.Figure:
    """Notou → examinou → retornou, por marca, numa célula."""

    if brand_df.empty:
        return _empty("Sem dados para o funil.")
    rows = brand_df.set_index("brand").reindex([b for b in brands_order if b in set(brand_df["brand"])])
    figure = go.Figure()
    for stage, column, label in (("notou", "reach", "Notou"), ("examinou", "examined", "Examinou"),
                                 ("retornou", "revisit", "Retornou")):
        figure.add_trace(go.Bar(
            y=rows.index, x=rows[column], name=label, orientation="h",
            marker=dict(color=FUNNEL_COLORS[stage], line=dict(color=SURFACE, width=2)),
            text=[_pct(v) for v in rows[column]], textposition="outside",
            textfont=dict(color=INK, size=11), cliponaxis=False,
            hovertemplate="<b>%{y}</b> · " + label + ": %{x:.0%}<extra></extra>",
        ))
    _layout(figure, _bar_height(len(rows), 78), barmode="group", bargap=0.35, bargroupgap=0.08,
            xaxis=dict(tickformat=".0%", range=[0, 1.12], title=None),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=140, r=40, t=60, b=40))
    return figure


def emphasis_bars(
    frame: pd.DataFrame,
    label_col: str,
    value_col: str,
    *,
    focus_col: str = "is_focus",
    value_format: str = "pct",
    title_x: Optional[str] = None,
    max_rows: int = 20,
) -> go.Figure:
    """Barras horizontais de uma série: a marca foco em violeta, o resto em cinza."""

    data = frame.dropna(subset=[value_col]) if not frame.empty else frame
    if data.empty:
        return _empty("Sem dados.")
    data = data.sort_values(value_col, ascending=False).head(max_rows)
    colors = [FOCUS_COLOR if bool(flag) else OTHER_COLOR for flag in data[focus_col]]
    if value_format == "pct":
        texts = [_pct(v) for v in data[value_col]]
        axis = dict(tickformat=".0%", title=title_x)
        hover = "%{x:.0%}"
    else:
        texts = ["{:.1f}".format(v).replace(".", ",") for v in data[value_col]]
        axis = dict(title=title_x)
        hover = "%{x:.2f}"
    figure = go.Figure(go.Bar(
        y=data[label_col], x=data[value_col], orientation="h", width=0.55,
        marker=dict(color=colors), text=texts, textposition="outside",
        textfont=dict(color=INK, size=11), cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>" + hover + "<extra></extra>",
    ))
    _layout(figure, _bar_height(len(data), 36), showlegend=False, xaxis=axis,
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=260, r=60, t=20, b=40))
    return figure


def ttff_strip(per_recording: pd.DataFrame, brands_order: Sequence[str], focus_brand: str,
               relative: bool = True) -> go.Figure:
    """Tempo até a primeira olhada, um ponto por participante; tique na mediana."""

    column = "rel_ttff_s" if relative else "ttff_s"
    data = per_recording[per_recording["looked"] & per_recording[column].notna()] if not per_recording.empty else per_recording
    if data.empty:
        return _empty("Nenhuma primeira olhada com tempo em segundos nesta seleção.")
    order = [b for b in brands_order if b in set(data["brand"])]
    figure = go.Figure()
    for brand in order:
        rows = data[data["brand"] == brand]
        focus = fold(brand) == fold(focus_brand)
        color = FOCUS_COLOR if focus else OTHER_COLOR
        figure.add_trace(go.Scatter(
            x=rows[column], y=[brand] * len(rows), mode="markers", name=brand, showlegend=False,
            marker=dict(size=11, color=color, line=dict(color=SURFACE, width=2)),
            customdata=rows[["participant"]], hovertemplate="<b>%{y}</b> · %{customdata[0]}<br>%{x:.1f} s<extra></extra>",
        ))
        median = rows[column].median()
        figure.add_trace(go.Scatter(
            x=[median, median], y=[brand, brand], mode="markers", showlegend=False,
            marker=dict(symbol="line-ns-open", size=26, color=INK, line=dict(width=2)),
            hovertemplate="<b>%{y}</b><br>mediana %{x:.1f} s<extra></extra>",
        ))
    title = "segundos depois da primeira marca vista" if relative else "segundos desde o início da gravação"
    _layout(figure, _bar_height(len(order), 52), xaxis=dict(title=title, rangemode="tozero"),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=140, r=24, t=20, b=50))
    return figure


def presence_index_chart(brand_df: pd.DataFrame, brands_order: Sequence[str]) -> go.Figure:
    """Share ÷ presença na gôndola: barras divergentes em torno de 1."""

    data = brand_df.dropna(subset=["presence_index"]) if not brand_df.empty else brand_df
    if data.empty:
        return _empty("Sem presença calculável nesta célula.")
    rows = data.set_index("brand").reindex([b for b in brands_order if b in set(data["brand"])])
    deltas = rows["presence_index"] - 1
    colors = [ABOVE_COLOR if d >= 0 else BELOW_COLOR for d in deltas]
    figure = go.Figure(go.Bar(
        y=rows.index, x=deltas, base=1, orientation="h", width=0.55, marker=dict(color=colors),
        text=["{:.1f}×".format(v).replace(".", ",") for v in rows["presence_index"]],
        textposition="outside", textfont=dict(color=INK, size=11), cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>%{text} a atenção esperada pela presença<extra></extra>",
    ))
    figure.add_vline(x=1, line=dict(color=MUTED, width=1))
    span = float(max(1.0, np.nanmax(np.abs(deltas.to_numpy())) + 0.4))
    _layout(figure, _bar_height(len(rows), 40), showlegend=False,
            xaxis=dict(range=[max(0, 1 - span), 1 + span], title="1 = atenção proporcional à presença"),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=140, r=60, t=20, b=50))
    return figure


# ---------------------------------------------------------------------------
# Navegacao, decisao e embalagem
# ---------------------------------------------------------------------------

def attribute_stacked(attr_df: pd.DataFrame, dimension: str, values_order: Sequence[str]) -> go.Figure:
    data = attr_df[attr_df["dimension"] == dimension] if not attr_df.empty else attr_df
    if data.empty:
        return _empty("Sem o atributo {} nas AOIs desta seleção.".format(dimension))
    cells = list(dict.fromkeys(data["cell"]))
    values = [v for v in values_order if v in set(data["value"])] + sorted(set(data["value"]) - set(values_order))
    figure = go.Figure()
    for index, value in enumerate(values):
        rows = data[data["value"] == value].set_index("cell").reindex(cells)
        fill = BRAND_SEQUENCE[index] if index < len(BRAND_SEQUENCE) else OTHER_COLOR
        figure.add_trace(go.Bar(
            y=cells, x=rows["share_mean"], name=value, orientation="h", width=0.5,
            marker=dict(color=fill, line=dict(color=SURFACE, width=2)),
            text=[_pct(v) if v == v and v >= 0.12 else "" for v in rows["share_mean"]],
            textposition="inside", insidetextanchor="middle",
            textfont=dict(color=_label_color(fill), size=11),
            hovertemplate="<b>" + value + "</b> · %{y}<br>%{x:.0%} da atenção<extra></extra>",
        ))
    _layout(figure, _bar_height(len(cells)), barmode="stack",
            xaxis=dict(tickformat=".0%", range=[0, 1], title=None),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=220, r=24, t=60, b=40))
    figure.update_layout(legend=dict(traceorder="normal"))
    return figure


def decision_strip(summary: pd.DataFrame, group_col: str) -> go.Figure:
    data = summary.dropna(subset=["tempo_decisao_s"]).drop_duplicates("participant") if not summary.empty else summary
    data = data[data[group_col].fillna("") != ""] if not data.empty else data
    if data.empty:
        return _empty("Sem tempo até a decisão informado.")
    order = sorted(data[group_col].unique())
    figure = go.Figure()
    for group in order:
        rows = data[data[group_col] == group]
        figure.add_trace(go.Scatter(
            x=rows["tempo_decisao_s"], y=[group] * len(rows), mode="markers", showlegend=False,
            marker=dict(size=11, color=FOCUS_COLOR, line=dict(color=SURFACE, width=2)),
            customdata=rows[["participant"]],
            hovertemplate="<b>%{y}</b> · %{customdata[0]}<br>%{x:.0f} s<extra></extra>",
        ))
        median = rows["tempo_decisao_s"].median()
        figure.add_trace(go.Scatter(
            x=[median], y=[group], mode="markers", showlegend=False,
            marker=dict(symbol="line-ns-open", size=26, color=INK, line=dict(width=2)),
            hovertemplate="<b>%{y}</b><br>mediana %{x:.1f} s<extra></extra>",
        ))
    _layout(figure, _bar_height(len(order), 52), xaxis=dict(title="segundos até a decisão", rangemode="tozero"),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=160, r=24, t=20, b=50))
    return figure


def packaging_heatmap(elements: pd.DataFrame, value: str = "element_share") -> go.Figure:
    """Elemento × marca: quanto do olhar de cada embalagem cada elemento levou."""

    if elements.empty:
        return _empty("Sem elementos de embalagem.")
    pivot = elements.pivot_table(index="element_label", columns="brand", values=value, aggfunc="first")
    pivot = pivot.loc[pivot.mean(axis=1).sort_values(ascending=False).index]
    text = [[_pct(v) for v in row] for row in pivot.to_numpy()]
    figure = go.Figure(go.Heatmap(
        z=pivot.to_numpy(), x=list(pivot.columns), y=list(pivot.index), colorscale=SEQUENTIAL,
        zmin=0, text=text, texttemplate="%{text}", textfont=dict(size=12), xgap=2, ygap=2,
        colorbar=dict(tickformat=".0%", title=None, outlinewidth=0),
        hovertemplate="%{x} · %{y}<br>%{z:.0%}<extra></extra>",
    ))
    _layout(figure, _bar_height(len(pivot), 36, 80), yaxis=dict(autorange="reversed"),
            margin=dict(l=200, r=24, t=20, b=40))
    return figure


def group_dots(frame: pd.DataFrame, group_col: str, value_col: str, *, value_title: str,
               percent: bool = True) -> go.Figure:
    """Um ponto por participante em cada grupo, com a mediana marcada."""

    data = frame.dropna(subset=[value_col]) if not frame.empty else frame
    data = data[data[group_col].fillna("") != ""] if not data.empty else data
    if data.empty:
        return _empty("Sem dados para comparar.")
    order = sorted(data[group_col].unique())
    figure = go.Figure()
    for group in order:
        rows = data[data[group_col] == group]
        figure.add_trace(go.Scatter(
            x=rows[value_col], y=[group] * len(rows), mode="markers", showlegend=False,
            marker=dict(size=11, color=FOCUS_COLOR, line=dict(color=SURFACE, width=2)),
            customdata=rows[["participant"]],
            hovertemplate="<b>%{y}</b> · %{customdata[0]}<br>" + ("%{x:.0%}" if percent else "%{x:.1f}") + "<extra></extra>",
        ))
        figure.add_trace(go.Scatter(
            x=[rows[value_col].median()], y=[group], mode="markers", showlegend=False,
            marker=dict(symbol="line-ns-open", size=26, color=INK, line=dict(width=2)),
            hovertemplate="<b>%{y}</b><br>mediana " + ("%{x:.0%}" if percent else "%{x:.1f}") + "<extra></extra>",
        ))
    _layout(figure, _bar_height(len(order), 52),
            xaxis=dict(title=value_title, tickformat=".0%" if percent else None, rangemode="tozero"),
            yaxis=dict(autorange="reversed", title=None), margin=dict(l=180, r=24, t=20, b=50))
    return figure
