"""
Visão geral — ponto de entrada e estado dos módulos.

Cada módulo é um card com o próprio estado de dados no canto superior e as
páginas internas como atalhos reais. O estado deixou de ser um bloco de
alertas separado abaixo da dobra.
"""

from datetime import datetime

import streamlit as st

from utils import auth, ui
from utils.ai_provider import get_openai_client, get_vector_store_id
from utils.icons import icon
from utils.organization_data import load_module_state

user = auth.require_login()
ui.inject_theme()


def _select_module(module_key: str, navigate_to: str | None = None) -> None:
    auth.require_module(module_key)
    st.session_state["modulo"] = module_key
    if navigate_to:
        st.session_state["_navigate_to"] = navigate_to


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Bom dia"
    if hour < 18:
        return "Boa tarde"
    return "Boa noite"


# (chave, rótulo, ícone, descrição, páginas, destino)
MODULE_CARDS = (
    (
        "teste_sensorial",
        "Teste Sensorial",
        "waveform",
        "EEG e sinais periféricos (BPM, GSR, RMSSD).",
        (
            ("folder-open", "Preparação de Dados"),
            ("chart-line", "Sinais no tempo"),
            ("users-three", "Média por etapa"),
        ),
        "modules/teste_sensorial/preparacao.py",
    ),
    (
        "jornada_compra",
        "Jornada de Compra",
        "eye",
        "Eye-tracking: fixações, sacadas e AOIs.",
        (
            ("folder-open", "Preparação de Dados"),
            ("chart-bar", "Análise"),
            ("books", "Base de Conhecimento"),
        ),
        "modules/jornada_compra/preparacao.py",
    ),
    (
        "prosodia",
        "NencBoost",
        "microphone-stage",
        "Prosódia e transcrições, com análise por IA.",
        (
            ("folders", "Projetos"),
            ("file-audio", "Entrevistas"),
            ("sparkle", "Análise e qualidade"),
        ),
        "modules/prosodia/projetos.py",
    ),
)


def _module_status(module_key: str) -> tuple[str, bool]:
    """Devolve (texto do selo, tem_dados) para o card do módulo."""
    if module_key == "teste_sensorial":
        data = load_module_state("teste_sensorial")
        loaded = bool(
            data and {"indicadores", "perifericos", "psd_results"}.intersection(data)
        )
        return ("Dados carregados" if loaded else "Sem dados", loaded)

    if module_key == "jornada_compra":
        data = load_module_state("jornada_compra")
        loaded = bool(data and any(key for key in data if key != "_errors"))
        text = "Dados carregados" if loaded else "Sem dados"
        vector_store_id = get_vector_store_id()
        if vector_store_id:
            client = get_openai_client()
            if client:
                try:
                    documents = list(
                        client.vector_stores.files.list(
                            vector_store_id=vector_store_id
                        )
                    )
                    text += " · {} docs".format(len(documents))
                except Exception:
                    text += " · base configurada"
        return (text, loaded)

    if module_key == "prosodia":
        try:
            from utils.prosodia_db import get_projects, init_db

            init_db()
            projects = get_projects()
            if not projects:
                return ("Nenhum projeto criado", False)
            audios = sum(project.get("n_audios", 0) for project in projects)
            return (
                "{} projetos · {} entrevistas".format(len(projects), audios),
                True,
            )
        except Exception as error:
            return ("Indisponível: {}".format(error), False)

    return ("", False)


available = [card for card in MODULE_CARDS if auth.can_access_module(user, card[0])]

st.markdown(
    '<div style="display:flex;flex-direction:column;gap:.25rem;'
    'margin:.2rem 0 1.3rem">'
    '<h1 style="margin:0;font-size:1.75rem;font-weight:500;'
    'letter-spacing:-.018em">{greeting}, {name}</h1>'
    '<p style="margin:0;font-size:.85rem;color:var(--nenc-muted)">'
    "{count}</p></div>".format(
        greeting=_greeting(),
        name=(user.name or "").split(" ")[0] or "bem-vindo",
        count=(
            "{} módulo(s) disponível(is)".format(len(available))
            if available
            else "Nenhum módulo disponível para esta conta"
        ),
    ),
    unsafe_allow_html=True,
)

if not available:
    st.info("Nenhum módulo está disponível para esta conta. Contate um administrador.")
    st.stop()

for column, card in zip(st.columns(len(available)), available):
    module_key, title, icon_name, description, pages, destination = card
    status_text, has_data = _module_status(module_key)

    with column:
        with st.container(border=True):
            badge_style = (
                "background:rgba(145,132,217,.12);color:var(--nenc-accent-300);"
                "border:1px solid var(--nenc-accent-800)"
                if has_data
                else "border:1px solid var(--nenc-border);color:var(--nenc-muted)"
            )
            st.markdown(
                '<div style="display:flex;align-items:center;'
                'justify-content:space-between;gap:.5rem;margin-bottom:.7rem">'
                '<span style="color:var(--nenc-accent-400);display:flex">{i}</span>'
                '<span style="font-size:.63rem;padding:.15rem .45rem;'
                'border-radius:4px;{b}">{s}</span></div>'
                '<div style="font-size:1rem;font-weight:600;'
                'margin-bottom:.25rem">{t}</div>'
                '<p style="font-size:.78rem;line-height:1.5;'
                'color:var(--nenc-muted);margin:0 0 .7rem">{d}</p>'
                '<div style="border-top:1px solid rgba(233,233,237,.08);'
                'padding-top:.55rem;display:flex;flex-direction:column;'
                'gap:.3rem;margin-bottom:.7rem">{p}</div>'.format(
                    i=icon(icon_name, 19),
                    b=badge_style,
                    s=status_text,
                    t=title,
                    d=description,
                    p="".join(
                        '<span style="display:flex;align-items:center;'
                        'gap:.45rem;font-size:.75rem;'
                        'color:var(--nenc-muted)">{i}{n}</span>'.format(
                            i=icon(page_icon, 13), n=page_name
                        )
                        for page_icon, page_name in pages
                    ),
                ),
                unsafe_allow_html=True,
            )
            st.button(
                "Abrir módulo",
                key="btn_" + module_key,
                on_click=_select_module,
                args=(module_key, destination),
                width="stretch",
                type="primary",
            )
