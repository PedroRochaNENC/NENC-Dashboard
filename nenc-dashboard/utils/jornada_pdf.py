"""
Relatório em PDF da Análise Geral da Jornada de Compra.

Segue as seções da página, no recorte dela: capa com os números-chave,
achados, gôndola (com a imagem de cada loja), navegação e decisão, escolha,
embalagens (com a foto de cada marca), canal e perfil, amostra e qualidade, a
análise de IA escolhida (quando há) e as limitações. As tabelas completas
ficam no Excel; aqui vai o que se lê.
"""

from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from utils import pdf_report
from utils.jornada_export import export_filename, filters_text
from utils.jornada_format import fmt_number, fmt_pct
from utils.jornada_ingest import TASK_LABELS
from utils.jornada_metrics import decision_by_store, time_kpi
from utils.jornada_taxonomy import fold

FINDING_AREAS = (
    ("Gôndola", ("gondola",)),
    ("Navegação e decisão", ("preco", "navegacao", "decisao")),
    ("Escolha", ("escolha",)),
    ("Embalagens", ("embalagem",)),
)
STATUS_TITLES = {
    "incluida": "Incluída",
    "nao_codificada": "Não codificada",
    "excluida": "Excluída",
    "agregado": "Só agregado",
    "sem_aoi": "Sem AOI",
}
MODE_LABELS = {"rapida": "modo rápido", "aprofundada": "modo aprofundado"}
MAX_FINDINGS_PER_AREA = 8
MAX_ISSUES = 12


def _dash(text) -> str:
    return text if text not in ("", None) else "—"


def _pct(value) -> str:
    return _dash(fmt_pct(value))


def _num(value, digits: int = 1) -> str:
    return _dash(fmt_number(value, digits))


def _frame(value) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _pictures(pdf, pictures: Sequence[Dict], title: str) -> None:
    if pictures:
        pdf_report.heading(pdf, title, level=2)
        pdf_report.image_grid(pdf, [(item["content"], item["title"]) for item in pictures])


def _by_cell_desc(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Linhas agrupadas por célula (na ordem em que aparecem), maior valor primeiro."""
    return pd.concat(
        [rows.sort_values(column, ascending=False) for _, rows in frame.groupby("cell", sort=False)]
    )


# ---------------------------------------------------------------------------
# Seções
# ---------------------------------------------------------------------------

def _cover(pdf, project: Dict, model: Dict, metrics: Dict, generated_at: str) -> None:
    focus = (model.get("meta") or {}).get("focus_brand") or ""
    pdf.set_font(pdf_report.FONT, "B", 8.5)
    pdf.set_text_color(*pdf_report.ACCENT)
    pdf.cell(0, 5, pdf_report.sanitize("JORNADA DE COMPRA · ANÁLISE GERAL"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font(pdf_report.FONT, "B", 20)
    pdf.set_text_color(*pdf_report.INK)
    pdf.multi_cell(0, 9, pdf_report.sanitize(project.get("name") or "Projeto"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    details = []
    if focus:
        details.append("Marca foco: {}".format(focus))
    details.append("Gerado em {}".format(generated_at))
    if project.get("data_version") is not None:
        details.append("dados na versão {}".format(project["data_version"]))
    pdf_report.paragraph(pdf, " · ".join(details), size=9)
    pdf_report.paragraph(pdf, "Recorte: {}".format(filters_text(metrics.get("filters"), model)), size=9)
    pdf.ln(3)

    sample = metrics.get("sample") or {}
    recordings = _frame(model.get("recordings"))
    uncoded = int((recordings["status"] == "nao_codificada").sum()) if not recordings.empty else 0
    time_label, time_value = time_kpi(metrics)
    pdf_report.kpi_row(pdf, [
        ("Participantes na análise", str(sample.get("participants", 0))),
        ("Gravações incluídas", str(sample.get("recordings", 0))),
        ("Não codificadas (fora)", str(uncoded)),
        (time_label, "{} s".format(fmt_number(time_value, 1)) if time_value == time_value else "—"),
    ])
    brand = _frame(metrics.get("brand"))
    if focus and not brand.empty and brand["is_focus"].any():
        pdf.set_font(pdf_report.FONT, "", 8)
        pdf.set_text_color(*pdf_report.MUTED)
        pdf.cell(0, 4.5, pdf_report.sanitize(
            "Share visual de {} por célula (média por participante)".format(focus)),
            new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf_report.kpi_row(pdf, [
            ("{} · n={}".format(row["cell"], int(row["n"])), _pct(row["share_mean"]))
            for _, row in brand[brand["is_focus"]].head(4).iterrows()
        ])
    questions = [line.strip(" -•\t") for line in str(project.get("questions") or "").splitlines()
                 if line.strip(" -•\t")]
    if questions:
        pdf_report.heading(pdf, "Perguntas do estudo", level=2)
        pdf_report.bullets(pdf, questions, size=9)


def _findings(pdf, metrics: Dict) -> None:
    findings = metrics.get("findings") or []
    pdf_report.heading(pdf, "Principais achados")
    if not findings:
        pdf_report.paragraph(pdf, "Sem achados para este recorte.")
        return
    for area, sections in FINDING_AREAS:
        items = [f for f in findings if f.get("section") in sections]
        if not items:
            continue
        pdf_report.heading(pdf, area, level=2)
        pdf_report.bullets(pdf, [
            "{}{}".format(f["text"], " (descritivo)" if f.get("strength") == "descritivo" else "")
            for f in items[:MAX_FINDINGS_PER_AREA]
        ], size=9)
        if len(items) > MAX_FINDINGS_PER_AREA:
            pdf_report.paragraph(pdf, "Mais {} achado(s) desta área no Excel (aba Achados).".format(
                len(items) - MAX_FINDINGS_PER_AREA), size=8, color=pdf_report.MUTED)


def _gondola(pdf, metrics: Dict, pictures: Sequence[Dict] = ()) -> None:
    brand = _frame(metrics.get("brand"))
    if brand.empty:
        return
    pdf_report.heading(pdf, "Gôndola")
    pdf_report.paragraph(
        pdf,
        "Share visual: média, por participante, da fração da atenção às marcas que cada uma levou. "
        "Só entram as gravações incluídas; a marca foco aparece em destaque.",
        size=9,
    )
    pdf.ln(2)
    for cell, rows in brand.groupby("cell", sort=False):
        n = int(rows["n"].iloc[0])
        ranked = rows.sort_values("share_mean", ascending=False)
        pdf_report.hbar_chart(
            pdf,
            [(row["brand"], row["share_mean"], bool(row["is_focus"])) for _, row in ranked.iterrows()],
            value_format=fmt_pct,
            title="{} · n={}".format(cell, n),
            note="Descritivo: menos de 5 participantes." if n < 5 else "",
        )
    pdf_report.heading(pdf, "Funil, primeira olhada e presença", level=2)
    pdf_report.paragraph(
        pdf,
        "Notou = olhou; examinou = ao menos o limiar de exame na marca; retornou = voltou a uma AOI "
        "dela. TTFF relativo = segundos depois da primeira marca vista. Índice = share ÷ fração da "
        "gôndola da marca (acima de 1, rende mais atenção que o espaço que ocupa).",
        size=8, color=pdf_report.MUTED,
    )
    pdf.ln(1)
    pdf_report.simple_table(
        pdf,
        ["Célula", "Marca", "n", "Share", "Notou", "Examinou", "Retornou", "1ª notada", "TTFF rel. (s)", "Índice"],
        [[row["cell"], row["brand"], int(row["n"]), _pct(row["share_mean"]), _pct(row["reach"]),
          _pct(row["examined"]), _pct(row["revisit"]), _pct(row["first_noticed"]),
          _num(row["rel_ttff_median"]), _num(row["presence_index"], 2)]
         for _, row in _by_cell_desc(brand, "share_mean").iterrows()],
        [42, 26, 8, 14, 14, 16, 16, 16, 16, 12],
    )
    sku = _frame(metrics.get("sku"))
    if not sku.empty:
        pdf_report.heading(pdf, "Produtos mais vistos", level=2)
        top = pd.concat([rows.head(5) for _, rows in _by_cell_desc(sku, "share_mean").groupby("cell", sort=False)])
        pdf_report.simple_table(
            pdf,
            ["Célula", "Produto", "Marca", "Share", "Alcance", "Tempo médio (s)"],
            [[row["cell"], row["product"], row["brand"], _pct(row["share_mean"]), _pct(row["reach"]),
              _num(row["dwell_mean_s"], 2)] for _, row in top.iterrows()],
            [44, 58, 26, 16, 16, 20],
        )
    _pictures(pdf, pictures, "A gôndola de cada loja")


def _navigation(pdf, metrics: Dict) -> None:
    attributes = _frame(metrics.get("attributes"))
    price = _frame(metrics.get("price"))
    decision = _frame(metrics.get("decision"))
    if attributes.empty and price.empty and decision.empty:
        return
    pdf_report.heading(pdf, "Navegação e decisão")
    if not attributes.empty:
        pdf_report.heading(pdf, "Atributos dos produtos", level=2)
        pdf_report.paragraph(pdf, "Fração da atenção entre os produtos em que o atributo aparece no "
                                  "nome da AOI, e a presença do valor na gôndola. Índice = share ÷ "
                                  "presença (acima de 1, atenção além do espaço ocupado).",
                             size=8, color=pdf_report.MUTED)
        pdf.ln(1)
        pdf_report.simple_table(
            pdf,
            ["Célula", "Atributo", "Valor", "n", "Share", "Alcance", "Presença", "Índice"],
            [[row["cell"], row["dimension"], row["value"], int(row["n_defined"]), _pct(row["share_mean"]),
              _pct(row["reach"]), _pct(row.get("presence")), _num(row.get("presence_index"), 2)]
             for _, row in attributes.iterrows()],
            [52, 22, 28, 10, 17, 17, 18, 16],
        )
    if not price.empty:
        pdf_report.heading(pdf, "Etiquetas de preço", level=2)
        pdf_report.simple_table(
            pdf,
            ["Célula", "Produto", "Viu o preço", "Tempo médio (s)", "Preço ÷ (preço + produto)"],
            [[row["cell"], row["product"], _pct(row["reach"]), _num(row["dwell_mean_s"], 2),
              _pct(row["price_fraction"])] for _, row in price.iterrows()],
            [48, 58, 22, 24, 28],
        )
    if not decision.empty:
        pdf_report.heading(pdf, "Tempo até a decisão", level=2)
        stores = decision_by_store(decision)
        if not stores.empty:
            pdf_report.hbar_chart(
                pdf,
                [(row["label"], row["median_s"], False) for _, row in stores.iterrows()],
                value_format=lambda v: "{} s".format(fmt_number(v, 1)),
                title="Mediana por tarefa e loja, em segundos (tempo de compra; planilha onde não há)",
                label_width=70,
            )
        pdf_report.simple_table(
            pdf,
            ["Tarefa", "Fonte", "Agrupamento", "Grupo", "n", "Mediana (s)", "Mín", "Máx"],
            [[row["task_label"], row["source_label"], str(row["group_type"]).capitalize(), row["group"],
              int(row["n"]), _num(row["median_s"]), _num(row["min_s"]), _num(row["max_s"])]
             for _, row in decision.iterrows()],
            [34, 38, 22, 34, 8, 18, 13, 13],
        )


def _choice(pdf, metrics: Dict) -> None:
    choices = _frame(metrics.get("choices"))
    choice = _frame(metrics.get("choice"))
    if choices.empty:
        return
    pdf_report.heading(pdf, "Escolha")
    pdf_report.paragraph(
        pdf,
        "Produto escolhido segundo o registro de campo, normalizado pelas marcas do projeto. Na jornada "
        "livre é a compra observada; na estimulada, a escolha pedida. Quem escolheu duas marcas conta nas duas.",
        size=9,
    )
    pdf.ln(2)
    for _, rows in choice.groupby("task", sort=False):
        total = rows[rows["group_type"] == "total"].sort_values("share", ascending=False)
        if total.empty:
            continue
        n = int(total["n"].iloc[0])
        pdf_report.hbar_chart(
            pdf,
            [(row["brand"], row["share"], bool(row["is_focus"])) for _, row in total.iterrows()],
            value_format=fmt_pct,
            max_value=1.0,
            title="{} · todas as lojas · n={}".format(rows["task_label"].iloc[0], n),
            note="Descritivo: menos de 5 participantes." if n < 5 else "",
        )
        groups = rows[rows["group_type"].isin(["loja", "canal"])]
        pdf_report.simple_table(
            pdf,
            ["Por", "Grupo", "Marca", "Escolheram", "Fração"],
            [[str(row["group_type"]).capitalize(), row["group"], row["brand"],
              "{}/{}".format(int(row["chose_n"]), int(row["n"])), _pct(row["share"])]
             for _, row in groups.iterrows()],
            [20, 52, 48, 30, 30],
        )
    attention = _frame(metrics.get("attention_choice"))
    if not attention.empty:
        pdf_report.heading(pdf, "Da atenção à escolha", level=2)
        pdf_report.paragraph(
            pdf,
            "Dos {} participantes com escolha e olhar codificado na mesma gravação, {} notaram a marca "
            "escolhida, {} a examinaram, {} a notaram primeiro e {} a viram mais que as outras.".format(
                len(attention), int(attention["looked"].sum()), int(attention["examined"].sum()),
                int(attention["first_noticed"].sum()), int(attention["top_share"].sum())),
            size=9,
        )
        pdf.ln(1)
    consideration = _frame(metrics.get("consideration"))
    if not consideration.empty:
        pdf_report.heading(pdf, "Conjunto considerado e embalagens", level=2)
        pdf_report.simple_table(
            pdf,
            ["Tarefa", "Loja", "n", "Consideradas (média)", "Só uma marca", "Embalagens citadas"],
            [[row["task_label"], row["store_label"], int(row["n"]), _num(row["considered_mean"]),
              int(row["single_brand_n"]), _dash(row["packs"])] for _, row in consideration.iterrows()],
            [32, 30, 10, 30, 20, 58],
        )


def _packaging(pdf, metrics: Dict, focus: str, pictures: Sequence[Dict] = ()) -> None:
    packaging = metrics.get("packaging") or {}
    elements = _frame(packaging.get("elements"))
    if elements.empty:
        if pictures:
            pdf_report.heading(pdf, "Embalagens")
            _pictures(pdf, pictures, "As embalagens")
        return
    brands = _frame(packaging.get("brands"))
    coverage = _frame(packaging.get("coverage"))
    pdf_report.heading(pdf, "Embalagens")
    group = "Todos" if (elements["group"] == "Todos").any() else elements["group"].iloc[0]
    rows = elements[elements["group"] == group]
    text = "Dados agregados por perfil, sem variação entre participantes. Base: {}, n={}.".format(
        rows["profile"].iloc[0], int(rows["n_group"].max()))
    cov = coverage[coverage["group"] == group] if not coverage.empty else coverage
    if not cov.empty and cov["aoi_coverage"].notna().any():
        text += (" Os elementos mapeados somam {} do tempo gravado; o resto ficou fora de "
                 "qualquer elemento.".format(_pct(cov["aoi_coverage"].iloc[0])))
    pdf_report.paragraph(pdf, text, size=9)
    pdf.ln(2)
    focus_rows = rows[rows["brand"].map(fold) == fold(focus)] if focus else rows.iloc[0:0]
    if not focus_rows.empty:
        ranked = focus_rows.sort_values("element_share", ascending=False)
        pdf_report.hbar_chart(
            pdf,
            [(row["element_label"], row["element_share"], True) for _, row in ranked.iterrows()],
            value_format=fmt_pct,
            title="Onde o olhar cai na embalagem de {}".format(focus),
        )
    summary = []
    for brand_name, brand_rows in rows.groupby("brand", sort=False):
        top = brand_rows.sort_values("element_share", ascending=False).iloc[0]
        logo = brands[(brands["group"] == group) & (brands["brand"] == brand_name)] if not brands.empty else brands
        summary.append([brand_name, top["element_label"], _pct(top["element_share"]), _pct(top["reach"]),
                        _pct(logo["logo_reach"].iloc[0]) if not logo.empty else "—"])
    pdf_report.heading(pdf, "Elemento que mais atrai em cada embalagem", level=2)
    pdf_report.simple_table(
        pdf,
        ["Marca", "Elemento mais visto", "Share na marca", "Viram o elemento", "Viram o logo"],
        summary,
        [40, 58, 27, 28, 27],
    )
    if focus and not brands.empty:
        logos = brands[brands["brand"].map(fold) == fold(focus)]
        if len(logos) > 1:
            pdf_report.hbar_chart(
                pdf,
                [(row["profile"], row["logo_reach"], row["group"] == "Todos") for _, row in logos.iterrows()],
                value_format=fmt_pct,
                max_value=1.0,
                title="Logo de {} visto, por perfil".format(focus),
            )
    _pictures(pdf, pictures, "As embalagens")


def _channel(pdf, metrics: Dict) -> None:
    confounds = metrics.get("confounds") or []
    comparisons = _frame(metrics.get("comparisons"))
    if not confounds and comparisons.empty:
        return
    pdf_report.heading(pdf, "Canal e perfil")
    for confound in confounds:
        first, second = confound["labels"]
        pdf_report.paragraph(
            pdf,
            "{} e {} andam juntos nesta amostra ({}): uma diferença entre um pode ser do outro, "
            "e a comparação não isola o efeito.".format(first, second.lower(), confound["mapping"]),
            size=9, color=pdf_report.INK,
        )
        pdf.ln(1)
    if not comparisons.empty:
        pdf_report.simple_table(
            pdf,
            ["Tarefa", "Por", "Grupo A", "Grupo B", "Média A", "Média B", "δ de Cliff", "p", "Método"],
            [[TASK_LABELS.get(row["task"], row["task"]), row["by"],
              "{} (n={})".format(row["group_a"], row["n_a"]), "{} (n={})".format(row["group_b"], row["n_b"]),
              _pct(row["mean_a"]), _pct(row["mean_b"]), _num(row["cliffs_delta"], 2), _num(row["p_value"], 3),
              row["method"]] for _, row in comparisons.iterrows()],
            [24, 12, 33, 33, 14, 14, 15, 10, 25],
        )
        pdf_report.paragraph(pdf, "Com menos de 5 participantes por grupo a comparação é só descritiva; "
                                  "o δ de Cliff vai de −1 a 1 (0 = grupos iguais).", size=8,
                             color=pdf_report.MUTED)


def _sample(pdf, model: Dict, metrics: Dict, quality: Optional[Dict]) -> None:
    pdf_report.heading(pdf, "Amostra e qualidade")
    by_cell = (metrics.get("sample") or {}).get("by_cell") or []
    if by_cell:
        pdf_report.simple_table(pdf, ["Célula", "Participantes"],
                                [[item["cell"], item["n"]] for item in by_cell], [120, 60])
    recordings = _frame(model.get("recordings"))
    if not recordings.empty:
        counts = recordings.groupby(["task_label", "status"]).size().unstack(fill_value=0)
        statuses = [s for s in STATUS_TITLES if s in counts.columns]
        pdf_report.heading(pdf, "Gravações por situação", level=2)
        pdf_report.simple_table(
            pdf,
            ["Tarefa"] + [STATUS_TITLES[s] for s in statuses],
            [[task] + [int(counts.loc[task, s]) for s in statuses] for task in counts.index],
            [60] + [120 / max(1, len(statuses))] * len(statuses),
        )
    summary = _frame((quality or {}).get("summary"))
    if not summary.empty:
        tally = summary["quality"].value_counts()
        pdf_report.paragraph(pdf, "Qualidade das gravações: {} OK, {} com atenção, {} com problema.".format(
            int(tally.get("pass", 0)), int(tally.get("warn", 0)), int(tally.get("fail", 0))), size=9)
        pdf.ln(1)
    issues = [i for i in model.get("issues") or [] if i.get("level") in ("warn", "error")]
    if issues:
        pdf_report.heading(pdf, "Avisos dos dados", level=2)
        pdf_report.bullets(pdf, ["{}{}".format(i["message"], " ({})".format(i["ref"]) if i.get("ref") else "")
                                 for i in issues[:MAX_ISSUES]], size=8.5)
        if len(issues) > MAX_ISSUES:
            pdf_report.paragraph(pdf, "Mais {} aviso(s) no Excel (aba Avisos).".format(len(issues) - MAX_ISSUES),
                                 size=8, color=pdf_report.MUTED)


def _analysis(pdf, analysis: Dict, data_version) -> None:
    pdf_report.ensure_space(pdf, 90)
    pdf_report.heading(pdf, "Análise de IA")
    details = [
        analysis.get("model") or "",
        MODE_LABELS.get(analysis.get("mode"), analysis.get("mode") or ""),
        "gerada em {}".format(analysis["created_at"]) if analysis.get("created_at") else "",
    ]
    pdf_report.paragraph(pdf, " · ".join(d for d in details if d), size=8.5, color=pdf_report.MUTED)
    if analysis.get("data_version") is not None and analysis.get("data_version") != data_version:
        pdf_report.paragraph(pdf, "Os dados do projeto mudaram depois desta análise; os números das "
                                  "seções anteriores são os atuais.", size=8.5, color=pdf_report.INK, style="B")
    pdf.ln(2)
    pdf_report.render_markdown_lite(pdf, analysis.get("analysis_text") or "")
    citations = analysis.get("citations") or []
    if citations:
        pdf_report.heading(pdf, "Referências", level=2)
        items = []
        for citation in citations:
            data = citation if isinstance(citation, dict) else {"quote": str(citation)}
            quote = str(data.get("quote") or "").strip().replace("**", "")
            if len(quote) > 300:
                quote = quote[:300].rstrip() + "…"
            name = str(data.get("filename") or "Documento").replace("**", "")
            items.append("**{}**{}".format(name, " — “{}”".format(quote) if quote else ""))
        pdf_report.bullets(pdf, items, size=8.5)


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

def build_pdf(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    analysis: Optional[Dict] = None,
    quality: Optional[Dict] = None,
    generated_at: Optional[str] = None,
    images: Optional[List[Dict]] = None,
) -> Tuple[bytes, str]:
    """Relatório da Análise Geral no recorte da página; devolve os bytes e o nome.

    `images` vem de `jornada_gallery.report_images`, cada uma com `content`
    (bytes já reduzidos): as de loja entram na Gôndola, as de marca em Embalagens.
    """

    generated_at = generated_at or datetime.now().strftime("%d/%m/%Y %H:%M")
    focus = (model.get("meta") or {}).get("focus_brand") or ""
    pdf = pdf_report.ReportPDF("Jornada de Compra · {}".format(project.get("name") or "Projeto"))
    pdf.add_page()
    _cover(pdf, project, model, metrics, generated_at)
    _findings(pdf, metrics)
    pictures = images or []
    _gondola(pdf, metrics, [item for item in pictures if item.get("group") == "loja"])
    _navigation(pdf, metrics)
    _choice(pdf, metrics)
    _packaging(pdf, metrics, focus, [item for item in pictures if item.get("group") == "marca"])
    _channel(pdf, metrics)
    _sample(pdf, model, metrics, quality)
    if analysis and analysis.get("analysis_text"):
        _analysis(pdf, analysis, project.get("data_version"))
    pdf_report.heading(pdf, "Limitações")
    pdf_report.bullets(pdf, metrics.get("limitations") or ["Sem limitações registradas."], size=9)
    return pdf_report.output_bytes(pdf), export_filename(project, "pdf", "jornada_analise")
