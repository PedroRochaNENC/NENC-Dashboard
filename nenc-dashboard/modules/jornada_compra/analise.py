"""
Jornada de Compra — Análise.

Visualização de dados de eye-tracking: tabelas resumo, gráficos por AOI,
heatmap de atenção, ANOVA e análise de IA com contexto do projeto.
"""

import streamlit as st
import pandas as pd
from io import BytesIO
from groq import Groq
from fpdf import FPDF

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


def _format_table(df: pd.DataFrame, title: str) -> str:
    """Converte DataFrame em texto para o prompt de IA."""
    if df.empty:
        return ""
    return f"### {title}\n{df.to_string()}"


def _summarize_interviews(client, entrevistas_df: pd.DataFrame) -> str:
    """Resume as entrevistas usando a API Groq."""
    # Preparar texto das entrevistas (limitar tamanho para não exceder contexto)
    interview_texts = []
    for _, row in entrevistas_df.iterrows():
        id_col = next(
            (c for c in entrevistas_df.columns if c.lower() in ("identificacao", "identificação")),
            entrevistas_df.columns[0],
        )
        interview_texts.append(
            f"**{row.get(id_col, 'Entrevista')}:**\n{str(row.get('texto', ''))[:1500]}"
        )

    all_interviews = "\n\n---\n\n".join(interview_texts[:5])  # Máximo 5 entrevistas

    system_prompt = (
        "Você é um especialista em análise qualitativa. "
        "Resuma as entrevistas de forma concisa em 3-4 parágrafos."
    )

    user_prompt = f"""Resuma estas entrevistas de consumidores em ponto de venda (máximo 3-4 parágrafos):

{all_interviews}

Extraia: padrões comportamentais, fatores de decisão de compra, percepções sobre marcas, e elementos visuais que influenciam escolhas."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=1000,
    )
    return response.choices[0].message.content


def _sanitize(text: str) -> str:
    """Remove caracteres fora do latin-1 para compatibilidade com fontes PDF."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _build_pdf(projeto: dict, tables_text: str, ai_text: str, entrevistas_summary: str = "") -> bytes:
    """Gera um PDF com resumo do projeto e dados da análise."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Título
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "NENC Insights - Jornada de Compra", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Contexto do projeto
    if projeto.get("nome"):
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, _sanitize(f"Projeto: {projeto['nome']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    for label, key in [
        ("Especialidade", "especialidade"),
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

    # Resumo das Entrevistas
    if entrevistas_summary:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Resumo das Entrevistas", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize(entrevistas_summary))
        pdf.ln(4)

    # Dados
    if tables_text.strip():
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Dados", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 8)
        for line in tables_text.splitlines():
            pdf.cell(0, 4, _sanitize(line[:120]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # Análise de IA
    if ai_text:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Analise de IA", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize(ai_text))

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


st.title("🔍 Análise — Jornada de Compra")

data = st.session_state.get("jc_data", {})
loaded_keys = [k for k in data if k != "_errors"]

if not loaded_keys:
    st.warning(
        "⚠️ Nenhum dado carregado. "
        "Volte à página de Preparação de Dados e carregue os arquivos."
    )
    st.stop()

# ------------------------------------------------------------------
# Determinar DataFrame principal para análise
# ------------------------------------------------------------------
# Prioridade: tabelas > consolidado > medias > por_marca
main_df = pd.DataFrame()
main_source = ""
for key in ("tabelas", "consolidado", "medias", "por_marca"):
    if key in data and not data[key].empty:
        main_df = data[key]
        main_source = key
        break

# ------------------------------------------------------------------
# Sidebar — Filtros
# ------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Controles")

    # Métrica selecionada
    available_metrics = [
        m for m in KEY_METRICS
        if not main_df.empty and m in main_df.columns
    ]
    if available_metrics:
        selected_metric = st.selectbox(
            "Métrica principal",
            available_metrics,
            index=0,
            key="jc_metric",
        )
    else:
        selected_metric = None

    st.divider()

    # Filtro de participantes
    all_participants = get_jornada_participants(data)
    selected_participants = None
    if all_participants:
        selected_participants = st.multiselect(
            "Participantes",
            options=all_participants,
            default=all_participants,
            key="jc_participants",
        )

    # Filtro de AOIs
    all_aois = get_jornada_aois(data)
    selected_aois = None
    if all_aois:
        selected_aois = st.multiselect(
            "AOIs",
            options=all_aois,
            default=all_aois,
            key="jc_aois",
        )

    # Filtro de marcas
    all_marcas = get_jornada_marcas(data)
    selected_marcas = None
    if all_marcas:
        selected_marcas = st.multiselect(
            "Marcas",
            options=all_marcas,
            default=all_marcas,
            key="jc_marcas",
        )

    st.divider()

    st.subheader("🤖 Análise de IA")
    api_key = st.text_input(
        "Chave da API Groq",
        type="password",
        key="jc_groq_key",
        help="Obtenha gratuitamente em console.groq.com/keys",
    )

# ------------------------------------------------------------------
# Aplicar filtros ao DataFrame principal
# ------------------------------------------------------------------
filtered_df = main_df.copy()

if selected_participants and "Participante" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Participante"].isin(selected_participants)]

if selected_aois and "AOI" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["AOI"].isin(selected_aois)]

if selected_marcas and "Marca" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Marca"].isin(selected_marcas)]

# ==================================================================
# Seção 1: Visualizar Dados (fechada por padrão)
# ==================================================================
with st.expander("📊 Visualizar Dados", expanded=False):
    # ------------------------------------------------------------------
    # Tabela resumo
    # ------------------------------------------------------------------
    st.subheader("📋 Tabela Resumo")

    if not filtered_df.empty and selected_metric:
        metric_cols = [
            m for m in KEY_METRICS
            if m in filtered_df.columns and pd.api.types.is_numeric_dtype(filtered_df[m])
        ]

        if "Participante" in filtered_df.columns and metric_cols:
            summary = (
                filtered_df
                .groupby("AOI")[metric_cols]
                .agg(["mean", "std"])
                .round(4)
            )
            st.dataframe(summary, width="stretch")
        elif metric_cols:
            summary = filtered_df[["AOI"] + metric_cols].copy()
            summary = summary.set_index("AOI") if "AOI" in summary.columns else summary
            st.dataframe(summary.round(4), width="stretch")
    else:
        st.info("Selecione uma métrica na barra lateral.")

    # ------------------------------------------------------------------
    # Gráfico de barras por AOI
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📊 Métricas por AOI")

    if selected_metric and not filtered_df.empty:
        fig_bar = create_metric_by_aoi(
            filtered_df,
            metric=selected_metric,
            aois=selected_aois,
        )
        st.plotly_chart(fig_bar, width="stretch")

    # ------------------------------------------------------------------
    # Heatmap participante × AOI
    # ------------------------------------------------------------------
    if (
        selected_metric
        and not filtered_df.empty
        and "Participante" in filtered_df.columns
    ):
        st.divider()
        st.subheader("🗺️ Heatmap de Atenção")

        fig_heat = create_attention_heatmap(
            filtered_df,
            metric=selected_metric,
            participants=selected_participants,
            aois=selected_aois,
        )
        st.plotly_chart(fig_heat, width="stretch")

    # ------------------------------------------------------------------
    # Visual Share por marca
    # ------------------------------------------------------------------
    if "visual_share" in data:
        st.divider()
        st.subheader("👁️ Share Visual por Marca")

        vs_df = data["visual_share"]
        if selected_marcas and "Marca" in vs_df.columns:
            vs_df = vs_df[vs_df["Marca"].isin(selected_marcas)]

        fig_share = create_brand_share_chart(vs_df)
        st.plotly_chart(fig_share, width="stretch")

    # ------------------------------------------------------------------
    # ANOVA
    # ------------------------------------------------------------------
    if "anova" in data:
        st.divider()
        st.subheader("📐 Resultados ANOVA")

        anova_display = format_anova_for_display(data["anova"])
        if not anova_display.empty:
            st.dataframe(anova_display, width="stretch")
        else:
            st.info("Dados ANOVA não puderam ser formatados.")

# ==================================================================
# Seção 2: Análise de IA
# ==================================================================
st.divider()
st.subheader("🤖 Análise de IA")

projeto = st.session_state.get("jc_projeto", {})

# Mostrar contexto do projeto se preenchido
if projeto.get("nome"):
    with st.expander("📝 Contexto do projeto", expanded=False):
        st.markdown(f"**Projeto:** {projeto['nome']}")
        if projeto.get("especialidade"):
            st.markdown(f"**Área:** {projeto['especialidade'][:200]}...")
        if projeto.get("problemas"):
            st.markdown(f"**Problemas centrais:** {projeto['problemas'][:200]}...")

pptx_text = st.session_state.get("jc_pptx_text", "")
if pptx_text:
    with st.expander("📄 Relatório PPTX carregado", expanded=False):
        st.text(pptx_text[:3000] + ("\n..." if len(pptx_text) > 3000 else ""))

# Resumo das entrevistas
entrevistas_summary = st.session_state.get("jc_entrevistas_summary", "")
if "entrevistas" in data and not data["entrevistas"].empty:
    with st.expander("🎙️ Entrevistas carregadas", expanded=False):
        st.info(f"{len(data['entrevistas'])} entrevistas disponíveis para análise qualitativa.")
        if entrevistas_summary:
            st.markdown("**Resumo gerado:**")
            st.markdown(entrevistas_summary)
        elif api_key:
            if st.button("📝 Gerar Resumo das Entrevistas", key="btn_summarize"):
                with st.spinner("Resumindo entrevistas..."):
                    try:
                        client = Groq(api_key=api_key)
                        summary = _summarize_interviews(client, data["entrevistas"])
                        st.session_state["jc_entrevistas_summary"] = summary
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao resumir entrevistas: {e}")
        else:
            st.warning("Insira a chave da API Groq para gerar o resumo das entrevistas.")

# Montar dados para o prompt
tables_text = ""

# Resumo das métricas por AOI
if not filtered_df.empty:
    metric_cols = [
        m for m in KEY_METRICS
        if m in filtered_df.columns and pd.api.types.is_numeric_dtype(filtered_df[m])
    ]
    if metric_cols:
        if "Participante" in filtered_df.columns:
            ai_summary = (
                filtered_df
                .groupby("AOI")[metric_cols]
                .agg(["mean", "std"])
                .round(4)
            )
        else:
            ai_summary = filtered_df[["AOI"] + metric_cols].set_index("AOI").round(4)
        tables_text += _format_table(ai_summary, "Métricas de Eye-Tracking por AOI")

# Visual Share
if "visual_share" in data:
    tables_text += "\n\n" + _format_table(
        data["visual_share"], "Share Visual por Marca"
    )

# ANOVA
if "anova" in data:
    anova_display = format_anova_for_display(data["anova"])
    if not anova_display.empty:
        tables_text += "\n\n" + _format_table(
            anova_display, "Resultados ANOVA (Entre Grupos)"
        )

# Por Marca
if "por_marca" in data:
    pm = data["por_marca"]
    pm_cols = ["AOI", "Marca", "Interação", "TotalGazeDuration", "FixationCount"]
    pm_available = [c for c in pm_cols if c in pm.columns]
    if pm_available:
        tables_text += "\n\n" + _format_table(
            pm[pm_available].head(15), "Dados por Marca (amostra)"
        )

if not api_key:
    st.info(
        "Insira sua chave da API Groq na barra lateral "
        "para habilitar a análise automática. "
        "Obtenha gratuitamente em **console.groq.com/keys**."
    )
elif not tables_text.strip():
    st.warning("Nenhum dado disponível para análise.")
else:
    if st.button("🔍 Gerar Análise", key="btn_ai_jc"):
        # Verificar se há entrevistas para análise comparativa
        entrevistas_summary = st.session_state.get("jc_entrevistas_summary", "")
        
        # Construir system prompt com contexto do projeto
        system_parts = [
            "Você é um especialista em análise de dados de eye-tracking "
            "e comportamento do consumidor."
        ]

        if projeto.get("especialidade"):
            system_parts.append(
                f"Contexto de especialidade: {projeto['especialidade']}"
            )

        if entrevistas_summary:
            system_parts.append(
                "Você possui expertise em triangulação de dados quantitativos (eye-tracking) "
                "e qualitativos (entrevistas). Lembre-se que há frequentemente diferenças entre "
                "o que os consumidores DIZEM que fazem e o que REALMENTE fazem. Os dados de "
                "eye-tracking revelam comportamentos inconscientes que os consumidores nem "
                "sempre conseguem verbalizar."
            )

        system_parts.append(
            "Analise os dados abaixo e forneça insights acionáveis. "
            "Responda em português do Brasil de forma clara e estruturada."
        )

        system_prompt = " ".join(system_parts)

        # Construir user prompt
        user_parts = []

        if projeto.get("nome"):
            user_parts.append(f"**Projeto:** {projeto['nome']}")

        if projeto.get("historico"):
            user_parts.append(
                f"**Histórico do problema:** {projeto['historico']}"
            )

        if projeto.get("problemas"):
            user_parts.append(
                f"**Problemas centrais a responder:** {projeto['problemas']}"
            )

        if pptx_text:
            user_parts.append(
                "**Relatório de referência (PPTX):**\n"
                f"{pptx_text[:3000]}"
            )

        # Adicionar resumo das entrevistas ao prompt
        if entrevistas_summary:
            user_parts.append(
                "**Resumo das Entrevistas Qualitativas:**\n"
                f"{entrevistas_summary[:3000]}"
            )

        user_parts.append(
            "Abaixo estão os dados de um estudo de eye-tracking "
            "em jornada de compra:\n\n"
            f"{tables_text}"
        )

        if entrevistas_summary:
            user_parts.append(
                "\nForneça:\n"
                "1. Resumo dos resultados de atenção visual\n"
                "2. Comparação entre marcas/AOIs\n"
                "3. Insights sobre fixação e padrão visual\n"
                "4. Significância estatística (se ANOVA disponível)\n"
                "5. Recomendações práticas\n"
                "6. Análise comparativa: Convergências, divergências entre entrevistas e eye-tracking. Destaque comportamentos inconscientes."
            )
        else:
            user_parts.append(
                "\nForneça:\n"
                "1. Resumo dos resultados de atenção visual\n"
                "2. Comparação entre marcas/AOIs\n"
                "3. Insights sobre fixação e padrão visual\n"
                "4. Significância estatística (se ANOVA disponível)\n"
                "5. Recomendações práticas"
            )

        if projeto.get("problemas"):
            next_item = 7 if entrevistas_summary else 6
            user_parts.append(
                f"\n{next_item}. Responda especificamente aos problemas centrais: "
                f"{projeto['problemas']}"
            )

        user_prompt = "\n\n".join(user_parts)

        print("=" * 60)
        print("SYSTEM PROMPT:")
        print("=" * 60)
        print(system_prompt)
        print("=" * 60)
        print("USER PROMPT:")
        print("=" * 60)
        print(user_prompt)
        print("=" * 60)

        with st.spinner("Gerando análise..."):
            try:
                client = Groq(api_key=api_key)
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.5,
                    max_tokens=2000,
                )
                st.markdown(response.choices[0].message.content)
                st.session_state["jc_ai_result"] = response.choices[0].message.content
            except Exception as e:
                st.error(f"Erro ao chamar a API: {e}")

# ------------------------------------------------------------------
# Navegação por etapas + Exportar PDF
# ------------------------------------------------------------------
st.divider()
col_nav1, col_nav2 = st.columns(2)

with col_nav1:
    if st.button("⬅️ Voltar para Preparação", width="stretch"):
        st.switch_page("modules/jornada_compra/preparacao.py")

with col_nav2:
    pdf_bytes = _build_pdf(
        projeto,
        tables_text,
        st.session_state.get("jc_ai_result", ""),
        st.session_state.get("jc_entrevistas_summary", ""),
    )
    st.download_button(
        "📄 Exportar PDF",
        data=pdf_bytes,
        file_name=f"{projeto.get('nome', 'analise_jornada')}.pdf",
        mime="application/pdf",
        width="stretch",
        type="primary",
    )
