"""
NENC Insights — Visualização de dados de Neuromarketing.

Controlador de navegação. O menu lateral é permanente: os três módulos ficam
sempre alcançáveis, e o módulo aberto expande suas páginas em seções
nomeadas. Nenhuma página é registrada fora do menu — o bloco de CSS que
escondia as sete páginas do NencBoost foi removido.

O NencBoost tem três níveis. Sem projeto aberto, o menu mostra a lista de
projetos, a base de conhecimento e a coleta via WhatsApp. Com um projeto
aberto, a seção passa a levar o nome dele e reúne as páginas do projeto —
é assim que o contexto ativo fica visível, já que o `st.navigation` desenha
o menu sempre no topo da barra lateral e nada pode ficar acima dele. Com uma
entrevista aberta, Timeline e Análise entram nessa mesma seção.
"""

import streamlit as st

from utils import auth, ui
from utils.icons import material

st.set_page_config(
    page_title="NENC Insights",
    page_icon="🧠",  # favicon do navegador; a interface usa utils.icons
    layout="wide",
    initial_sidebar_state="expanded",
)

# Antes do login: a tela de autenticação também usa o tema.
ui.inject_theme()

# Rótulo e página de entrada de cada módulo, na ordem em que aparecem.
MODULES = (
    ("teste_sensorial", "Teste Sensorial", "waveform",
     "modules/teste_sensorial/preparacao.py"),
    ("jornada_compra", "Jornada de Compra", "eye",
     "modules/jornada_compra/preparacao.py"),
    ("prosodia", "NencBoost", "microphone-stage",
     "modules/prosodia/projetos.py"),
)


def _module_of_route(url_path: str) -> str | None:
    """Módulo dono de uma rota, ou None para Visão geral e Administração."""
    for entry in MODULES:
        if url_path.startswith(entry[0].replace("_", "-") + "-"):
            return entry[0]
    return None


def _other_modules(user: auth.User) -> list:
    """Módulos que a conta alcança, tirando o que já está aberto."""
    active = st.session_state.get("modulo")
    return [
        entry for entry in MODULES
        if entry[0] != active and auth.can_access_module(user, entry[0])
    ]


def _url_path(path: str) -> str:
    """Rota unica a partir do caminho do arquivo.

    O Streamlit infere a rota do nome do arquivo, e tres modulos tem um
    `preparacao.py`. Como o menu permanente registra o modulo aberto ao lado
    dos outros, as rotas inferidas colidiriam.
    """
    stem = path.removesuffix(".py")
    for prefix in ("modules/", "pages/"):
        stem = stem.removeprefix(prefix)
    return stem.replace("/", "-").replace("_", "-")


def _page(path: str, title: str, icon_name: str, **kwargs) -> st.Page:
    return st.Page(
        path,
        title=title,
        icon=material(icon_name),
        url_path=_url_path(path),
        **kwargs,
    )


def _module_pages(module_key: str, user: auth.User) -> dict[str, list]:
    """Páginas internas do módulo aberto, agrupadas por seção."""
    if module_key == "teste_sensorial":
        return {
            "Teste Sensorial": [
                _page("modules/teste_sensorial/preparacao.py",
                      "Preparação de Dados", "folder-open"),
                # Timeline e Média Geral unificadas em "Sinais".
                _page("modules/teste_sensorial/sinais.py",
                      "Sinais", "chart-line"),
            ],
        }

    if module_key == "jornada_compra":
        return {
            "Jornada de Compra": [
                _page("modules/jornada_compra/preparacao.py",
                      "Preparação de Dados", "folder-open"),
                _page("modules/jornada_compra/analise.py",
                      "Análise", "chart-bar"),
                _page("modules/jornada_compra/base_conhecimento.py",
                      "Base de Conhecimento", "books"),
            ],
        }

    if module_key == "prosodia":
        return _prosodia_pages(user)

    return {}


def _active_project() -> dict | None:
    """Projeto aberto, ou None. Limpa o estado se ele apontar para o vazio."""
    project_id = st.session_state.get("pros_project_id")
    if not project_id:
        return None
    try:
        from utils.prosodia_db import get_project

        project = get_project(project_id)
    except Exception:
        return None
    if not project:
        st.session_state.pop("pros_project_id", None)
        st.session_state.pop("pros_audio_id", None)
        return None
    return project


def _prosodia_pages(user: auth.User) -> dict[str, list]:
    """Menu do NencBoost, em três níveis conforme o contexto aberto."""
    sections: dict[str, list] = {}
    project = _active_project()

    if project:
        # Nível 2: a seção leva o nome do projeto — é onde o contexto ativo
        # cabe, acima das páginas dele.
        project_pages = [
            _page("modules/prosodia/entrevistas.py",
                  "Entrevistas", "list-bullets"),
            _page("modules/prosodia/analise_geral.py",
                  "Análise Geral", "chart-bar"),
            _page("modules/prosodia/audios.py",
                  "Uploads", "upload-simple"),
            _page("modules/prosodia/preparacao.py",
                  "Dados do Projeto", "note-pencil"),
        ]
        # Nível 3: Timeline e Análise pertencem a uma entrevista, e são
        # abertas pelas ações da linha na tabela de Entrevistas.
        if st.session_state.get("pros_audio_id"):
            project_pages.extend([
                _page("modules/prosodia/audio_timeline.py",
                      "Timeline", "chart-line"),
                _page("modules/prosodia/audio_analise.py",
                      "Análise", "sparkle"),
            ])
        sections[str(project["name"])] = project_pages

    # Nível 1: continua visível com um projeto aberto, senão Campanhas e Base
    # de Conhecimento só seriam alcançáveis passando por "Todos os projetos".
    module_pages = [
        _page("modules/prosodia/projetos.py",
              "Todos os projetos" if project else "Projetos", "folders"),
    ]
    if not project:
        # Sem projeto, `preparacao.py` é o formulário de novo projeto; com um
        # projeto aberto ele é "Dados do Projeto", na seção acima.
        module_pages.append(
            _page("modules/prosodia/preparacao.py", "Novo projeto", "plus")
        )
    module_pages.append(
        _page("modules/prosodia/base_conhecimento.py",
              "Base de Conhecimento", "books")
    )
    sections["NencBoost"] = module_pages

    # Antes escondida por CSS; agora uma seção própria do menu.
    whatsapp = [
        _page("modules/prosodia/whatsapp_contatos.py",
              "Contatos", "address-book"),
        _page("modules/prosodia/whatsapp_campanhas.py",
              "Campanhas", "megaphone"),
        _page("modules/prosodia/whatsapp_monitor.py",
              "Monitor", "broadcast"),
    ]
    if user.is_platform_admin:
        whatsapp.append(
            _page("modules/prosodia/whatsapp_config.py",
                  "Config API", "gear-six")
        )
    sections["Coleta via WhatsApp"] = whatsapp

    return sections


def _build_pages(user: auth.User) -> dict[str, list]:
    active_module = st.session_state.get("modulo")
    if active_module and not auth.can_access_module(user, active_module):
        st.session_state.pop("modulo", None)
        active_module = None

    pages: dict[str, list] = {
        "": [
            st.Page(
                "home.py",
                title="Visão geral",
                icon=material("squares-four"),
                default=True,
            ),
            # Entradas dos outros módulos: registradas para o `st.page_link`
            # da barra lateral poder alcançá-las, e ocultas do menu. Uma
            # seção própria renderizava só o cabeçalho, sem os itens.
            *(
                _page(entry[3], entry[1], entry[2], visibility="hidden")
                for entry in _other_modules(user)
            ),
        ]
    }

    if active_module:
        pages.update(_module_pages(active_module, user))

    if user.is_admin:
        pages["Administração"] = [
            _page("pages/admin_users.py", "Usuários", "users-three")
        ]

    return pages


def _reset_audio_selection() -> None:
    st.session_state.pop("pros_audio_id", None)


def _render_project_context(user: auth.User) -> None:
    """Seletor do projeto aberto, no topo da barra lateral.

    Antes vivia só em `entrevistas.py`; com o menu permanente o contexto
    precisa valer em todas as páginas do projeto. Só aparece quando já há um
    projeto aberto — na lista de projetos não há contexto a trocar.
    """
    if st.session_state.get("modulo") != "prosodia":
        return
    if not st.session_state.get("pros_project_id"):
        return
    if not auth.can_access_module(user, "prosodia"):
        return
    try:
        from utils.prosodia_db import get_projects

        projects = get_projects()
    except Exception:
        return
    if not projects:
        return

    ui.context_selector(
        kicker="Projeto ativo",
        options=projects,
        id_key="id",
        label_key="name",
        state_key="pros_project_id",
        meta=lambda record: (
            ("file-audio", str(record.get("n_audios", 0))),
            ("plug", "API #{}".format(record["api_project_id"]))
            if record.get("api_project_id")
            else ("plug", "sem API"),
        ),
        on_change=_reset_audio_selection,
    )


def _render_module_links(user: auth.User) -> None:
    """Troca de módulo na barra lateral, sobre as páginas ocultas.

    Roda depois do `st.navigation`: o `st.page_link` exige que a página já
    esteja registrada nesta execução.
    """
    others = _other_modules(user)
    if not others:
        return
    with st.sidebar:
        st.markdown(
            '<div style="font-size:.6rem;letter-spacing:.12em;'
            'text-transform:uppercase;color:var(--nenc-faint);'
            'padding:.7rem 0 .1rem">Módulos</div>',
            unsafe_allow_html=True,
        )
        for _key, label, icon_name, path in others:
            st.page_link(path, label=label, icon=material(icon_name))


def _clear_organization_ui_state_if_needed(user: auth.User) -> None:
    active_organization_id = auth.active_organization_id(user)
    state_key = "_nenc_ui_state_organization_id"
    previous_organization_id = st.session_state.get(state_key)
    if (
        previous_organization_id is not None
        and previous_organization_id != active_organization_id
    ):
        for session_key in (
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
            "_ctx_pros_project_id",
            "_ctx_pros_project_id_shadow",
            "_ctx_pros_audio_id",
            "_ctx_pros_audio_id_shadow",
        ):
            st.session_state.pop(session_key, None)
    st.session_state[state_key] = active_organization_id


authenticated_user = auth.render_login_page()
if authenticated_user is None:
    # Sem uma chamada a st.navigation, o Streamlit cai na descoberta
    # automatica de `pages/` e lista "app" e "admin users" na barra lateral
    # da tela de login. Uma navegacao oculta de uma pagina so desliga isso.
    st.navigation(
        [st.Page("home.py", title="NENC Insights")], position="hidden"
    )
    st.stop()

_clear_organization_ui_state_if_needed(authenticated_user)
# O seletor roda antes de montar o menu: trocar de projeto precisa valer já
# nesta execução, e não só no rerun seguinte.
_render_project_context(authenticated_user)

# `expanded=True` e obrigatorio aqui. No padrao (`False`) o Streamlit colapsa
# o menu para caber na altura da barra lateral e esconde o excedente: com as
# 14 paginas mais o seletor de projeto e o cartao de sessao, sobravam 10 itens
# e a seccao "Administracao" sumia inteira.
pg = st.navigation(_build_pages(authenticated_user), expanded=True)

# O modulo ativo deriva da pagina que o menu resolveu. Chegar por
# `st.page_link` ou por URL colada nao passa pela Visao geral, e sem isto o
# menu continuaria expandindo o modulo anterior. O rerun so acontece na
# troca; na execucao seguinte a condicao ja e falsa. Fica aqui, e nao em
# `auth.require_module`, porque aquela funcao tambem autoriza leitura de
# dados — a Visao geral le os tres modulos de uma vez.
_rota_modulo = _module_of_route(getattr(pg, "url_path", "") or "")
if _rota_modulo and st.session_state.get("modulo") != _rota_modulo:
    st.session_state["modulo"] = _rota_modulo
    st.rerun()

# Depois do menu: `st.page_link` so alcanca pagina ja registrada.
_render_module_links(authenticated_user)
auth.render_auth_sidebar(authenticated_user)

if st.session_state.get("_navigate_to"):
    _target = st.session_state.pop("_navigate_to")
    try:
        st.switch_page(_target)
    except Exception:
        pass

pg.run()
