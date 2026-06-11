"""
Página Inicial — Seleção de módulo.
"""

import streamlit as st
from utils.ai_provider import get_openai_client, get_vector_store_id


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

col1, col2, col3 = st.columns(3)

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
        width='stretch',
        type="primary",
    )

with col2:
    st.subheader("🛒 Jornada de Compra")
    st.markdown(
        "Análise de dados de **eye-tracking** (fixações, sacadas, AOIs) "
        "coletados durante jornadas de compra.\n\n"
        "**Páginas:**\n"
        "- 📂 Preparação de Dados\n"
        "- 📚 Base de Conhecimento\n"
        "- 🔍 Análise"
    )
    st.button(
        "Abrir Jornada de Compra →",
        key="btn_jc",
        on_click=_select_module,
        args=("jornada_compra", "modules/jornada_compra/preparacao.py"),
        width='stretch',
        type="primary",
    )

with col3:
    st.subheader("🎙️ Prosódia")
    st.markdown(
        "Análise de **prosódia e transcrições de entrevistas** — "
        "segmentos VAD, features acústicas, qualidade de entrevista e análise com IA.\n\n"
        "**Páginas:**\n"
        "- 🎙️ Projetos\n"
        "- 🎵 Áudios\n"
        "- 📊 Timeline\n"
        "- 🤖 Análise + Qualidade"
    )
    st.button(
        "Abrir Prosódia →",
        key="btn_pr",
        on_click=_select_module,
        args=("prosodia", "modules/prosodia/projetos.py"),
        width='stretch',
        type="primary",
    )

# ------------------------------------------------------------------
# Status dos dados carregados
# ------------------------------------------------------------------
st.divider()
st.markdown("### 📌 Status dos dados")

ts_data = st.session_state.get("ts_data", {})
jc_data = st.session_state.get("jc_data", {})
pr_data = st.session_state.get("pr_data", {})

c1, c2, c3 = st.columns(3)
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

    # Knowledge base status
    _vs_id = get_vector_store_id()
    if _vs_id:
        _kb_client = get_openai_client()
        if _kb_client:
            try:
                _vs_files = list(_kb_client.vector_stores.files.list(vector_store_id=_vs_id))
                st.success(f"📚 Base de Conhecimento — {len(_vs_files)} documentos")
            except Exception:
                st.info("📚 Base de Conhecimento — configurada")

with c3:
    try:
        from utils.prosodia_db import init_db, get_projects
        init_db()
        _projects = get_projects()
        if _projects:
            _n_proj = len(_projects)
            _n_aud = sum(p.get("n_audios", 0) for p in _projects)
            st.success(f"✅ Prosódia — {_n_proj} projeto(s), {_n_aud} áudio(s)")
        else:
            st.info("Prosódia — nenhum projeto criado")
    except Exception:
        pr_sessions = pr_data.get("sessions", [])
        if pr_sessions:
            st.success(f"✅ Prosódia — {len(pr_sessions)} sessão(ões) carregada(s)")
        else:
            st.info("Prosódia — sem dados")
