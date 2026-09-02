"""
Teste Sensorial — Sinais no tempo.

Unifica as antigas páginas Timeline e Média Geral. "Individual" e "Média
geral" já eram um radio na barra lateral; agora são o mesmo alternador no
topo, e as médias por etapa aparecem abaixo do sinal bruto, no mesmo
contexto. Os oito checkboxes de indicador viraram um multiselect com rótulo
em português.

O filtro por código e a análise de IA vinham de Média Geral e continuam
aqui — a unificação era de layout, não de funcionalidade.
"""

import streamlit as st

from utils import auth

auth.require_module("teste_sensorial")

import pandas as pd
from groq import Groq

from utils import ui
from utils.charts import (
    create_average_by_etapa,
    create_perifericos_by_etapa,
    create_synchronized_timeline,
)
from utils.data_loader import get_etapas, get_participants
from utils.icons import page_title
from utils.organization_data import hydrate_session_state
from utils.resampler import (
    build_unified_timeline,
    compute_participant_average,
    get_etapa_boundaries,
)

# Nome técnico da coluna -> rótulo de interface
INDICATOR_LABELS = {
    "atencao": "Atenção",
    "WTP": "WTP",
    "Memoria_log": "Memória",
    "assimetria": "Assimetria",
    "Alpha/Beta": "Alpha/Beta",
    "AWI_frontal": "AWI frontal",
    "sens_asym": "Assimetria sensorial",
    "inst_sens": "Sensibilidade instantânea",
}

DEFAULT_ON = ("atencao", "WTP", "assimetria")

# Métricas das médias por etapa
AVERAGE_METRICS = [
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


def _section(title: str) -> None:
    """Rótulo de seção discreto, no lugar de `st.subheader`."""
    st.markdown(
        '<div style="font-size:.68rem;font-weight:600;letter-spacing:.1em;'
        'text-transform:uppercase;color:var(--nenc-faint);'
        'padding:.8rem 0 .3rem">{}</div>'.format(title),
        unsafe_allow_html=True,
    )


def _format_summary_table(frame: pd.DataFrame, title: str) -> str:
    if frame.empty:
        return ""
    return "### {}\n{}".format(title, frame.to_string())


hydrate_session_state("teste_sensorial", ("ts_data",))
ui.inject_theme()
ui.breadcrumb("Teste Sensorial", "Sinais")
page_title(
    "chart-line", "Sinais no tempo", "EEG e periféricos alinhados por etapa."
)

data = st.session_state.get("ts_data", {})

if not data or "indicadores" not in data:
    st.warning(
        "Nenhum dado carregado. Abra Preparação de Dados e carregue os "
        "arquivos do pipeline."
    )
    if st.button("Ir para Preparação de Dados", type="primary"):
        st.switch_page("modules/teste_sensorial/preparacao.py")
    st.stop()

indicadores: pd.DataFrame = data["indicadores"].copy()
perifericos: pd.DataFrame = data.get("perifericos", pd.DataFrame()).copy()
participants = get_participants(data)

# ---------------------------------------------------------------------------
# Recorte — alternador e participante no topo, camadas na barra lateral
# ---------------------------------------------------------------------------
head_left, head_right = st.columns([3, 2])

with head_left:
    view_mode = st.segmented_control(
        "Visualização",
        ["Individual", "Média geral"],
        default="Individual",
        key="ts_view_mode",
        label_visibility="collapsed",
    ) or "Individual"

selected_participant = None
with head_right:
    if view_mode == "Individual":
        if not participants:
            st.warning("Nenhum participante encontrado nos dados.")
            st.stop()
        selected_participant = st.selectbox(
            "Participante",
            participants,
            index=0,
            key="ts_participant",
            label_visibility="collapsed",
        )

with st.sidebar:
    st.markdown(
        '<div style="font-size:.6rem;letter-spacing:.12em;'
        'text-transform:uppercase;color:var(--nenc-faint);'
        'padding:.4rem 0 .3rem">Indicadores</div>',
        unsafe_allow_html=True,
    )
    available_indicators = [
        column for column in INDICATOR_LABELS if column in indicadores.columns
    ]
    selected_indicators = st.multiselect(
        "Indicadores",
        options=available_indicators,
        default=[
            column for column in available_indicators if column in DEFAULT_ON
        ],
        format_func=lambda column: INDICATOR_LABELS[column],
        key="ts_indicators",
        label_visibility="collapsed",
    )

    use_zscore = st.toggle("Z-scores (periféricos)", value=False, key="ts_zscore")

    if "Codigo" in indicadores.columns:
        codigos = ["Todos"] + sorted(
            indicadores["Codigo"].dropna().unique().astype(str).tolist()
        )
        selected_codigo = st.selectbox("Código", codigos, key="ts_codigo")
        if selected_codigo != "Todos":
            indicadores = indicadores[
                indicadores["Codigo"].astype(str) == selected_codigo
            ]
            if not perifericos.empty and "Codigo" in perifericos.columns:
                perifericos = perifericos[
                    perifericos["Codigo"].astype(str) == selected_codigo
                ]

    api_key = st.text_input(
        "Chave da API Groq",
        type="password",
        key="ts_groq_key",
        help="Obtenha gratuitamente em console.groq.com/keys",
    )

# Etapas: faixa acima do gráfico, filtro e legenda temporal ao mesmo tempo
all_etapas = get_etapas(data, filename=selected_participant)
selected_etapas = st.multiselect(
    "Etapas",
    options=all_etapas,
    default=all_etapas,
    key="ts_etapas",
    label_visibility="collapsed",
    placeholder="Etapas em análise",
)

if not selected_etapas:
    st.info("Selecione pelo menos uma etapa para ver os sinais.")
    st.stop()

indicadores = indicadores[indicadores["Etapa"].isin(selected_etapas)]
if not perifericos.empty and "Etapa" in perifericos.columns:
    perifericos = perifericos[perifericos["Etapa"].isin(selected_etapas)]

# ---------------------------------------------------------------------------
# Sinal no tempo
# ---------------------------------------------------------------------------
if view_mode == "Individual" and selected_participant:
    merged = build_unified_timeline(
        indicadores, perifericos, filename=selected_participant
    )
    chart_title = "Timeline — {}".format(selected_participant)
else:
    average_indicadores, average_perifericos = compute_participant_average(
        indicadores, perifericos
    )
    merged = build_unified_timeline(
        average_indicadores, average_perifericos, filename="Media Geral"
    )
    chart_title = "Timeline — média geral"

if merged.empty:
    st.warning("Sem dados para o recorte selecionado.")
    st.stop()

if not selected_indicators:
    st.info("Ligue ao menos um indicador na barra lateral.")
    st.stop()

st.plotly_chart(
    create_synchronized_timeline(
        merged=merged,
        boundaries=get_etapa_boundaries(merged),
        selected_indicators=selected_indicators,
        use_zscore=use_zscore,
        title=chart_title,
    ),
    width="stretch",
)

# ---------------------------------------------------------------------------
# Médias por etapa — no mesmo contexto do sinal bruto
# ---------------------------------------------------------------------------
_section("Médias por etapa")

average_available = [m for m in AVERAGE_METRICS if m in indicadores.columns]
peripheral_metrics = PERIFERICOS_Z if use_zscore else PERIFERICOS_RAW
peripheral_available = [
    m for m in peripheral_metrics if m in perifericos.columns
] if not perifericos.empty else []

left, right = st.columns(2)

with left:
    if average_available:
        st.plotly_chart(
            create_average_by_etapa(indicadores, average_available),
            width="stretch",
        )
    else:
        st.caption("Nenhuma métrica de indicadores disponível.")

with right:
    if not perifericos.empty:
        st.plotly_chart(
            create_perifericos_by_etapa(perifericos, use_zscore=use_zscore),
            width="stretch",
        )
    else:
        st.caption("Dados de periféricos não carregados.")


def _indicator_summary() -> pd.DataFrame | None:
    """Média ± desvio entre participantes, por etapa."""
    if not average_available:
        return None
    per_participant = (
        indicadores.groupby(["filename", "Etapa"])[average_available]
        .mean()
        .reset_index()
    )
    return (
        per_participant.groupby("Etapa")[average_available]
        .agg(["mean", "std"])
        .round(3)
    )


def _peripheral_summary() -> pd.DataFrame | None:
    if not peripheral_available:
        return None
    return (
        perifericos.groupby("Etapa")[peripheral_available]
        .agg(["mean", "std"])
        .round(3)
    )


indicator_summary = _indicator_summary()
peripheral_summary = _peripheral_summary()

with st.expander("Tabela resumo por etapa"):
    if indicator_summary is not None:
        st.dataframe(indicator_summary, width="stretch")
    if peripheral_summary is not None:
        st.dataframe(peripheral_summary, width="stretch")

# ---------------------------------------------------------------------------
# Análise de IA
# ---------------------------------------------------------------------------
_section("Análise de IA")

tables_text = ""
if indicator_summary is not None:
    tables_text += _format_summary_table(
        indicator_summary, "Indicadores Neurais (média ± std por Etapa)"
    )
if peripheral_summary is not None:
    tables_text += "\n\n" + _format_summary_table(
        peripheral_summary, "Periféricos (média ± std por Etapa)"
    )

if not api_key:
    st.caption(
        "Insira a chave da API Groq na barra lateral para habilitar a "
        "análise automática. Obtenha gratuitamente em console.groq.com/keys."
    )
elif not tables_text.strip():
    st.warning("Nenhum dado disponível para análise.")
elif st.button("Gerar análise", key="btn_ai_ts", type="primary"):
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
        "{}\n\n"
        "Por favor, forneça:\n"
        "1. Resumo geral dos resultados\n"
        "2. Comparação entre as Etapas\n"
        "3. Principais insights de engajamento e atenção\n"
        "4. Observações sobre os sinais periféricos (se disponíveis)\n"
        "5. Recomendações práticas".format(tables_text)
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
        except Exception as error:
            st.error("Erro ao chamar a API: {}".format(error))
