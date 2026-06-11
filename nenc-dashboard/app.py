"""
NENC Insights — Visualização de dados de Neuromarketing.

Controlador de navegação: st.navigation() com páginas dinâmicas por módulo.
"""

import streamlit as st

st.set_page_config(
    page_title="NENC Insights",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _build_pages():
    """Constrói lista de páginas com base no módulo selecionado."""
    modulo = st.session_state.get("modulo")

    home = st.Page("home.py", title="Início", icon="🧠", default=True)

    if modulo == "teste_sensorial":
        return {
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
        }

    if modulo == "jornada_compra":
        return {
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
        }

    if modulo == "prosodia":
        return {
            "": [home],
            "Prosódia": [
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
                    "modules/prosodia/audios.py",
                    title="Áudios",
                    icon="🎵",
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
            ],
        }

    return {"": [home]}


pg = st.navigation(_build_pages())
pg.run()
