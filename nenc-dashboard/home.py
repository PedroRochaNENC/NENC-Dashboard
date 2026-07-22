"""
Página Inicial — Seleção de módulo.
"""

import streamlit as st
from utils.ai_provider import get_openai_client, get_vector_store_id
from utils import auth
from utils.organization_data import load_module_state


user = auth.require_login()


def _select_module(module_key: str, navigate_to: str | None = None):
    auth.require_module(module_key)
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

module_cards = (
    (
        "teste_sensorial",
        "🧪 Teste Sensorial",
        "Análise de dados de **EEG e sinais periféricos** "
        "(BPM, GSR, RMSSD) coletados durante testes sensoriais.\n\n"
        "**Páginas:**\n"
        "- 📂 Preparação de Dados\n"
        "- 📊 Timeline sincronizada\n"
        "- 👥 Média Geral por Etapa",
        "Abrir Teste Sensorial →",
        "btn_ts",
        "modules/teste_sensorial/preparacao.py",
    ),
    (
        "jornada_compra",
        "🛒 Jornada de Compra",
        "Análise de dados de **eye-tracking** (fixações, sacadas, AOIs) "
        "coletados durante jornadas de compra.\n\n"
        "**Páginas:**\n"
        "- 📂 Preparação de Dados\n"
        "- 📚 Base de Conhecimento\n"
        "- 🔍 Análise",
        "Abrir Jornada de Compra →",
        "btn_jc",
        "modules/jornada_compra/preparacao.py",
    ),
    (
        "prosodia",
        "🎙️ NencLex",
        "Análise do **NencLex e transcrições de entrevistas** — "
        "segmentos VAD, features acústicas, qualidade de entrevista e análise com IA.\n\n"
        "**Páginas:**\n"
        "- 🎙️ Projetos\n"
        "- 📤 Uploads\n"
        "- 🗂️ Entrevistas\n"
        "- 📊 Timeline\n"
        "- 🤖 Análise + Qualidade",
        "Abrir NencLex →",
        "btn_pr",
        "modules/prosodia/projetos.py",
    ),
)
available_cards = [
    card for card in module_cards if auth.can_access_module(user, card[0])
]

if not available_cards:
    st.info("Nenhum modulo esta disponivel para esta conta. Contate um administrador.")
else:
    for column, card in zip(st.columns(len(available_cards)), available_cards):
        module_key, title, description, button_label, button_key, page_path = card
        with column:
            st.subheader(title)
            st.markdown(description)
            st.button(
                button_label,
                key=button_key,
                on_click=_select_module,
                args=(module_key, page_path),
                width="stretch",
                type="primary",
            )

# ------------------------------------------------------------------
# Status dos dados carregados
# ------------------------------------------------------------------
if available_cards:
    st.divider()
    st.markdown("### 📌 Status dos dados")
    available_modules = {card[0] for card in available_cards}
    ts_data = (
        load_module_state("teste_sensorial")
        if "teste_sensorial" in available_modules
        else {}
    )
    jc_data = (
        load_module_state("jornada_compra")
        if "jornada_compra" in available_modules
        else {}
    )

    for column, card in zip(st.columns(len(available_cards)), available_cards):
        with column:
            if card[0] == "teste_sensorial":
                if ts_data and {"indicadores", "perifericos", "psd_results"}.intersection(ts_data):
                    st.success("✅ Teste Sensorial — dados carregados")
                else:
                    st.info("Teste Sensorial — sem dados")
            elif card[0] == "jornada_compra":
                if jc_data and any(key for key in jc_data if key != "_errors"):
                    st.success("✅ Jornada de Compra — dados carregados")
                else:
                    st.info("Jornada de Compra — sem dados")
                vector_store_id = get_vector_store_id()
                if vector_store_id:
                    knowledge_client = get_openai_client()
                    if knowledge_client:
                        try:
                            files = list(
                                knowledge_client.vector_stores.files.list(
                                    vector_store_id=vector_store_id
                                )
                            )
                            st.success("📚 Base de Conhecimento — {} documentos".format(len(files)))
                        except Exception:
                            st.info("📚 Base de Conhecimento — configurada")
            elif card[0] == "prosodia":
                try:
                    from utils.prosodia_db import get_projects, init_db

                    init_db()
                    projects = get_projects()
                    if projects:
                        project_count = len(projects)
                        audio_count = sum(project.get("n_audios", 0) for project in projects)
                        st.success(
                            "✅ NencLex — {} projeto(s), {} entrevista(s)".format(
                                project_count, audio_count
                            )
                        )
                    else:
                        st.info("NencLex — nenhum projeto criado")
                except Exception as error:
                    st.warning("NencLex indisponivel: {}".format(error))
