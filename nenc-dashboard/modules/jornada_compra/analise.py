"""
Jornada de Compra — Análise.

Visualização de dados de eye-tracking: tabelas resumo, gráficos por AOI,
heatmap de atenção, ANOVA e análise de IA com contexto e histórico salvos no banco.
"""

import streamlit as st
from utils import auth

auth.require_module("jornada_compra")

import pandas as pd
from io import BytesIO
from openai import OpenAI
from fpdf import FPDF

from utils import jornada_db
from utils.jornada_loader import (
    get_jornada_participants,
    get_jornada_aois,
    get_jornada_marcas,
    KEY_METRICS,
)
from utils.jornada_charts import (
    create_metric_by_aoi,
    create_attention_heatmap,
    create_brand_share_chart,
    format_anova_for_display,
)
from utils.ai_provider import (
    get_openai_client,
    get_vector_store_id,
    create_analysis,
)
from utils.neuro_prompts import (
    NEURO_SYSTEM_PROMPT,
    NEURO_SYSTEM_PROMPT_STATISTICAL,
    NEURO_SYSTEM_PROMPT_STRATEGIC,
    build_user_prompt,
)

jornada_db.init_db()

st.title("🔍 Análise — Jornada de Compra")

project_id = st.session_state.get("jc_project_id")

if not project_id:
    st.warning("⚠️ Nenhum projeto está selecionado.")
    if st.button("🎙️ Ir para Projetos", type="primary"):
        st.switch_page("modules/jornada_compra/projetos.py")
    st.stop()

project = jornada_db.get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    st.stop()

st.caption(f"Projeto Ativo: **{project['name']}** | Categoria: **{project.get('categoria') or 'Geral'}**")

# Carregar dados do projeto
data = st.session_state.get("jc_data")
if not data:
    data = jornada_db.get_dataset(project_id)
    st.session_state["jc_data"] = data

loaded_keys = [k for k in data if k != "_errors" and isinstance(data[k], pd.DataFrame) and not data[k].empty]

if not loaded_keys:
    st.warning("⚠️ Nenhum dado de eye-tracking disponível para este projeto. Volte à página de Dados do Projeto e envie as planilhas.")
    if st.button("⬅️ Ir para Dados do Projeto", type="primary"):
        st.switch_page("modules/jornada_compra/preparacao.py")
    st.stop()

# Helper para prompt
def _format_table(df: pd.DataFrame, title: str) -> str:
    if df.empty:
        return ""
    return f"### {title}\n{df.to_string()}"

def _sanitize(text: str) -> str:
    return text.encode("latin-1", errors="replace").decode("latin-1")

def _build_pdf(projeto: dict, tables_text: str, ai_text: str, entrevistas_summary: str = "") -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "NENC Insights - Jornada de Compra", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    if projeto.get("name"):
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, _sanitize(f"Projeto: {projeto['name']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    for label, key in [
        ("Categoria", "categoria"),
        ("Historico", "historico"),
        ("Problemas centrais", "problemas"),
    ]:
        value = projeto.get(key, "")
        if value:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 7, f"{label}:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, _sanitize(value))
            pdf.ln(2)

    if entrevistas_summary:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Resumo das Entrevistas", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize(entrevistas_summary))
        pdf.ln(4)

    if tables_text.strip():
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Dados", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 8)
        for line in tables_text.splitlines():
            pdf.cell(0, 4, _sanitize(line[:120]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    if ai_text:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Analise de IA", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize(ai_text))

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()

# DataFrame principal
main_df = pd.DataFrame()
for key in ("tabelas", "consolidado", "medias", "por_marca"):
    if key in data and not data[key].empty:
        main_df = data[key]
        break

# ------------------------------------------------------------------
# Sidebar — Controles
# ------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Controles")

    available_metrics = [m for m in KEY_METRICS if not main_df.empty and m in main_df.columns]
    selected_metric = st.selectbox("Métrica principal", available_metrics, index=0, key="jc_metric") if available_metrics else None

    st.divider()

    all_participants = get_jornada_participants(data)
    selected_participants = st.multiselect("Participantes", options=all_participants, default=all_participants, key="jc_participants") if all_participants else None

    all_aois = get_jornada_aois(data)
    selected_aois = st.multiselect("AOIs", options=all_aois, default=all_aois, key="jc_aois") if all_aois else None

    all_marcas = get_jornada_marcas(data)
    selected_marcas = st.multiselect("Marcas", options=all_marcas, default=all_marcas, key="jc_marcas") if all_marcas else None

    st.divider()

    st.subheader("🤖 Configurações da IA")
    _client = get_openai_client()
    if _client:
        st.success("OpenAI Conectado ✅")
    else:
        st.error("Configure OPENAI_API_KEY no .env ❌")

    ai_model = st.selectbox("Modelo", ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"], index=0, key="jc_ai_model")
    ai_mode = st.radio("Modo de análise", ["Rápida (1 chamada)", "Aprofundada (2 etapas)"], index=0, key="jc_ai_mode")

    vs_id = project.get("vector_store_id")
    use_kb = False
    if vs_id:
        use_kb = st.toggle("📚 Consultar Base do Projeto", value=True, key="jc_use_kb")

# Aplicar Filtros
filtered_df = main_df.copy()
if selected_participants and "Participante" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Participante"].isin(selected_participants)]
if selected_aois and "AOI" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["AOI"].isin(selected_aois)]
if selected_marcas and "Marca" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Marca"].isin(selected_marcas)]

# Tabs Principais: Visualizar Gráficos vs Análise Inteligente vs Histórico
tab_charts, tab_ai, tab_history = st.tabs(["📊 Visualização dos Dados", "🤖 Análise com IA", "📜 Histórico Salvo no Banco"])

with tab_charts:
    st.subheader("📋 Tabela Resumo")
    if not filtered_df.empty and selected_metric:
        metric_cols = [m for m in KEY_METRICS if m in filtered_df.columns and pd.api.types.is_numeric_dtype(filtered_df[m])]
        if "Participante" in filtered_df.columns and metric_cols:
            summary = filtered_df.groupby("AOI")[metric_cols].agg(["mean", "std"]).round(4)
            st.dataframe(summary, use_container_width=True)
        elif metric_cols:
            summary = filtered_df[["AOI"] + metric_cols].copy().set_index("AOI")
            st.dataframe(summary.round(4), use_container_width=True)

    if selected_metric and not filtered_df.empty:
        st.divider()
        st.subheader("📊 Métricas por AOI")
        fig_bar = create_metric_by_aoi(filtered_df, metric=selected_metric, aois=selected_aois)
        st.plotly_chart(fig_bar, use_container_width=True)

    if selected_metric and not filtered_df.empty and "Participante" in filtered_df.columns:
        st.divider()
        st.subheader("🗺️ Heatmap de Atenção")
        fig_heat = create_attention_heatmap(filtered_df, metric=selected_metric, participants=selected_participants, aois=selected_aois)
        st.plotly_chart(fig_heat, use_container_width=True)

    if "visual_share" in data and not data["visual_share"].empty:
        st.divider()
        st.subheader("👁️ Share Visual por Marca")
        vs_df = data["visual_share"]
        if selected_marcas and "Marca" in vs_df.columns:
            vs_df = vs_df[vs_df["Marca"].isin(selected_marcas)]
        fig_share = create_brand_share_chart(vs_df)
        st.plotly_chart(fig_share, use_container_width=True)

with tab_ai:
    st.subheader("🤖 Gerador de Análise Inteligente")
    
    # Preparar texto dos dados
    tables_text = ""
    if not filtered_df.empty:
        metric_cols = [m for m in KEY_METRICS if m in filtered_df.columns and pd.api.types.is_numeric_dtype(filtered_df[m])]
        if metric_cols:
            ai_summary = filtered_df.groupby("AOI")[metric_cols].agg(["mean", "std"]).round(4) if "Participante" in filtered_df.columns else filtered_df[["AOI"] + metric_cols].set_index("AOI").round(4)
            tables_text += _format_table(ai_summary, "Métricas de Eye-Tracking por AOI")
    if "visual_share" in data and not data["visual_share"].empty:
        tables_text += "\n\n" + _format_table(data["visual_share"], "Share Visual por Marca")

    if not _client:
        st.info("Configure a variável OPENAI_API_KEY no arquivo .env para gerar diagnósticos automatizados.")
    else:
        if st.button("🔍 Gerar e Salvar Nova Análise de IA", type="primary", key="btn_run_jc_ai"):
            with st.spinner("Analisando dados do estudo e salvando relatório no banco..."):
                try:
                    user_prompt = build_user_prompt(
                        tables_text=tables_text,
                        project_context=project,
                        pptx_text=project.get("briefing_text", ""),
                        entrevistas_summary="",
                    )
                    active_vs = vs_id if use_kb else None

                    if ai_mode == "Aprofundada (2 etapas)":
                        stat_res = create_analysis(
                            system_prompt=NEURO_SYSTEM_PROMPT_STATISTICAL,
                            user_prompt=user_prompt,
                            model=ai_model,
                            vector_store_id=active_vs,
                            temperature=0.3,
                            max_tokens=3000,
                        )
                        strat_prompt = f"## Análise Estatística Prévia\n{stat_res['text']}\n\n## Dados Originais\n{user_prompt}"
                        strat_res = create_analysis(
                            system_prompt=NEURO_SYSTEM_PROMPT_STRATEGIC,
                            user_prompt=strat_prompt,
                            model=ai_model,
                            vector_store_id=active_vs,
                            temperature=0.5,
                            max_tokens=4000,
                        )
                        full_text = f"## Análise Estatística\n\n{stat_res['text']}\n\n## Interpretação Estratégica\n\n{strat_res['text']}"
                        citations = [c.get("filename", "") for c in stat_res["citations"] + strat_res["citations"]]
                    else:
                        res = create_analysis(
                            system_prompt=NEURO_SYSTEM_PROMPT,
                            user_prompt=user_prompt,
                            model=ai_model,
                            vector_store_id=active_vs,
                            temperature=0.5,
                            max_tokens=4000,
                        )
                        full_text = res["text"]
                        citations = [c.get("filename", "") for c in res["citations"]]

                    # Salvar no Banco SQLite!
                    jornada_db.save_analysis(project_id, analysis_text=full_text, model=ai_model, citations=citations)
                    st.session_state["jc_last_ai_text"] = full_text
                    st.success("Análise gerada e salva no banco de dados com sucesso!")
                    st.markdown(full_text)
                except Exception as e:
                    st.error(f"Erro ao gerar análise: {e}")

        # Mostrar última gerada se houver
        if st.session_state.get("jc_last_ai_text"):
            st.divider()
            st.markdown("### 📄 Análise Gerada Recentemente")
            st.markdown(st.session_state["jc_last_ai_text"])

with tab_history:
    st.subheader("📜 Histórico de Análises Salvas no Banco")
    history = jornada_db.get_analyses(project_id)
    if not history:
        st.info("Nenhuma análise salva para este projeto ainda.")
    else:
        for idx, item in enumerate(history):
            with st.expander(f"🧠 Análise de {item['created_at']} (Modelo: {item['model']})"):
                st.markdown(item["analysis_text"])
                if item.get("citations"):
                    st.caption(f"📎 Fontes: {', '.join(item['citations'])}")

# Navegação
st.divider()
col_nav1, col_nav2 = st.columns(2)
with col_nav1:
    if st.button("⬅️ Voltar para Dados do Projeto", use_container_width=True):
        st.switch_page("modules/jornada_compra/preparacao.py")
with col_nav2:
    pdf_bytes = _build_pdf(project, tables_text, st.session_state.get("jc_last_ai_text", ""), "")
    st.download_button(
        "📄 Exportar PDF da Análise",
        data=pdf_bytes,
        file_name=f"{project['name']}_analise.pdf",
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )
