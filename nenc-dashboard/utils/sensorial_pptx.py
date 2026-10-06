"""
Apresentação PPTX da Análise Geral do Teste Sensorial (python-pptx, 16:9).

Os mesmos números do PDF, com gráficos nativos e editáveis: cada índice por
condição e etapa, a curva média dos índices com achados (alinhada na primeira
cheirada), comparações, teste de associação, topomapas, a síntese por amostra
e claim, a análise de IA escolhida e as limitações. Só códigos de participante.
"""

from typing import Dict, List, Optional, Tuple

from pptx.enum.chart import XL_TICK_LABEL_POSITION

from utils import pptx_kit, sensorial_design, sensorial_metrics
from utils.pptx_kit import ACCENT, BODY_BOTTOM, BODY_TOP, CATEGORICAL, CONTENT_WIDTH, LEFT, Inches, rgb
from utils.sensorial_export import (
    association_rows,
    comparison_rows,
    export_filename,
    synthesis_rows,
)

BASAL_COLOR = rgb("#a3a6b3")
CONTROL_COLOR = rgb("#75798c")
SAMPLE_COLORS = (ACCENT,) + tuple(CATEGORICAL)
MAX_CURVES = 6
CURVE_BIN_S = 1.0
NUMBER_FORMAT = "0.00"


def _colors(design: Dict) -> Dict[str, object]:
    colors, samples = {}, 0
    for condition in design.get("condicoes") or []:
        if condition["papel"] == "basal":
            colors[condition["codigo"]] = BASAL_COLOR
        elif condition["papel"] == "controle":
            colors[condition["codigo"]] = CONTROL_COLOR
        else:
            colors[condition["codigo"]] = SAMPLE_COLORS[samples % len(SAMPLE_COLORS)]
            samples += 1
    return colors


def _box():
    return (LEFT, BODY_TOP, CONTENT_WIDTH, BODY_BOTTOM - BODY_TOP)


def _values(series) -> List[float]:
    return [v for _, column in series for v in column if v is not None and v == v]


def _number_format(series) -> str:
    """Índice em potência absoluta (~1e-12) vira 0,00 com duas casas: esse vai em notação científica."""
    values = [abs(v) for v in _values(series)]
    return "0.00E+00" if values and 0 < max(values) < 0.01 else NUMBER_FORMAT


def _allow_negative(chart, series) -> None:
    """Assimetria, PPI e Score têm valores negativos: o eixo deixa de começar no zero.

    Os rótulos das categorias vão para a borda do gráfico; no zero, cobririam as barras negativas.
    """
    values = _values(series)
    if values and min(values) < 0:
        chart.value_axis.minimum_scale = None
        chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW


def _index_slide(deck, model: Dict, summary, measure: str) -> None:
    design = model["design"]
    data = summary[summary["medida"] == measure]
    if data.empty:
        return
    stages = [s for s in design.get("etapas") or [] if s["codigo"] in set(data["etapa"])]
    colors = _colors(design)
    series, palette = [], []
    for condition in design.get("condicoes") or []:
        rows = data[data["condicao"] == condition["codigo"]].set_index("etapa")
        values = [rows["media"].get(stage["codigo"]) for stage in stages]
        if any(value == value and value is not None for value in values):
            series.append((condition["rotulo"], values))
            palette.append(colors[condition["codigo"]])
    if not series:
        return
    name = model["index_names"].get(measure, measure)
    slide = deck.slide(name, "Média das médias por participante, por etapa e condição (erro padrão e n no Excel)")
    chart = pptx_kit.clustered_chart(slide, _box(), [s["rotulo"] for s in stages], series, palette,
                                     number_format=_number_format(series))
    _allow_negative(chart, series)


def _curve_slide(deck, model: Dict, measure: str, filters: Dict) -> None:
    result = sensorial_metrics.curves(model, measure, "olfacao", filters, bin_s=CURVE_BIN_S)
    curve = result["curva"]
    if curve.empty:
        return
    labels = sensorial_design.condition_labels(model["design"])
    colors = _colors(model["design"])
    times = sorted(curve["t"].unique())
    series, palette = [], []
    for condition, rows in curve.groupby("condicao", sort=False):
        by_time = rows.set_index("t")["media"]
        series.append((labels.get(condition, condition), [by_time.get(t) for t in times]))
        palette.append(colors.get(condition, ACCENT))
    name = model["index_names"].get(measure, measure)
    slide = deck.slide("{} no tempo".format(name), "Curva média por condição, segundos desde a 1ª cheirada")
    pptx_kit.line_chart(slide, _box(), ["{:g}".format(t) for t in times], series, palette,
                        number_format=_number_format(series), label_every=max(1, len(times) // 12))


def build_pptx(project: Dict, model: Dict, metrics: Dict, *, recorte: str = "", analysis: Optional[Dict] = None,
               images: Optional[List[Dict]] = None) -> Tuple[bytes, str]:
    """Apresentação no recorte da página; `images` = [{"content", "title"}] dos topomapas."""
    deck = pptx_kit.Deck("Teste Sensorial · {} · {}".format(project.get("name") or "Projeto", recorte or
                                                           "todo o projeto"))
    findings = metrics.get("achados") or []
    slide = deck.slide(project.get("name") or "Projeto", "Teste Sensorial · Análise Geral · recorte: {}".format(
        recorte or "todo o projeto"))
    pptx_kit.kpis(slide, BODY_TOP, [
        ("Participantes", str(metrics.get("participantes", 0))),
        ("Condições", str(len(metrics["n_por_condicao"]))),
        ("Diferenças (Holm)", str(sum(1 for f in findings if f["tipo"] == "diferença"))),
        ("Tendências", str(sum(1 for f in findings if f["tipo"] == "tendência"))),
    ])
    if project.get("objetivo"):
        pptx_kit.textbox(slide, LEFT, BODY_TOP + Inches(1.6), CONTENT_WIDTH, Inches(2.5), project["objetivo"],
                         size=14)

    headers, rows = synthesis_rows(metrics["sintese"], model)
    if rows:
        deck.table_slides("Síntese por amostra e claim", headers, rows,
                          [3.4] + [(12.1 - 3.4) / (len(headers) - 1)] * (len(headers) - 1),
                          subtitle="Validado: teste explícito + cérebro ou corpo no sentido esperado")
    items = [("h", "Diferenças (Holm)")] + [("b", f["texto"]) for f in findings if f["tipo"] == "diferença"]
    if len(items) == 1:
        items.append(("p", "Nenhuma comparação passou na correção de Holm."))
    trends = [f["texto"] for f in findings if f["tipo"] == "tendência"]
    if trends:
        items += [("h", "Tendências")] + [("b", text) for text in trends[:12]]
    deck.flow("Achados", items, size=13)

    summary = metrics["eeg"]["resumo"]
    for measure in model["indices"]:
        _index_slide(deck, model, summary, measure)
    with_findings = list(dict.fromkeys(f["medida"] for f in findings if f["camada"] == "eeg"
                                       and f["medida"] in model["indices"]))
    for measure in (with_findings or [m for m in ("FAI",) if m in model["indices"]])[:MAX_CURVES]:
        _curve_slide(deck, model, measure, metrics.get("filtros") or {})
    headers, rows = comparison_rows(metrics["eeg"]["comparacoes"], model, model["indices"])
    if rows:
        deck.table_slides("EEG: comparações com diferença ou tendência", headers, rows,
                          [2.4, 2.6, 1.4, 0.6, 1.4, 0.8, 1.1, 1.8])

    peripheral_summary = metrics["perifericos"]["resumo"]
    for measure in sensorial_design.PERIPHERAL_CODES:
        if not peripheral_summary.empty and measure in set(peripheral_summary["medida"]):
            _index_slide(deck, model, peripheral_summary, measure)

    association = metrics["associacao"]["por_condicao"]
    if not association.empty:
        labels = sensorial_design.condition_labels(model["design"])
        colors = _colors(model["design"])
        words = list(dict.fromkeys(association.sort_values("score", ascending=False)["palavra"]))
        series, palette = [], []
        for condition, rows in association.groupby("condicao", sort=False):
            by_word = rows.set_index("palavra")["score"]
            series.append((labels.get(condition, condition), [by_word.get(w) for w in words]))
            palette.append(colors.get(condition, ACCENT))
        slide = deck.slide("Teste de associação: Score por claim", "% de “Sim” × CR médio das respostas “Sim”")
        chart = pptx_kit.clustered_chart(slide, _box(), words, series, palette, number_format=NUMBER_FORMAT)
        _allow_negative(chart, series)
        headers, rows = association_rows(association, model)
        deck.table_slides("Teste de associação", headers, rows, [1.6, 3.4, 1.0, 1.3, 1.2, 1.6, 2.0])

    if images:
        pptx_kit.picture_slides(deck, images, "Topomapas do pipeline")

    labels = sensorial_design.condition_labels(model["design"])
    deck.table_slides("Amostra", ["Condição", "EEG", "Periféricos", "Teste de associação"],
                      [[labels.get(r.condicao, r.condicao), str(r.eeg), str(r.perifericos), str(r.associacao)]
                       for r in metrics["n_por_condicao"].itertuples()], [4.0, 2.6, 2.6, 2.9],
                      subtitle="Participantes por condição e camada")
    if analysis and analysis.get("analysis_text"):
        deck.flow("Análise de IA", pptx_kit.markdown_items(analysis["analysis_text"]), size=12,
                  subtitle="{} · {}".format(analysis.get("model") or "", analysis.get("created_at") or ""))
    deck.flow("Limitações", [("b", note) for note in metrics.get("limitacoes") or ["Sem limitações registradas."]],
              size=13)
    return deck.save(), export_filename(project, "pptx", "teste_sensorial_analise")
