"""
Gráficos do Teste Sensorial (Plotly, tema "nenc").

Cores por condição, fixas: o basal em cinza escuro (é a referência), o
controle em cinza azulado e cada amostra num tom da sequência validada de
`utils/chart_kit.py`, na ordem do desenho. Toda figura tem a tabela
equivalente na página que a mostra.
"""

from typing import Dict, List, Optional, Sequence

import pandas as pd
import plotly.graph_objects as go

from utils.chart_kit import BRAND_SEQUENCE, DEEMPHASIS, INK, MUTED, OTHER_COLOR, bar_height, empty_figure, layout

DIFFERENCE_COLOR = "#3987e5"
TREND_COLOR = "#c98500"
NEUTRAL_COLOR = "#75798c"
RESULT_COLORS = {"diferença": DIFFERENCE_COLOR, "tendência": TREND_COLOR, "sem diferença": NEUTRAL_COLOR,
                 "descritivo": DEEMPHASIS}
BAND_COLORS = {"Muito alta": "#199e70", "Alta": "#3987e5", "Baixa": "#c98500", "Muito baixa": "#e66767"}


def condition_colors(design: Dict) -> Dict[str, str]:
    """Cor de cada condição: basal e controle em cinza, amostras na sequência da marca."""
    colors: Dict[str, str] = {}
    samples = 0
    for condition in design.get("condicoes") or []:
        if condition["papel"] == "basal":
            colors[condition["codigo"]] = DEEMPHASIS
        elif condition["papel"] == "controle":
            colors[condition["codigo"]] = OTHER_COLOR
        else:
            colors[condition["codigo"]] = BRAND_SEQUENCE[samples % len(BRAND_SEQUENCE)]
            samples += 1
    return colors


def _rgba(hex_color: str, alpha: float) -> str:
    raw = hex_color.lstrip("#")
    return "rgba({}, {}, {}, {})".format(*(int(raw[i:i + 2], 16) for i in (0, 2, 4)), alpha)


def condition_stage_bars(summary: pd.DataFrame, measure: str, design: Dict, name: str) -> go.Figure:
    """Média das médias por participante, por etapa e condição, com o erro padrão e a linha do basal."""
    data = summary[summary["medida"] == measure]
    if data.empty:
        return empty_figure("Sem dados desta medida no recorte.")
    labels = {c["codigo"]: c["rotulo"] for c in design.get("condicoes") or []}
    stage_labels = {s["codigo"]: s["rotulo"] for s in design.get("etapas") or []}
    stages = [s["codigo"] for s in design.get("etapas") or [] if s["codigo"] in set(data["etapa"])]
    colors = condition_colors(design)
    reference = design.get("referencia") or {}
    figure = go.Figure()
    for condition in [c["codigo"] for c in design.get("condicoes") or []]:
        rows = data[data["condicao"] == condition].set_index("etapa").reindex(stages)
        if rows["media"].isna().all():
            continue
        figure.add_bar(
            name=labels.get(condition, condition), x=[stage_labels.get(s, s) for s in stages], y=rows["media"],
            marker=dict(color=colors.get(condition, OTHER_COLOR), line=dict(width=2, color="#1c1e2c")),
            error_y=dict(type="data", array=rows["ep"].fillna(0), color=MUTED, thickness=1.2, width=4),
            customdata=rows[["n"]].fillna(0).astype(int),
            hovertemplate="%{x} · " + labels.get(condition, condition) + "<br>média %{y:.4g} (n = %{customdata[0]})"
                          "<extra></extra>")
    basal = data[(data["condicao"] == reference.get("condicao")) & (data["etapa"] == reference.get("etapa"))]
    if not basal.empty:
        figure.add_hline(y=float(basal["media"].iloc[0]), line=dict(color=MUTED, dash="dot", width=1.5),
                         annotation_text="basal", annotation_font_color=MUTED)
    return layout(figure, 380, barmode="group", yaxis_title=name, bargap=0.25, bargroupgap=0.08)


def comparison_chart(table: pd.DataFrame, labels: Dict[str, str], stage_labels: Dict[str, str]) -> go.Figure:
    """Diferença mediana de cada comparação pareada, colorida pelo resultado (Holm)."""
    if table.empty:
        return empty_figure("Sem comparação para esta medida.")
    rows = table.copy()
    rows["rotulo"] = ["{} × {} · {}".format(labels.get(a, a), labels.get(b, b), stage_labels.get(e, e))
                      for a, b, e in zip(rows["condicao_a"], rows["condicao_b"], rows["etapa"])]
    figure = go.Figure()
    for result, color in RESULT_COLORS.items():
        part = rows[rows["resultado"] == result]
        if part.empty:
            continue
        figure.add_scatter(
            name=result, x=part["diferenca_mediana"], y=part["rotulo"], mode="markers",
            marker=dict(size=11, color=color, line=dict(width=2, color="#1c1e2c")),
            customdata=part[["n", "r", "p_holm"]],
            hovertemplate="%{y}<br>diferença mediana %{x:.4g}<br>n = %{customdata[0]} · r = %{customdata[1]:.2f}"
                          " · p Holm = %{customdata[2]:.3g}<extra></extra>")
    figure.add_vline(x=0, line=dict(color=MUTED, width=1))
    return layout(figure, bar_height(len(rows), 30, 120), xaxis_title="diferença mediana (pareada)",
                  yaxis=dict(autorange="reversed"))


def curve_chart(curve: pd.DataFrame, reference: Optional[Dict], design: Dict, name: str, alignment: str) -> go.Figure:
    """Curva média ± erro padrão por condição, com a faixa do basal."""
    if curve.empty:
        return empty_figure("Sem janelas desta medida no recorte.")
    labels = {c["codigo"]: c["rotulo"] for c in design.get("condicoes") or []}
    colors = condition_colors(design)
    figure = go.Figure()
    for (condition, stage), rows in curve.groupby(["condicao", "etapa"], sort=False):
        rows = rows.sort_values("t")
        color = colors.get(condition, OTHER_COLOR)
        label = labels.get(condition, condition)
        upper = rows["media"] + rows["ep"].fillna(0)
        lower = rows["media"] - rows["ep"].fillna(0)
        group = "{}|{}".format(condition, stage)
        figure.add_scatter(x=pd.concat([rows["t"], rows["t"][::-1]]), y=pd.concat([upper, lower[::-1]]),
                           fill="toself", fillcolor=_rgba(color, 0.18), line=dict(width=0), hoverinfo="skip",
                           showlegend=False, legendgroup=group)
        figure.add_scatter(x=rows["t"], y=rows["media"], mode="lines", line=dict(color=color, width=2),
                           name=label if alignment != "etapa" else "{} · {}".format(label, stage),
                           legendgroup=group, customdata=rows[["n"]],
                           hovertemplate=label + " · %{x:.2f} s<br>média %{y:.4g} (n = %{customdata[0]})"
                                                 "<extra></extra>")
    if reference and reference.get("n"):
        mean, error = reference["media"], reference.get("ep") or 0
        figure.add_hrect(y0=mean - error, y1=mean + error, fillcolor=_rgba("#9397ab", 0.12), line_width=0)
        figure.add_hline(y=mean, line=dict(color=MUTED, dash="dot", width=1.5), annotation_text="basal",
                         annotation_font_color=MUTED)
    if alignment == "olfacao":
        figure.add_vline(x=0, line=dict(color=INK, width=1, dash="dash"), annotation_text="1ª cheirada",
                         annotation_font_color=INK)
    title = "segundos desde a 1ª cheirada" if alignment == "olfacao" else "segundos desde o início da etapa"
    return layout(figure, 420, xaxis_title=title, yaxis_title=name)


def timeline_chart(frame: pd.DataFrame, measures: Sequence[str], names: Dict[str, str],
                   stage_labels: Dict[str, str]) -> go.Figure:
    """Linha do tempo de uma sessão: cada medida janela a janela, com as etapas marcadas."""
    if frame.empty:
        return empty_figure("Sem janelas nesta sessão.")
    figure = go.Figure()
    for index, measure in enumerate(measures):
        color = BRAND_SEQUENCE[index % len(BRAND_SEQUENCE)]
        kept = frame[frame["incluida"]]
        dropped = frame[~frame["incluida"]]
        figure.add_scatter(x=kept["t"], y=kept[measure], mode="lines", line=dict(color=color, width=1.6),
                           name=names.get(measure, measure), connectgaps=False,
                           hovertemplate=names.get(measure, measure) + " · %{x:.2f} s<br>%{y:.4g}<extra></extra>")
        if not dropped.empty:
            figure.add_scatter(x=dropped["t"], y=dropped[measure], mode="markers", showlegend=False,
                               marker=dict(size=4, color=DEEMPHASIS), hoverinfo="skip")
    for stage, rows in frame.groupby("Etapa", sort=False):
        start = float(rows["t"].min())
        figure.add_vline(x=start, line=dict(color=MUTED, width=1, dash="dot"),
                         annotation_text=stage_labels.get(stage, stage), annotation_font_color=MUTED,
                         annotation_position="top right")
    return layout(figure, 420, xaxis_title="segundos na sessão (etapas da análise em sequência)")


def score_bars(table: pd.DataFrame, design: Dict, bands: Dict[str, float]) -> go.Figure:
    """Score por claim, agrupado por condição, com os cortes das faixas."""
    if table.empty:
        return empty_figure("Sem teste de associação no recorte.")
    labels = {c["codigo"]: c["rotulo"] for c in design.get("condicoes") or []}
    colors = condition_colors(design)
    words = sorted(table["palavra"].unique(), key=lambda w: -table.loc[table["palavra"] == w, "score"].max())
    figure = go.Figure()
    for condition, rows in table.groupby("condicao", sort=False):
        rows = rows.set_index("palavra").reindex(words)
        figure.add_bar(name=labels.get(condition, condition), y=words, x=rows["score"], orientation="h",
                       marker=dict(color=colors.get(condition, BRAND_SEQUENCE[0]),
                                   line=dict(width=2, color="#1c1e2c")),
                       customdata=rows[["pct_sim", "cr_sim", "faixa"]],
                       hovertemplate="%{y} · " + labels.get(condition, condition) + "<br>Score %{x:.3f} · "
                                     "%{customdata[0]:.0%} Sim · CR %{customdata[1]:.2f}<br>%{customdata[2]}"
                                     "<extra></extra>")
    for name, value in (("muito alta", bands["muito_alta"]), ("alta", bands["alta"]), ("baixa", bands["baixa"])):
        figure.add_vline(x=value, line=dict(color=MUTED, width=1, dash="dot"), annotation_text=name,
                         annotation_font_color=MUTED)
    return layout(figure, bar_height(len(words) * max(1, table["condicao"].nunique()), 18, 140), barmode="group",
                  xaxis_title="Score (% de Sim × CR médio do Sim)", yaxis=dict(autorange="reversed"))


def quadrant_chart(table: pd.DataFrame, design: Dict, cuts: Dict[str, float]) -> go.Figure:
    """Adesão explícita (% de Sim) × convicção (CR do Sim): os quatro quadrantes."""
    if table.empty:
        return empty_figure("Sem teste de associação no recorte.")
    labels = {c["codigo"]: c["rotulo"] for c in design.get("condicoes") or []}
    colors = condition_colors(design)
    figure = go.Figure()
    for condition, rows in table.groupby("condicao", sort=False):
        figure.add_scatter(
            name=labels.get(condition, condition), x=rows["pct_sim"], y=rows["cr_sim"], mode="markers+text",
            text=rows["palavra"], textposition="top center", textfont=dict(size=10, color=MUTED),
            marker=dict(size=10, color=colors.get(condition, BRAND_SEQUENCE[0]), line=dict(width=2, color="#1c1e2c")),
            customdata=rows[["score", "quadrante"]],
            hovertemplate="%{text}<br>%{x:.0%} Sim · CR %{y:.2f}<br>Score %{customdata[0]:.3f} · %{customdata[1]}"
                          "<extra></extra>")
    figure.add_vline(x=cuts["pct_sim"], line=dict(color=MUTED, width=1, dash="dot"))
    figure.add_hline(y=cuts["cr"], line=dict(color=MUTED, width=1, dash="dot"))
    for x, y, text in ((0.98, 0.98, "Dominante"), (0.02, 0.98, "Nicho"), (0.98, 0.02, "Potencial"),
                       (0.02, 0.02, "Sem aderência")):
        figure.add_annotation(text=text, xref="paper", yref="paper", x=x, y=y, showarrow=False,
                              xanchor="right" if x > 0.5 else "left", yanchor="top" if y > 0.5 else "bottom",
                              font=dict(color=MUTED, size=11))
    return layout(figure, 460, xaxis=dict(title="% de respostas Sim", tickformat=".0%", range=[-0.05, 1.05]),
                  yaxis_title="CR médio das respostas Sim")


def results_matrix(comparisons: pd.DataFrame, names: Dict[str, str], labels: Dict[str, str],
                   stage_labels: Dict[str, str], measures: List[str]) -> pd.DataFrame:
    """Uma linha por medida, uma coluna por comparação × etapa: ▲/▼ diferença, △/▽ tendência."""
    if comparisons.empty:
        return pd.DataFrame()
    rows = comparisons[comparisons["medida"].isin(measures)].copy()
    rows["coluna"] = ["{} × {} · {}".format(labels.get(a, a), labels.get(b, b), stage_labels.get(e, e))
                      for a, b, e in zip(rows["condicao_a"], rows["condicao_b"], rows["etapa"])]

    def mark(row) -> str:
        up = row.diferenca_mediana > 0
        if row.resultado == "diferença":
            return "▲" if up else "▼"
        if row.resultado == "tendência":
            return "△" if up else "▽"
        return "·" if row.resultado == "sem diferença" else ""

    rows["marca"] = [mark(row) for row in rows.itertuples()]
    matrix = rows.pivot_table(index="medida", columns="coluna", values="marca", aggfunc="first", sort=False)
    matrix = matrix.reindex([m for m in measures if m in matrix.index])
    matrix.index = [names.get(m, m) for m in matrix.index]
    return matrix.fillna("").reset_index().rename(columns={"index": "medida"})
