"""
Página Inicial — Seleção de módulo.
"""

import streamlit as st


def _select_module(module_key: str, navigate_to: str | None = None):
    st.session_state["modulo"] = module_key
    if navigate_to:
        st.session_state["_navigate_to"] = navigate_to


st.title("🧠 NENC Insights")

# Navegação automática por etapas
if st.session_state.get("_navigate_to"):
    _target = st.session_state.pop("_navigate_to")
    st.switch_page(_target)

st.markdown(
    "**Visualização de dados de Neuromarketing** — "
    "Selecione um módulo para começar."
)

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("🧪 Teste Sensorial")
    st.markdown(
        "Análise de dados de **EEG e sinais periféricos** "
        "(BPM, GSR, RMSSD) coletados durante testes sensoriais.\n\n"
        "**Páginas:**\n"
        "- 📂 Preparação de Dados\n"
        "- 📊 Timeline sincronizada\n"
        "- 👥 Média Geral por Etapa"
    )
    st.button(
        "Abrir Teste Sensorial →",
        key="btn_ts",
        on_click=_select_module,
        args=("teste_sensorial", "modules/teste_sensorial/preparacao.py"),
        use_container_width=True,
        type="primary",
    )

with col2:
    st.subheader("🛒 Jornada de Compra")
    st.markdown(
        "Análise de dados de **eye-tracking** (fixações, sacadas, AOIs) "
        "coletados durante jornadas de compra.\n\n"
        "**Páginas:**\n"
        "- 📂 Preparação de Dados\n"
        "- 🔍 Análise"
    )
    st.button(
        "Abrir Jornada de Compra →",
        key="btn_jc",
        on_click=_select_module,
        args=("jornada_compra", "modules/jornada_compra/preparacao.py"),
        use_container_width=True,
        type="primary",
    )

# ------------------------------------------------------------------
# Status dos dados carregados
# ------------------------------------------------------------------
st.divider()
st.markdown("### 📌 Status dos dados")

ts_data = st.session_state.get("ts_data", {})
jc_data = st.session_state.get("jc_data", {})

c1, c2 = st.columns(2)
with c1:
    if ts_data and {"indicadores", "perifericos", "psd_results"}.intersection(ts_data):
        st.success("✅ Teste Sensorial — dados carregados")
    else:
        st.info("Teste Sensorial — sem dados")

with c2:
    if jc_data and any(k for k in jc_data if k != "_errors"):
        st.success("✅ Jornada de Compra — dados carregados")
    else:
        st.info("Jornada de Compra — sem dados")
