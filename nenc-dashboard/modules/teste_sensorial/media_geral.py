"""
Teste Sensorial — Média Geral por Etapa.
"""

import streamlit as st
import pandas as pd
from groq import Groq

from utils.charts import create_average_by_etapa, create_perifericos_by_etapa
from utils.data_loader import get_etapas


def _format_summary_table(df: pd.DataFrame, title: str) -> str:
    if df.empty:
        return ""
    lines = [f"### {title}"]
    lines.append(df.to_string())
    return "\n".join(lines)

INDICADORES_METRICS = [
    "engagement_score",
    "atencao",
    "WTP",
    "Memoria_log",
    "assimetria",
    "Alpha/Beta",
    "AWI_frontal",
]

PERIFERICOS_RAW = ["BPM", "RMSSD", "GSR_CAL_mean"]
PERIFERICOS_Z = ["BPM_zscore", "RMSSD_zscore", "GSR_CAL_zscore"]


st.title("👥 Média Geral por Etapa")

data = st.session_state.get("ts_data", {})

if not data or "indicadores" not in data:
    st.warning(
        "⚠️ Nenhum dado carregado. "
        "Volte à página de Preparação de Dados e carregue os dados."
    )
    st.stop()

indicadores: pd.DataFrame = data["indicadores"].copy()
perifericos: pd.DataFrame = data.get("perifericos", pd.DataFrame()).copy()

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Controles")

    use_zscore = st.checkbox("Usar Z-Scores (periféricos)", value=False)

    st.divider()
    st.subheader("🤖 Análise de IA")
    api_key = st.text_input(
        "Chave da API Groq",
        type="password",
        key="ts_groq_key",
        help="Obtenha gratuitamente em console.groq.com/keys",
    )

    st.divider()

    all_etapas = get_etapas(data)
    if all_etapas:
        selected_etapas = st.multiselect(
            "Etapas para análise",
            options=all_etapas,
            default=all_etapas,
            key="etapas_media",
        )
    else:
        selected_etapas = []

    if "Codigo" in indicadores.columns:
        codigos = ["Todos"] + sorted(
            indicadores["Codigo"].dropna().unique().astype(str).tolist()
        )
        selected_codigo = st.selectbox("Filtrar por Código", codigos)
        if selected_codigo != "Todos":
            indicadores = indicadores[
                indicadores["Codigo"].astype(str) == selected_codigo
            ]
            if (
                not perifericos.empty
                and "Codigo" in perifericos.columns
            ):
                perifericos = perifericos[
                    perifericos["Codigo"].astype(str) == selected_codigo
                ]

# ------------------------------------------------------------------
# Filtrar etapas selecionadas
# ------------------------------------------------------------------
if selected_etapas:
    indicadores = indicadores[indicadores["Etapa"].isin(selected_etapas)]
    if not perifericos.empty and "Etapa" in perifericos.columns:
        perifericos = perifericos[perifericos["Etapa"].isin(selected_etapas)]
else:
    st.warning("Selecione pelo menos uma Etapa.")
    st.stop()

# ------------------------------------------------------------------
# Indicadores
# ------------------------------------------------------------------
st.subheader("Indicadores Neurais")

available_metrics = [
    m for m in INDICADORES_METRICS if m in indicadores.columns
]

if available_metrics:
    fig_ind = create_average_by_etapa(indicadores, available_metrics)
    st.plotly_chart(fig_ind, width='stretch')
else:
    st.info("Nenhuma métrica de indicadores disponível.")

# ------------------------------------------------------------------
# Periféricos
# ------------------------------------------------------------------
st.subheader("Periféricos")

if not perifericos.empty:
    fig_per = create_perifericos_by_etapa(
        perifericos, use_zscore=use_zscore
    )
    st.plotly_chart(fig_per, width='stretch')
else:
    st.info("Dados de periféricos não carregados.")

# ------------------------------------------------------------------
# Tabelas resumo
# ------------------------------------------------------------------
st.divider()
st.subheader("📋 Tabelas Resumo")

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Indicadores (média ± std por Etapa)**")
    if available_metrics:
        per_part = (
            indicadores
            .groupby(["filename", "Etapa"])[available_metrics]
            .mean()
            .reset_index()
        )
        summary = (
            per_part
            .groupby("Etapa")[available_metrics]
            .agg(["mean", "std"])
            .round(4)
        )
        st.dataframe(summary, width='stretch')

with col2:
    st.markdown("**Periféricos (média ± std por Etapa)**")
    if not perifericos.empty:
        per_metrics = PERIFERICOS_Z if use_zscore else PERIFERICOS_RAW
        per_available = [
            m for m in per_metrics if m in perifericos.columns
        ]
        if per_available:
            per_summary = (
                perifericos
                .groupby("Etapa")[per_available]
                .agg(["mean", "std"])
                .round(4)
            )
            st.dataframe(per_summary, width='stretch')

# ------------------------------------------------------------------
# Análise de IA
# ------------------------------------------------------------------
st.divider()
st.subheader("🤖 Análise de IA")

tables_text = ""
if available_metrics:
    per_part = (
        indicadores
        .groupby(["filename", "Etapa"])[available_metrics]
        .mean()
        .reset_index()
    )
    ind_summary = (
        per_part
        .groupby("Etapa")[available_metrics]
        .agg(["mean", "std"])
        .round(4)
    )
    tables_text += _format_summary_table(
        ind_summary, "Indicadores Neurais (média ± std por Etapa)"
    )

if not perifericos.empty:
    per_metrics = PERIFERICOS_Z if use_zscore else PERIFERICOS_RAW
    per_available_ai = [
        m for m in per_metrics if m in perifericos.columns
    ]
    if per_available_ai:
        per_summary_ai = (
            perifericos
            .groupby("Etapa")[per_available_ai]
            .agg(["mean", "std"])
            .round(4)
        )
        tables_text += "\n\n" + _format_summary_table(
            per_summary_ai, "Periféricos (média ± std por Etapa)"
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
    if st.button("🔍 Gerar Análise", key="btn_ai_ts"):
        system_prompt = (
            "Você é um especialista em neuromarketing e análise de dados "
            "de EEG e sinais periféricos (BPM, GSR, RMSSD). "
            "Analise os dados abaixo e forneça insights acionáveis. "
            "Destaque diferenças relevantes entre Etapas, padrões de "
            "engajamento, atenção e resposta emocional. "
            "Responda em português do Brasil de forma clara e estruturada."
        )
        user_prompt = (
            "Abaixo estão as tabelas resumo (média ± desvio padrão entre "
            "participantes) de um experimento de neuromarketing.\n\n"
            f"{tables_text}\n\n"
            "Por favor, forneça:\n"
            "1. Resumo geral dos resultados\n"
            "2. Comparação entre as Etapas\n"
            "3. Principais insights de engajamento e atenção\n"
            "4. Observações sobre os sinais periféricos (se disponíveis)\n"
            "5. Recomendações práticas"
        )

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
                    max_tokens=2048,
                )
                st.markdown(response.choices[0].message.content)
            except Exception as e:
                st.error(f"Erro ao chamar a API: {e}")
