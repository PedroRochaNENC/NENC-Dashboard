"""
Jornada de Compra — Análise Geral.

A leitura consolidada do projeto, no molde da Análise Geral do NencBoost:
resumo com os achados, gôndola (atenção por marca, funil, primeira olhada,
presença, SKUs), navegação e decisão (atributos, preço, tempo até a decisão),
embalagens, canal e perfil, amostra e qualidade — e, por fim, a IA e as
exportações.

Só entram as gravações incluídas; os filtros valem para todas as seções. Cada
gráfico tem a tabela equivalente logo abaixo.
"""

import json

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")
pode_editar = auth.can_write(user)

from utils import jornada_charts as charts
from utils import jornada_db
from utils.ai_provider import get_openai_client, get_vector_store_id
from utils.jornada_ai import AI_MODELS, chat_answer, generate_analysis, send_analysis_to_kb
from utils.jornada_cache import get_project_metrics, get_project_model
from utils.jornada_export import build_excel, filters_text
from utils.jornada_ingest import TASK_LABELS
from utils.jornada_metrics import ALL_STORES, TIME_SOURCE_LABELS, time_kpi
from utils.jornada_model import RECORDING_STATUS_LABELS
from utils.jornada_pdf import build_pdf
from utils.jornada_pptx import build_pptx
from utils.jornada_quality import run_quality
from utils.jornada_ui import active_project, fmt_number, fmt_pct, fmt_seconds

jornada_db.init_db()
project = active_project()
project_id = project["id"]

SECTIONS = [
    "Resumo",
    "Gôndola",
    "Navegação e decisão",
    "Embalagens",
    "Canal e perfil",
    "Amostra e qualidade",
    "IA",
    "Exportar",
]
MODE_LABELS = {"rapida": "rápida", "aprofundada": "aprofundada"}
SECTION_OF_FINDING = {
    "gondola": "Gôndola",
    "preco": "Navegação e decisão",
    "navegacao": "Navegação e decisão",
    "decisao": "Navegação e decisão",
    "embalagem": "Embalagens",
}

ui.inject_theme()
ui.breadcrumb("Jornada de Compra", project["name"], "Análise Geral")
page_title("chart-bar", "Análise Geral", project["name"])

model = get_project_model(project)
recordings = model["recordings"]
if recordings.empty and model["pooled"].empty:
    st.info("Ainda não há dados neste projeto. Envie os arquivos do estudo em **Uploads**.")
    st.stop()

meta = model["meta"]
focus_brand = meta.get("focus_brand") or ""
brand_colors = charts.brand_color_map(meta.get("brands") or [], focus_brand)
brand_order = list(brand_colors)

# ------------------------------------------------------------------
# Filtros: uma linha acima de tudo que eles recortam
# ------------------------------------------------------------------
included_all = recordings[recordings["status"] == "incluida"] if not recordings.empty else recordings
f1, f2, f3, f4 = st.columns([2, 2, 2, 1.4])
task_options = sorted(included_all["task"].unique()) if not included_all.empty else []
store_labels = dict(zip(model["stores"]["store"], model["stores"]["label"]))
store_options = sorted(included_all["store"].unique()) if not included_all.empty else []
profile_options = sorted(p for p in included_all["profile"].unique() if p) if not included_all.empty else []
chosen_tasks = f1.multiselect(
    "Tarefas", task_options, format_func=lambda t: TASK_LABELS.get(t, t), key="jc_ag_tasks",
    placeholder="Todas",
)
chosen_stores = f2.multiselect(
    "Lojas", store_options, format_func=lambda s: store_labels.get(s, s), key="jc_ag_stores",
    placeholder="Todas",
)
chosen_profiles = f3.multiselect("Perfis", profile_options, key="jc_ag_profiles", placeholder="Todos")
with_price = f4.toggle("Preço conta para a marca", key="jc_ag_price",
                       help="Soma as etiquetas de preço à atenção de cada marca.")
filters = {
    "tasks": chosen_tasks,
    "stores": chosen_stores,
    "profiles": chosen_profiles,
    "kinds": ["produto", "preco"] if with_price else ["produto"],
}
metrics = get_project_metrics(project, filters)
brand = metrics["brand"]

section = st.segmented_control(
    "Seção", SECTIONS, default="Resumo", key="jc_ag_section", label_visibility="collapsed"
) or "Resumo"


def _table(frame: pd.DataFrame, columns: dict, formats: dict = None, key: str = "") -> None:
    """Tabela equivalente ao gráfico, com rótulos em português."""
    if frame.empty:
        return
    view = frame[[c for c in columns if c in frame.columns]].copy()
    for column, formatter in (formats or {}).items():
        if column in view:
            view[column] = view[column].map(formatter)
    view = view.rename(columns=columns)
    with st.expander("Ver tabela"):
        st.dataframe(view, hide_index=True, width="stretch", key=key or None)


def _analysis_label(analysis: dict) -> str:
    """Uma análise salva em uma linha: quando, modo, modelo e se os dados mudaram depois."""
    parts = [
        str(analysis.get("created_at") or "")[:16],
        "análise {}".format(MODE_LABELS.get(analysis.get("mode"), analysis.get("mode") or "")),
        analysis.get("model") or "",
    ]
    label = " · ".join(part for part in parts if part.strip())
    if analysis.get("data_version") is not None and analysis.get("data_version") != project.get("data_version"):
        label += " · dados mudaram depois"
    return label


def _cells(frame: pd.DataFrame, include_all: bool = True) -> list:
    if frame.empty:
        return []
    cells = list(dict.fromkeys(frame["cell"]))
    if not include_all:
        cells = [c for c in cells if not c.endswith("todas as lojas")]
    return cells


# ==================================================================
# Resumo
# ==================================================================
if section == "Resumo":
    sample = metrics["sample"]
    uncoded = int((recordings["status"] == "nao_codificada").sum()) if not recordings.empty else 0
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Participantes na análise", sample["participants"])
    k2.metric("Gravações incluídas", sample["recordings"])
    k3.metric("Não codificadas (fora)", uncoded)
    time_label, time_value = time_kpi(metrics)
    k4.metric(time_label, "{} s".format(fmt_number(time_value, 1)) if time_value == time_value else "—")

    if focus_brand and not brand.empty:
        focus_rows = brand[brand["is_focus"]]
        if not focus_rows.empty:
            st.markdown("**{} por célula**".format(focus_brand))
            columns = st.columns(min(4, len(focus_rows)))
            for column, (_, row) in zip(columns, focus_rows.head(4).iterrows()):
                column.metric(
                    row["cell"],
                    fmt_pct(row["share_mean"]),
                    help="Share média da atenção entre as marcas; alcance {} · n={}".format(
                        fmt_pct(row["reach"]), int(row["n"])),
                )

    st.subheader("Achados")
    findings = metrics["findings"]
    if not findings:
        st.info("Sem achados para esta seleção.")
    for area in ("Gôndola", "Navegação e decisão", "Embalagens"):
        items = [f for f in findings if SECTION_OF_FINDING.get(f["section"]) == area]
        if not items:
            continue
        st.markdown("**{}**".format(area))
        for item in items[:8]:
            tag = "" if item["strength"] != "descritivo" else " · _descritivo_"
            st.markdown("- {}{}".format(item["text"], tag))
        if len(items) > 8:
            st.caption("Mais {} na seção {}.".format(len(items) - 8, area))

    with st.expander("Limitações da análise", expanded=True):
        for note in metrics["limitations"]:
            st.markdown("- {}".format(note))

# ==================================================================
# Gondola
# ==================================================================
elif section == "Gôndola":
    if brand.empty:
        st.info("Não há gravações individuais incluídas nesta seleção.")
        st.stop()
    st.subheader("Share visual por marca")
    st.caption(
        "Média, por participante, da fração da atenção às marcas que cada uma levou. "
        "A share ponderada pelo tempo está na tabela."
    )
    st.plotly_chart(charts.share_stacked(brand, brand_colors), width="stretch")
    _table(
        brand,
        {"cell": "Célula", "brand": "Marca", "n": "n", "share_mean": "Share (média)",
         "share_weighted": "Share (ponderada pelo tempo)", "reach": "Alcance",
         "dwell_mean_s": "Tempo médio (s)", "visits_mean": "Visitas (média)"},
        {"share_mean": fmt_pct, "share_weighted": fmt_pct, "reach": fmt_pct,
         "dwell_mean_s": lambda v: fmt_number(v, 1), "visits_mean": lambda v: fmt_number(v, 1)},
        key="jc_tab_share",
    )

    cells = _cells(brand)
    cell = st.selectbox("Célula", cells, key="jc_ag_cell")
    cell_brand = brand[brand["cell"] == cell]
    n = int(cell_brand["n"].iloc[0]) if not cell_brand.empty else 0
    st.caption("n = {} participante(s){}".format(
        n, " — descritivo, sem teste estatístico" if n < 5 else ""))

    left, right = st.columns(2)
    with left:
        st.markdown("**Funil de atenção**")
        st.caption("Notou: olhou. Examinou: ≥ {} s na marca. Retornou: voltou a uma AOI dela.".format(
            fmt_number(meta.get("examined_threshold_s", 1.0), 1)))
        st.plotly_chart(charts.funnel_bars(cell_brand, brand_order), width="stretch")
    with right:
        st.markdown("**Primeira marca notada**")
        st.caption("Fração dos participantes cuja primeira olhada foi em cada marca.")
        first = cell_brand.assign(label=cell_brand["brand"])
        st.plotly_chart(charts.emphasis_bars(first, "label", "first_noticed"), width="stretch")

    st.markdown("**Tempo até a primeira olhada**")
    relative = st.toggle(
        "Relativo à primeira marca vista", value=True, key="jc_ag_rel",
        help="Na jornada livre o tempo absoluto inclui a caminhada até a categoria.",
    )
    per = metrics["per_recording_brand"]
    cell_row = cell_brand.iloc[0] if not cell_brand.empty else None
    if cell_row is not None:
        scope = per[per["task"] == cell_row["task"]]
        if cell_row["store"] != ALL_STORES:
            scope = scope[scope["store"] == cell_row["store"]]
        st.plotly_chart(charts.ttff_strip(scope, brand_order, focus_brand, relative), width="stretch")

        media = jornada_db.list_media(project_id)
        with_video = {
            "{}|{}|{}".format(m["participant_code"], m["task"], m["store"]) for m in media
        }
        focus_rows = scope[(scope["brand"] == focus_brand) & scope["looked"] & scope["ttff_s"].notna()]
        focus_rows = focus_rows[focus_rows["recording_key"].isin(with_video)]
        if focus_brand and not focus_rows.empty:
            c1, c2 = st.columns([3, 1])
            chosen = c1.selectbox(
                "Ver no vídeo a primeira olhada em {}".format(focus_brand),
                focus_rows["recording_key"].tolist(),
                format_func=lambda key: "{} · {}".format(
                    key.split("|")[0],
                    fmt_seconds(focus_rows.set_index("recording_key").loc[key, "ttff_s"]),
                ),
                key="jc_ag_video_pick",
            )
            if c2.button("Abrir vídeo", width="stretch", key="jc_ag_video_go"):
                seconds = float(focus_rows.set_index("recording_key").loc[chosen, "ttff_s"])
                st.session_state["jc_media_focus"] = {"recording_key": chosen, "seconds": max(0.0, seconds - 1.0)}
                st.switch_page("modules/jornada_compra/participantes.py")

    left, right = st.columns(2)
    with left:
        st.markdown("**Atenção × presença na gôndola**")
        source = cell_brand["presence_source"].iloc[0] if not cell_brand.empty else ""
        st.caption("Share ÷ fração da gôndola da marca ({}). Acima de 1: rende mais que o espaço.".format(
            source or "sem presença"))
        st.plotly_chart(charts.presence_index_chart(cell_brand, brand_order), width="stretch")
    with right:
        st.markdown("**Mapa: célula × marca**")
        st.plotly_chart(charts.store_brand_heatmap(brand), width="stretch")

    _table(
        cell_brand,
        {"brand": "Marca", "reach": "Notou", "examined": "Examinou", "revisit": "Retornou",
         "first_noticed": "1ª notada", "ttff_median": "TTFF mediana (s)",
         "rel_ttff_median": "TTFF relativo (s)", "presence": "Presença", "presence_index": "Índice"},
        {c: fmt_pct for c in ("reach", "examined", "revisit", "first_noticed", "presence")}
        | {"ttff_median": lambda v: fmt_number(v, 1), "rel_ttff_median": lambda v: fmt_number(v, 1),
           "presence_index": lambda v: fmt_number(v, 2)},
        key="jc_tab_cell",
    )

    st.subheader("Produtos (SKUs)")
    sku = metrics["sku"]
    sku_cells = _cells(sku, include_all=False)
    if sku_cells:
        sku_cell = st.selectbox("Loja", sku_cells, key="jc_ag_sku_cell",
                                index=sku_cells.index(cell) if cell in sku_cells else 0)
        rows = sku[sku["cell"] == sku_cell]
        st.plotly_chart(charts.emphasis_bars(rows, "product", "share_mean"), width="stretch")
        _table(
            rows,
            {"product": "Produto", "brand": "Marca", "share_mean": "Share", "reach": "Alcance",
             "dwell_mean_s": "Tempo médio (s)", "ttff_median": "TTFF mediana (s)",
             "visits_mean": "Visitas (média)"},
            {"share_mean": fmt_pct, "reach": fmt_pct, "dwell_mean_s": lambda v: fmt_number(v, 2),
             "ttff_median": lambda v: fmt_number(v, 1), "visits_mean": lambda v: fmt_number(v, 1)},
            key="jc_tab_sku",
        )

# ==================================================================
# Navegacao e decisao
# ==================================================================
elif section == "Navegação e decisão":
    dimensions = meta.get("dimensions") or {}
    attributes = metrics["attributes"]
    st.subheader("Atributos dos produtos")
    if not dimensions:
        st.info("Defina os atributos das AOIs (ex.: `tipo: Diurno, Noturno`) em Dados do Projeto → "
                "Catálogo de AOIs.")
    for dimension, values in dimensions.items():
        st.markdown("**{}**".format(dimension.capitalize()))
        st.caption("Fração da atenção entre os produtos em que o atributo aparece no nome da AOI. "
                   "Compare com a presença do valor na gôndola (na tabela): índice acima de 1 "
                   "é atenção além do espaço que o valor ocupa.")
        st.plotly_chart(charts.attribute_stacked(attributes, dimension, values), width="stretch")
    _table(
        attributes,
        {"cell": "Célula", "dimension": "Atributo", "value": "Valor", "n_defined": "n",
         "share_mean": "Share", "reach": "Alcance", "presence": "Presença na gôndola",
         "presence_index": "Índice"},
        {"share_mean": fmt_pct, "reach": fmt_pct, "presence": fmt_pct,
         "presence_index": lambda v: fmt_number(v, 2)},
        key="jc_tab_attr",
    )

    st.subheader("Etiquetas de preço")
    price = metrics["price"]
    if price.empty:
        st.caption("Nenhuma AOI de preço mapeada nesta seleção.")
    else:
        rows = price.assign(is_focus=price["brand"] == focus_brand, label=price["product"] + " · " + price["cell"])
        st.plotly_chart(charts.emphasis_bars(rows, "label", "reach"), width="stretch")
        _table(
            price,
            {"cell": "Célula", "product": "Produto", "reach": "Viu o preço", "dwell_mean_s": "Tempo médio (s)",
             "ttff_median": "TTFF mediana (s)", "price_fraction": "Preço ÷ (preço + produto)"},
            {"reach": fmt_pct, "dwell_mean_s": lambda v: fmt_number(v, 2),
             "ttff_median": lambda v: fmt_number(v, 1), "price_fraction": fmt_pct},
            key="jc_tab_price",
        )

    st.subheader("Tempo até a decisão")
    summary = metrics["recording_summary"]
    times = metrics.get("times")
    combos = (list(dict.fromkeys(zip(times["task"], times["source"])))
              if times is not None and not times.empty else [])
    combos = [combo for combo in combos if combo[0]]
    t1, t2 = st.columns([2, 1.4])
    combo = t1.selectbox(
        "Medida", combos,
        format_func=lambda item: "{} · {}".format(TASK_LABELS.get(item[0], item[0]),
                                                   TIME_SOURCE_LABELS.get(item[1], item[1])),
        key="jc_ag_decision_measure",
        help="O tempo de compra vem do registro de campo; o Tempo da planilha, da planilha enriquecida. "
             "Cada um aparece só na tarefa a que pertence.",
    ) if combos else None
    by = t2.radio("Agrupar por", ["Loja", "Perfil", "Canal"], horizontal=True, key="jc_ag_decision_by")
    column = {"Loja": "store_label", "Perfil": "profile", "Canal": "channel"}[by]
    if combo is not None:
        chosen = times[(times["task"] == combo[0]) & (times["source"] == combo[1])]
        st.plotly_chart(charts.decision_strip(chosen.rename(columns={"seconds": "tempo_decisao_s"}), column),
                        width="stretch")
    else:
        st.plotly_chart(charts.decision_strip(summary, column), width="stretch")
    decision = metrics["decision"]
    _table(
        decision,
        {"task_label": "Tarefa", "source_label": "Fonte", "group_type": "Agrupamento", "group": "Grupo",
         "n": "n", "median_s": "Mediana (s)", "q1_s": "1º quartil (s)", "q3_s": "3º quartil (s)",
         "min_s": "Mín (s)", "max_s": "Máx (s)"},
        {c: (lambda v: fmt_number(v, 1)) for c in ("median_s", "q1_s", "q3_s", "min_s", "max_s")},
        key="jc_tab_decision",
    )
    with st.expander("Tempo até a decisão × comportamento na gôndola"):
        view = summary.dropna(subset=["tempo_decisao_s"])
        st.dataframe(
            pd.DataFrame({
                "Participante": view["participant"],
                "Loja": view["store_label"],
                "Perfil": view["profile"],
                "Decisão (s)": view["tempo_decisao_s"].map(lambda v: fmt_number(v, 0)),
                "Marcas vistas": view["brands_looked"],
                "Visitas": view["visits_total"].map(lambda v: fmt_number(v, 0)),
                "Tempo na categoria (s)": view["category_dwell_s"].map(lambda v: fmt_number(v, 1)),
                "Share de {}".format(focus_brand or "marca foco"): view["focus_share"].map(fmt_pct),
            }),
            hide_index=True, width="stretch",
        )

# ==================================================================
# Embalagens
# ==================================================================
elif section == "Embalagens":
    packaging = metrics["packaging"]
    elements = packaging.get("elements", pd.DataFrame())
    if elements is None or elements.empty:
        st.info("Não há dados de embalagem (agregados por perfil) neste projeto.")
        st.stop()
    groups = list(dict.fromkeys(elements["profile"]))
    default = groups.index("Todos os perfis") if "Todos os perfis" in groups else 0
    group = st.selectbox("Perfil", groups, index=default, key="jc_ag_pack_group")
    rows = elements[elements["profile"] == group]
    coverage = packaging["coverage"]
    cov = coverage[coverage["profile"] == group]
    if not cov.empty and cov["aoi_coverage"].notna().any():
        st.caption(
            "n = {} · os elementos mapeados somam {} do tempo gravado; o resto ficou fora de "
            "qualquer elemento.".format(int(cov["n_group"].iloc[0]), fmt_pct(cov["aoi_coverage"].iloc[0]))
        )
    st.markdown("**Onde o olhar cai em cada embalagem**")
    st.caption("Fração do olhar de cada marca que cada elemento levou.")
    st.plotly_chart(charts.packaging_heatmap(rows), width="stretch")
    st.markdown("**Quantos viram cada elemento**")
    st.plotly_chart(charts.packaging_heatmap(rows, value="reach"), width="stretch")
    _table(
        rows,
        {"brand": "Marca", "element_label": "Elemento", "reach": "Alcance", "element_share": "Share na marca",
         "dwell_per_participant_s": "Tempo por participante (s)", "dwell_per_looker_s": "Tempo por quem olhou (s)",
         "ttff_mean_s": "TTFF médio (s)", "n_group": "n"},
        {"reach": fmt_pct, "element_share": fmt_pct, "dwell_per_participant_s": lambda v: fmt_number(v, 2),
         "dwell_per_looker_s": lambda v: fmt_number(v, 2), "ttff_mean_s": lambda v: fmt_number(v, 1)},
        key="jc_tab_pack",
    )
    brands_table = packaging.get("brands", pd.DataFrame())
    if brands_table is not None and not brands_table.empty:
        st.markdown("**Marca / logo visto, por perfil**")
        logos = brands_table.assign(label=brands_table["brand"] + " · " + brands_table["profile"],
                                    is_focus=brands_table["brand"] == focus_brand)
        st.plotly_chart(charts.emphasis_bars(logos, "label", "logo_reach"), width="stretch")

# ==================================================================
# Canal e perfil
# ==================================================================
elif section == "Canal e perfil":
    for confound in metrics["confounds"]:
        first, second = confound["labels"]
        st.warning(
            "{} e {} andam juntos nesta amostra ({}). Uma diferença entre canais pode ser da "
            "tarefa, e vice-versa: a comparação não isola o efeito.".format(first, second.lower(), confound["mapping"])
        )
    summary = metrics["recording_summary"]
    if focus_brand and not summary.empty:
        st.subheader("Share de {} por perfil".format(focus_brand))
        for task, rows in summary.groupby("task"):
            st.markdown("**{}**".format(TASK_LABELS.get(task, task)))
            st.plotly_chart(
                charts.group_dots(rows, "profile", "focus_share",
                                  value_title="share de {}".format(focus_brand)),
                width="stretch", key="jc_ag_profile_{}".format(task),
            )
    comparisons = metrics["comparisons"]
    st.subheader("Comparações")
    if comparisons.empty:
        st.caption("Sem grupos para comparar nesta seleção.")
    else:
        st.dataframe(
            pd.DataFrame({
                "Tarefa": comparisons["task"].map(lambda t: TASK_LABELS.get(t, t)),
                "Por": comparisons["by"],
                "Métrica": comparisons["metric"],
                "Grupo A": comparisons["group_a"] + " (n=" + comparisons["n_a"].astype(str) + ")",
                "Grupo B": comparisons["group_b"] + " (n=" + comparisons["n_b"].astype(str) + ")",
                "Média A": comparisons["mean_a"].map(fmt_pct),
                "Média B": comparisons["mean_b"].map(fmt_pct),
                "δ de Cliff": comparisons["cliffs_delta"].map(lambda v: fmt_number(v, 2)),
                "p": comparisons["p_value"].map(lambda v: fmt_number(v, 3)),
                "Método": comparisons["method"],
            }),
            hide_index=True, width="stretch",
        )
        st.caption("Com menos de 5 participantes por grupo a comparação é só descritiva; o δ de "
                   "Cliff vai de −1 a 1 (0 = grupos iguais).")

# ==================================================================
# Amostra e qualidade
# ==================================================================
elif section == "Amostra e qualidade":
    st.subheader("Amostra por célula")
    by_cell = pd.DataFrame(metrics["sample"]["by_cell"])
    if not by_cell.empty:
        st.dataframe(by_cell.rename(columns={"cell": "Célula", "n": "Participantes"})[["Célula", "Participantes"]],
                     hide_index=True, width="stretch")
    status_counts = recordings.groupby(["task_label", "status"]).size().unstack(fill_value=0) if not recordings.empty else pd.DataFrame()
    if not status_counts.empty:
        st.markdown("**Gravações por situação**")
        status_counts = status_counts.rename(columns=RECORDING_STATUS_LABELS)
        status_counts.index.name = "Tarefa"
        status_counts.columns.name = None
        st.dataframe(status_counts, width="stretch")
    issues = model["issues"]
    if issues:
        st.markdown("**Avisos dos dados**")
        for issue in issues:
            prefix = {"error": "✕", "warn": "!", "info": "·"}.get(issue["level"], "·")
            ref = " ({})".format(issue["ref"]) if issue.get("ref") else ""
            st.markdown("`{}` {}{}".format(prefix, issue["message"], ref))
    st.caption("Qualidade de cada gravação, com as checagens, em **Participantes**.")

# ==================================================================
# IA
# ==================================================================
elif section == "IA":
    analyses = jornada_db.list_analyses(project_id)
    # Em "Todas as organizações" a base resolvida seria a de quem está logado,
    # não a do projeto: a busca fica desligada.
    kb_store = get_vector_store_id() if auth.active_organization_id(user) else None
    st.subheader("Análise por IA")
    st.caption(
        "A IA recebe o contexto do projeto e as métricas do recorte — não os dados brutos — e "
        "responde às perguntas do estudo com esses números. As contas são do app; a leitura é da IA."
    )
    use_kb = False
    if pode_editar:
        with st.container(border=True):
            c1, c2, c3 = st.columns([1.4, 1.2, 1.6], vertical_alignment="bottom")
            mode_label = c1.segmented_control(
                "Modo",
                ["Rápida", "Aprofundada"],
                default="Rápida",
                key="jc_ai_mode",
                help="Rápida: uma chamada. Aprofundada: leitura estatística das tabelas e, depois, "
                     "interpretação estratégica (duas chamadas, mais demorada).",
            ) or "Rápida"
            ai_model = c2.selectbox("Modelo", AI_MODELS, key="jc_ai_model")
            use_kb = c3.toggle(
                "Usar a base de conhecimento",
                value=bool(kb_store),
                disabled=not kb_store,
                key="jc_ai_kb",
                help="Busca na literatura da organização e no material deste projeto.",
            ) and bool(kb_store)
            if not auth.active_organization_id(user):
                st.caption("Em “Todas as organizações” a base fica desligada: a busca iria à base da sua "
                           "organização, não à do projeto.")
            elif not kb_store:
                st.caption("A base de conhecimento da Jornada não está configurada nesta organização.")
            st.caption("Recorte enviado: {}".format(filters_text(filters, model)))
            if st.button("Gerar análise", type="primary", key="jc_ai_generate"):
                if get_openai_client() is None:
                    st.error("A OpenAI não está configurada: defina OPENAI_API_KEY no .env e reinicie o app.")
                else:
                    mode = "rapida" if mode_label == "Rápida" else "aprofundada"
                    try:
                        with st.spinner("Gerando a análise {}…".format(mode_label.lower())):
                            result = generate_analysis(
                                project, model, metrics,
                                mode=mode,
                                ai_model=ai_model,
                                recorte=filters_text(filters, model),
                                quality=run_quality(model, project),
                                interviews=jornada_db.list_interviews(project_id),
                                vector_store_id=kb_store if use_kb else None,
                            )
                        if not result["text"].strip():
                            st.error("A IA não devolveu texto. Tente de novo.")
                        else:
                            new_id = jornada_db.save_analysis(
                                project_id,
                                model=ai_model,
                                mode=mode,
                                analysis_text=result["text"],
                                citations=result["citations"],
                                search=result["search"],
                                filters=filters,
                                data_version=project.get("data_version"),
                            )
                            # O seletor ainda não existe nesta execução: dá para apontar a nova.
                            st.session_state["jc_ai_pick"] = new_id
                            st.rerun()
                    except Exception as error:
                        st.error("Não foi possível gerar a análise: {}".format(error))

    if not analyses:
        st.info("Nenhuma análise gerada ainda."
                + (" Escolha o modo e clique em **Gerar análise**." if pode_editar else ""))
    else:
        analyses_by_id = {a["id"]: a for a in analyses}
        if st.session_state.get("jc_ai_pick") not in analyses_by_id:
            st.session_state["jc_ai_pick"] = analyses[0]["id"]
        chosen_id = st.selectbox(
            "Análise",
            list(analyses_by_id),
            format_func=lambda aid: _analysis_label(analyses_by_id[aid]),
            key="jc_ai_pick",
        )
        analysis = analyses_by_id[chosen_id]
        analysis_recorte = filters_text(analysis.get("filters"), model)
        if analysis.get("data_version") is not None and analysis["data_version"] != project.get("data_version"):
            st.warning(
                "Os dados do projeto mudaram depois desta análise (arquivos, exclusões ou ajustes): os "
                "números dela podem não bater com as seções atuais. {}".format(
                    "Gere uma nova para atualizar." if pode_editar
                    else "Peça uma análise nova a quem edita o projeto.")
            )
        st.caption("Recorte da análise: {}".format(analysis_recorte))
        with st.container(border=True):
            st.markdown(analysis.get("analysis_text") or "")
        with st.expander("Referências da base de conhecimento"):
            ui.knowledge_base_references({"citations": analysis.get("citations"), "search": analysis.get("search")})

        if pode_editar:
            a1, a2, _ = st.columns([1.4, 1.1, 2.5])
            if analysis.get("kb_file_id"):
                a1.caption("Esta análise está na base de conhecimento.")
            elif a1.button(
                "Enviar para a base",
                key="jc_ai_to_kb",
                disabled=not kb_store,
                width="stretch",
                help="Guarda esta análise na base da organização, marcada como análise deste projeto.",
            ):
                try:
                    file_id = send_analysis_to_kb(project, analysis, analysis_recorte, kb_store)
                    jornada_db.set_analysis_kb_file(project_id, chosen_id, file_id)
                    st.toast("Análise enviada para a base de conhecimento.")
                    st.rerun()
                except Exception as error:
                    st.error("Não foi possível enviar para a base: {}".format(error))
            if a2.button("Excluir", key="jc_ai_delete", width="stretch"):
                st.session_state["jc_ai_confirm"] = chosen_id
                st.rerun()
            if st.session_state.get("jc_ai_confirm") == chosen_id:
                st.warning("Excluir a análise de {}? {}Não dá para desfazer.".format(
                    str(analysis.get("created_at") or "")[:16],
                    "A cópia na base de conhecimento também sai. " if analysis.get("kb_file_id") else "",
                ))
                y, n, _ = st.columns([1.4, 1.1, 2.5])
                if y.button("Confirmar exclusão", type="primary", key="jc_ai_delete_yes", width="stretch"):
                    jornada_db.delete_analyses(project_id, [chosen_id])
                    for state_key in ("jc_ai_confirm", "jc_ai_pick", "jc_ai_chat_{}".format(chosen_id)):
                        st.session_state.pop(state_key, None)
                    st.rerun()
                if n.button("Cancelar", key="jc_ai_delete_no", width="stretch"):
                    st.session_state.pop("jc_ai_confirm", None)
                    st.rerun()

            st.divider()
            st.markdown("**Perguntas sobre esta análise**")
            st.caption("A conversa usa o relatório e os achados do recorte da análise e fica só nesta sessão.")
            chat_key = "jc_ai_chat_{}".format(chosen_id)
            history = st.session_state.setdefault(chat_key, [])
            for message in history:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
            if history and st.button("Limpar conversa", key="jc_ai_chat_clear"):
                st.session_state[chat_key] = []
                st.rerun()
            question = st.chat_input("Pergunte sobre esta análise…", key="jc_ai_chat_input")
            if question:
                if get_openai_client() is None:
                    st.error("A OpenAI não está configurada: defina OPENAI_API_KEY no .env e reinicie o app.")
                else:
                    history.append({"role": "user", "content": question})
                    try:
                        with st.spinner("Pensando…"):
                            answer = chat_answer(
                                analysis.get("analysis_text") or "",
                                get_project_metrics(project, analysis.get("filters") or {}),
                                history,
                                ai_model=analysis.get("model") or AI_MODELS[0],
                                project_id=project_id,
                                vector_store_id=kb_store if use_kb else None,
                            )
                        history.append({"role": "assistant", "content": answer})
                        st.rerun()
                    except Exception as error:
                        history.pop()
                        st.error("Não foi possível responder: {}".format(error))

# ==================================================================
# Exportar
# ==================================================================
else:
    st.subheader("Exportar")
    st.markdown("**Recorte:** {}".format(filters_text(filters, model)))
    st.caption(
        "As métricas seguem o recorte acima. O PDF é o relatório para leitura; o PPTX traz os "
        "mesmos números em gráficos editáveis; o Excel leva também as tabelas completas de "
        "gravações, olhar e catálogo, com as chaves para relacionar no Power BI e um dicionário "
        "das colunas."
    )
    analyses = jornada_db.list_analyses(project_id)
    chosen_analysis = None
    if analyses:
        analyses_by_id = {a["id"]: a for a in analyses}
        chosen_id = st.selectbox(
            "Análise de IA no relatório",
            [None] + list(analyses_by_id),
            index=1,
            format_func=lambda aid: "Nenhuma" if aid is None else _analysis_label(analyses_by_id[aid]),
            key="jc_export_analysis",
            help="Entra no fim do PDF e da apresentação. O Excel leva todas as análises salvas.",
        )
        chosen_analysis = analyses_by_id.get(chosen_id)
    # Arquivos valem para um recorte, uma versao dos dados e as analises da vez.
    signature = (
        project_id,
        project.get("data_version"),
        json.dumps(filters, sort_keys=True, ensure_ascii=False),
        tuple(a["id"] for a in analyses),
        chosen_analysis["id"] if chosen_analysis else None,
    )
    prepared = st.session_state.get("jc_exports")
    if not prepared or prepared.get("signature") != signature:
        if st.button("Preparar arquivos", type="primary", key="jc_export_prepare"):
            with st.spinner("Montando os arquivos…"):
                quality = run_quality(model, project)
                media = jornada_db.list_media(project_id)
                pdf, pdf_name = build_pdf(project, model, metrics, analysis=chosen_analysis, quality=quality)
                pptx, pptx_name = build_pptx(project, model, metrics, analysis=chosen_analysis, quality=quality)
                excel, excel_name = build_excel(
                    project, model, metrics, quality=quality, analyses=analyses, media=media,
                )
            st.session_state["jc_exports"] = {
                "signature": signature,
                "files": [
                    {
                        "kind": "pdf",
                        "label": "Relatório PDF",
                        "data": pdf,
                        "name": pdf_name,
                        "mime": "application/pdf",
                    },
                    {
                        "kind": "pptx",
                        "label": "Apresentação PPTX",
                        "data": pptx,
                        "name": pptx_name,
                        "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    },
                    {
                        "kind": "excel",
                        "label": "Excel / Power BI",
                        "data": excel,
                        "name": excel_name,
                        "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    },
                ],
            }
            st.rerun()
    else:
        columns = st.columns(3)
        for column, item in zip(columns, prepared["files"]):
            column.download_button(
                item["label"],
                data=item["data"],
                file_name=item["name"],
                mime=item["mime"],
                width="stretch",
                key="jc_export_{}".format(item["kind"]),
                on_click=jornada_db.audit_export,
                args=(project_id, item["kind"]),
            )
        st.caption("Prontos para este recorte e estes dados; mudar qualquer um deles pede nova preparação.")
