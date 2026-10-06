"""
Relatório PDF da Análise Geral do Teste Sensorial (fpdf2, A4).

Mesmo conteúdo da página, no recorte dela: capa com os números do estudo,
síntese por amostra e claim, achados, EEG (cada índice por condição e etapa,
comparações e topomapas), periféricos, teste de associação, amostra e
qualidade, a análise de IA escolhida e as limitações. Só códigos de
participante; nenhum nome.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from utils import pdf_report, sensorial_design
from utils.project_ai import MODE_LABELS
from utils.sensorial_export import (
    association_rows,
    comparison_rows,
    export_filename,
    measure_rows,
    synthesis_rows,
)


def _widths(count: int, first: float = 36.0) -> List[float]:
    rest = (pdf_report.CONTENT_WIDTH - first) / max(1, count - 1)
    return [first] + [rest] * (count - 1)


def _cover(pdf, project: Dict, metrics: Dict, recorte: str, generated_at: str) -> None:
    pdf.set_font(pdf_report.FONT, "B", 8.5)
    pdf.set_text_color(*pdf_report.ACCENT)
    pdf.cell(0, 5, pdf_report.sanitize("TESTE SENSORIAL · ANÁLISE GERAL"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font(pdf_report.FONT, "B", 20)
    pdf.set_text_color(*pdf_report.INK)
    pdf.multi_cell(0, 9, pdf_report.sanitize(project.get("name") or "Projeto"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    details = ["Gerado em {}".format(generated_at)]
    if project.get("data_version") is not None:
        details.append("dados na versão {}".format(project["data_version"]))
    pdf_report.paragraph(pdf, " · ".join(details), size=9)
    pdf_report.paragraph(pdf, "Recorte: {}".format(recorte), size=9)
    if project.get("objetivo"):
        pdf.ln(1)
        pdf_report.paragraph(pdf, project["objetivo"], size=9)
    pdf.ln(3)
    findings = metrics.get("achados") or []
    pdf_report.kpi_row(pdf, [
        ("Participantes no recorte", str(metrics.get("participantes", 0))),
        ("Condições", str(len(metrics["n_por_condicao"]))),
        ("Diferenças (Holm)", str(sum(1 for f in findings if f["tipo"] == "diferença"))),
        ("Tendências", str(sum(1 for f in findings if f["tipo"] == "tendência"))),
    ])


def _synthesis(pdf, model: Dict, metrics: Dict) -> None:
    headers, rows = synthesis_rows(metrics["sintese"], model)
    if not rows:
        return
    pdf_report.heading(pdf, "Síntese por amostra e claim")
    pdf_report.paragraph(pdf, "Validado: o teste explícito confirma e o cérebro ou o corpo mostram diferença no "
                              "sentido esperado contra o controle (ou o basal). Parcial: só uma das camadas.",
                         size=8.5)
    pdf.ln(1)
    pdf_report.prose_table(pdf, headers, rows, _widths(len(headers), 64.0), size=8)


def _findings(pdf, metrics: Dict) -> None:
    pdf_report.heading(pdf, "Achados")
    findings = metrics.get("achados") or []
    differences = [f["texto"] for f in findings if f["tipo"] == "diferença"]
    pdf_report.bullets(pdf, differences or ["Nenhuma comparação passou na correção de Holm."], size=8.5)
    trends = [f["texto"] for f in findings if f["tipo"] == "tendência"]
    if trends:
        pdf_report.heading(pdf, "Tendências (p bruto abaixo de alfa, sem passar no Holm)", level=2)
        pdf_report.bullets(pdf, trends[:15], size=8.5)
        if len(trends) > 15:
            pdf_report.paragraph(pdf, "… e mais {} no Excel.".format(len(trends) - 15), size=8)


def _layer(pdf, model: Dict, block: Dict, measures, title: str) -> None:
    if block["resumo"].empty or not measures:
        return
    pdf_report.heading(pdf, title)
    names = model["index_names"]
    for measure in measures:
        headers, rows = measure_rows(block["resumo"], measure, model["design"])
        if not rows:
            continue
        pdf_report.heading(pdf, names.get(measure, measure), level=3)
        pdf_report.simple_table(pdf, headers, rows, _widths(len(headers)), size=7.5)
    headers, rows = comparison_rows(block["comparacoes"], model, measures)
    pdf_report.heading(pdf, "Comparações com diferença ou tendência", level=2)
    if rows:
        pdf_report.prose_table(pdf, headers, rows, [34, 40, 22, 9, 20, 11, 15, 19], size=7.5)
    else:
        pdf_report.paragraph(pdf, "Nenhuma.", size=8.5)


def _association(pdf, model: Dict, metrics: Dict) -> None:
    headers, rows = association_rows(metrics["associacao"]["por_condicao"], model)
    if not rows:
        return
    pdf_report.heading(pdf, "Teste de associação")
    pdf_report.paragraph(pdf, "Score = % de “Sim” × CR médio das respostas “Sim” (o quanto a pessoa respondeu mais "
                              "rápido que a média dela).", size=8.5)
    pdf_report.prose_table(pdf, headers, rows, [24, 48, 14, 18, 16, 22, 38], size=7.5)


def _sample(pdf, model: Dict, metrics: Dict) -> None:
    pdf_report.heading(pdf, "Amostra e qualidade")
    labels = sensorial_design.condition_labels(model["design"])
    counts = metrics["n_por_condicao"]
    pdf_report.simple_table(pdf, ["Condição", "EEG", "Periféricos", "Teste de associação"],
                            [[labels.get(r.condicao, r.condicao), str(r.eeg), str(r.perifericos), str(r.associacao)]
                             for r in counts.itertuples()], _widths(4, 60.0), size=8)
    warnings = [issue["message"] for issue in model["issues"] if issue["level"] == "warn"]
    if warnings:
        pdf_report.heading(pdf, "Avisos dos dados", level=2)
        pdf_report.bullets(pdf, warnings, size=8)


def _analysis(pdf, analysis: Dict, data_version) -> None:
    pdf_report.ensure_space(pdf, 90)
    pdf_report.heading(pdf, "Análise de IA")
    details = [analysis.get("model") or "", MODE_LABELS.get(analysis.get("mode"), analysis.get("mode") or ""),
               "gerada em {}".format(analysis["created_at"]) if analysis.get("created_at") else ""]
    pdf_report.paragraph(pdf, " · ".join(d for d in details if d), size=8.5, color=pdf_report.MUTED)
    if analysis.get("data_version") is not None and analysis.get("data_version") != data_version:
        pdf_report.paragraph(pdf, "Os dados do projeto mudaram depois desta análise; os números das seções "
                                  "anteriores são os atuais.", size=8.5, color=pdf_report.INK, style="B")
    pdf.ln(2)
    pdf_report.render_markdown_lite(pdf, analysis.get("analysis_text") or "")


def build_pdf(project: Dict, model: Dict, metrics: Dict, *, recorte: str = "", analysis: Optional[Dict] = None,
              images: Optional[List[Tuple[bytes, str]]] = None,
              generated_at: Optional[str] = None) -> Tuple[bytes, str]:
    """Relatório no recorte da página; `images` = (bytes, legenda) dos topomapas. Devolve bytes e nome."""
    generated_at = generated_at or datetime.now().strftime("%d/%m/%Y %H:%M")
    pdf = pdf_report.ReportPDF("Teste Sensorial · {}".format(project.get("name") or "Projeto"))
    pdf.add_page()
    _cover(pdf, project, metrics, recorte or "todo o projeto", generated_at)
    _synthesis(pdf, model, metrics)
    _findings(pdf, metrics)
    _layer(pdf, model, metrics["eeg"], list(model["indices"]), "EEG: índices do relatório")
    if images:
        pdf_report.heading(pdf, "Topomapas do pipeline", level=2)
        pdf_report.image_grid(pdf, images, columns=4, max_height=40.0)
    peripherals = [m for m in sensorial_design.PERIPHERAL_CODES
                   if m in set(metrics["perifericos"]["resumo"]["medida"])] if not metrics["perifericos"]["resumo"].empty \
        else []
    _layer(pdf, model, metrics["perifericos"], peripherals, "Periféricos")
    _association(pdf, model, metrics)
    _sample(pdf, model, metrics)
    if analysis and analysis.get("analysis_text"):
        _analysis(pdf, analysis, project.get("data_version"))
    pdf_report.heading(pdf, "Limitações")
    pdf_report.bullets(pdf, metrics.get("limitacoes") or ["Sem limitações registradas."], size=8.5)
    return pdf_report.output_bytes(pdf), export_filename(project, "pdf", "teste_sensorial_analise")
