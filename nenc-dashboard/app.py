"""
NENC Insights — Visualização de dados de Neuromarketing.

Controlador de navegação: st.navigation() com páginas dinâmicas por módulo.
"""

import streamlit as st

from utils import auth

st.set_page_config(
    page_title="NENC Insights",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _with_admin_pages(user: auth.User, pages):
    if user.is_admin:
        pages["Administracao"] = [
            st.Page(
                "pages/admin_users.py",
                title="Administracao de usuarios",
                icon="👤",
            )
        ]
    return pages


def _build_pages(user: auth.User):
    """Constrói lista de páginas com base no módulo selecionado."""
    modulo = st.session_state.get("modulo")
    if modulo and not auth.can_access_module(user, modulo):
        st.session_state.pop("modulo", None)
        modulo = None

    home = st.Page("home.py", title="Início", icon="🧠", default=True)

    if modulo == "teste_sensorial":
        return _with_admin_pages(user, {
            "": [home],
            "Teste Sensorial": [
                st.Page(
                    "modules/teste_sensorial/preparacao.py",
                    title="Preparação de Dados",
                    icon="📂",
                ),
                st.Page(
                    "modules/teste_sensorial/timeline.py",
                    title="Timeline",
                    icon="📊",
                ),
                st.Page(
                    "modules/teste_sensorial/media_geral.py",
                    title="Média Geral",
                    icon="👥",
                ),
            ],
        })

    if modulo == "jornada_compra":
        return _with_admin_pages(user, {
            "": [home],
            "Jornada de Compra": [
                st.Page(
                    "modules/jornada_compra/preparacao.py",
                    title="Preparação de Dados",
                    icon="📂",
                ),
                st.Page(
                    "modules/jornada_compra/base_conhecimento.py",
                    title="Base de Conhecimento",
                    icon="📚",
                ),
                st.Page(
                    "modules/jornada_compra/analise.py",
                    title="Análise",
                    icon="🔍",
                ),
            ],
        })

    if modulo == "prosodia":
        # Registra páginas internas para permitir switch_page,
        # mas esconde esses itens do menu lateral.
        st.markdown(
            """
            <style>
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/preparacao.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Fpreparacao.py"],
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/audio_timeline.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Faudio_timeline.py"],
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/audio_analise.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Faudio_analise.py"],
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/whatsapp_config.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Fwhatsapp_config.py"],
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/whatsapp_contatos.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Fwhatsapp_contatos.py"],
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/whatsapp_campanhas.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Fwhatsapp_campanhas.py"],
            [data-testid="stSidebarNav"] a[href*="modules/prosodia/whatsapp_monitor.py"],
            [data-testid="stSidebarNav"] a[href*="modules%2Fprosodia%2Fwhatsapp_monitor.py"] {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        return _with_admin_pages(user, {
            "": [home],
            "NencLex": [
                st.Page(
                    "modules/prosodia/projetos.py",
                    title="Projetos",
                    icon="🎙️",
                ),
                st.Page(
                    "modules/prosodia/preparacao.py",
                    title="Dados do Projeto",
                    icon="📋",
                ),
                st.Page(
                    "modules/prosodia/entrevistas.py",
                    title="Entrevistas",
                    icon="🗂️",
                ),
                st.Page(
                    "modules/prosodia/analise_geral.py",
                    title="Análise Geral",
                    icon="🧠",
                ),
                st.Page(
                    "modules/prosodia/audios.py",
                    title="Uploads",
                    icon="📤",
                ),
                st.Page(
                    "modules/prosodia/audio_timeline.py",
                    title="Timeline",
                    icon="📊",
                ),
                st.Page(
                    "modules/prosodia/audio_analise.py",
                    title="Análise",
                    icon="🤖",
                ),
                st.Page(
                    "modules/prosodia/base_conhecimento.py",
                    title="Base de Conhecimento",
                    icon="📚",
                ),
                *(
                    [
                        st.Page(
                            "modules/prosodia/whatsapp_config.py",
                            title="Config API",
                            icon="🔧",
                        ),
                    ]
                    if user.is_platform_admin
                    else []
                ),
                st.Page(
                    "modules/prosodia/whatsapp_contatos.py",
                    title="Contatos",
                    icon="👤",
                ),
                st.Page(
                    "modules/prosodia/whatsapp_campanhas.py",
                    title="Campanhas",
                    icon="📢",
                ),
                st.Page(
                    "modules/prosodia/whatsapp_monitor.py",
                    title="Monitor",
                    icon="📡",
                ),
            ],
        })

    return _with_admin_pages(user, {"": [home]})


def _clear_organization_ui_state_if_needed(user: auth.User) -> None:
    active_organization_id = auth.active_organization_id(user)
    state_key = "_nenc_ui_state_organization_id"
    previous_organization_id = st.session_state.get(state_key)
    if (
        previous_organization_id is not None
        and previous_organization_id != active_organization_id
    ):
        for session_key in (
            "modulo",
            "_navigate_to",
            "pros_project_id",
            "pros_audio_id",
            "pros_timeline_focus",
            "prep_campaign_select",
            "editor_importacao",
            "mon_filter_phone",
            "api_audio_file",
            "api_audio_label",
            "jc_metric",
            "jc_participants",
            "jc_aois",
            "jc_marcas",
            "jc_ai_model",
            "jc_ai_mode",
            "jc_use_kb",
        ):
            st.session_state.pop(session_key, None)
    st.session_state[state_key] = active_organization_id


authenticated_user = auth.render_login_page()
if authenticated_user is None:
    st.stop()

auth.render_auth_sidebar(authenticated_user)
_clear_organization_ui_state_if_needed(authenticated_user)
pg = st.navigation(_build_pages(authenticated_user))

if st.session_state.get("_navigate_to"):
    _target = st.session_state.pop("_navigate_to")
    try:
        st.switch_page(_target)
    except Exception:
        pass

pg.run()
