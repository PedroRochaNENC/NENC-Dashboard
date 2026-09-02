"""
Camada de UI compartilhada do NENC Insights.

Reune o que antes estava repetido em cada pagina: a folha de estilo do tema,
a trilha de navegacao, o seletor de contexto (projeto / entrevista) e a barra
de acoes da linha selecionada.

Ordem de uso numa pagina de modulo:

    from utils import ui
    from utils.icons import page_title

    ui.inject_theme()
    ui.breadcrumb("NencBoost", project["name"], "Entrevistas")
    page_title("list-bullets", "Entrevistas", "18 no projeto")
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

import streamlit as st

from utils.icons import icon

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
# Mesmos valores do .streamlit/config.toml, expostos como variaveis CSS para
# o HTML custom (cabecalhos, trilha, barra de acoes e o componente da
# Timeline do NencBoost) nao precisar repetir hex.

TOKENS = {
    "bg": "#161826",
    "surface": "#1c1e2c",
    "border": "#3f424d",
    "text": "#e9e9ed",
    "muted": "#9397ab",
    "faint": "#75798c",
    "dim": "#595d6c",
    "accent": "#9184d9",
    "accent-300": "#d2cefd",
    "accent-400": "#b5abfc",
    "accent-700": "#5d5294",
    "accent-800": "#423a6a",
}

_THEME_CSS = """
<style>
:root {{
{vars}
}}
/* Tipografia dos titulos: hierarquia por tamanho, nao por peso */
h1, h2, h3 {{ font-weight: 500 !important; letter-spacing: -.015em; }}
h1 {{ font-size: 1.75rem !important; }}
h2 {{ font-size: 1.3rem !important; }}
h3 {{ font-size: 1.05rem !important; }}

/* Menu lateral: seccoes com rotulo discreto e item ativo marcado a esquerda */
[data-testid="stSidebarNav"] {{ padding-top: .3rem; }}
[data-testid="stSidebarNav"] > div > div > div > span {{
    font-size: .62rem; letter-spacing: .12em; text-transform: uppercase;
    color: var(--nenc-dim);
}}
[data-testid="stSidebarNav"] a {{ border-radius: 6px; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: rgba(145,132,217,.13);
    border-left: 2px solid var(--nenc-accent);
}}

/* Botao primario como contorno de accent, nao preenchimento.
   `st.button` gera kind="primary"; `st.form_submit_button` gera
   kind="primaryFormSubmit" e fora de .stButton — os dois precisam constar,
   senao todo botao de formulario escapa da regra. */
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"] {{
    background: transparent;
    border: 1px solid var(--nenc-accent);
    color: var(--nenc-accent-300);
}}
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:hover {{
    background: rgba(145,132,217,.12);
    border-color: var(--nenc-accent-400);
    color: var(--nenc-accent-300);
}}
button:focus-visible {{
    outline: 2px solid var(--nenc-accent); outline-offset: 2px;
}}
</style>
"""


def inject_theme() -> None:
    """Publica os tokens e os ajustes de tema.

    Sem guarda de sessao: o Streamlit refaz o DOM a cada rerun, entao um
    ``<style>`` emitido so na primeira execucao desapareceria na interacao
    seguinte. Repetir a chamada no mesmo rerun apenas reemite a mesma folha.
    """
    variables = "\n".join(
        "    --nenc-{}: {};".format(key, value) for key, value in TOKENS.items()
    )
    st.markdown(_THEME_CSS.format(vars=variables), unsafe_allow_html=True)


def css_variables() -> str:
    """Bloco `:root` para embutir em componentes HTML isolados (iframes)."""
    return ":root{" + "".join(
        "--nenc-{}:{};".format(key, value) for key, value in TOKENS.items()
    ) + "}"


# ---------------------------------------------------------------------------
# Trilha de navegacao
# ---------------------------------------------------------------------------

def resolve_js_colors(markup: str) -> str:
    """Troca `'var(--nenc-x)'` por hex literal, para o contexto JS.

    Um `<canvas>` nao resolve custom properties: bibliotecas de grafico
    entregam a string ao contexto 2D, que a rejeita e desenha em preto. No
    CSS o `var()` funciona normalmente, entao so as ocorrencias entre aspas
    simples — as que viram valor de JavaScript — sao substituidas.
    """
    for key, value in TOKENS.items():
        markup = markup.replace(
            "'var(--nenc-{})'".format(key), "'{}'".format(value)
        )
    return markup


def breadcrumb(*parts: str) -> None:
    """Trilha discreta no topo da pagina. O ultimo item e o atual."""
    visible = [part for part in parts if part]
    if not visible:
        return
    separator = (
        '<span style="color:var(--nenc-dim);display:flex">{}</span>'.format(
            icon("caret-right", 11)
        )
    )
    rendered = []
    for index, part in enumerate(visible):
        is_last = index == len(visible) - 1
        color = "var(--nenc-muted)" if is_last else "var(--nenc-faint)"
        rendered.append(
            '<span style="color:{c}">{p}</span>'.format(c=color, p=part)
        )
    st.markdown(
        '<div style="display:flex;align-items:center;gap:.45rem;'
        'font-size:.75rem;padding:.1rem 0 .5rem">{}</div>'.format(
            separator.join(rendered)
        ),
        unsafe_allow_html=True,
    )


def status_chip(name: str, text: str, tone: str = "muted") -> str:
    """Selo pequeno para estado (API conectada, filtro ativo, sincronizacao)."""
    if tone == "accent":
        style = (
            "background:rgba(145,132,217,.08);border:1px solid "
            "var(--nenc-accent-800);color:var(--nenc-accent-300)"
        )
    else:
        style = (
            "border:1px solid var(--nenc-border);color:var(--nenc-muted)"
        )
    return (
        '<span style="display:inline-flex;align-items:center;gap:.35rem;'
        'font-size:.7rem;padding:.18rem .5rem;border-radius:5px;{s}">'
        "{i}{t}</span>".format(s=style, i=icon(name, 12), t=text)
    )


# ---------------------------------------------------------------------------
# Seletor de contexto
# ---------------------------------------------------------------------------

def context_selector(
    *,
    kicker: str,
    options: Sequence[dict],
    id_key: str,
    label_key: str,
    state_key: str,
    meta: Callable[[dict], Iterable[tuple[str, str]]] | None = None,
    on_change: Callable[[], None] | None = None,
) -> dict | None:
    """
    Seletor do objeto ativo (projeto ou entrevista), no topo da barra lateral.

    Substitui os botoes de salto que cada pagina repetia no cabecalho: o
    contexto passa a ser trocado sem sair da pagina atual.

    Devolve o registro ativo, ou None se a lista estiver vazia.
    """
    if not options:
        return None

    ids = [str(item[id_key]) for item in options]
    labels = {str(item[id_key]): str(item[label_key]) for item in options}

    widget_key = "_ctx_" + state_key
    shadow_key = widget_key + "_shadow"

    current = str(st.session_state.get(state_key) or ids[0])
    if current not in ids:
        current = ids[0]

    # Quem chega por outro caminho — a lista de projetos, por exemplo — grava
    # `state_key` direto. O Streamlit da prioridade ao valor guardado da chave
    # do widget sobre o `index=`, entao o seletor precisa acompanhar quando o
    # estado muda por fora. O espelho distingue essa mudanca externa da
    # escolha feita no proprio seletor, que nao deve ser desfeita.
    if st.session_state.get(shadow_key) != current:
        st.session_state[widget_key] = current
        st.session_state[shadow_key] = current

    with st.sidebar:
        st.markdown(
            '<div style="font-size:.6rem;letter-spacing:.12em;'
            'text-transform:uppercase;color:var(--nenc-faint);'
            'padding:.2rem 0 .25rem">{}</div>'.format(kicker),
            unsafe_allow_html=True,
        )
        chosen = st.selectbox(
            kicker,
            ids,
            index=ids.index(current),
            format_func=lambda value: labels[value],
            key=widget_key,
            label_visibility="collapsed",
            on_change=on_change,
        )

    record = next(item for item in options if str(item[id_key]) == chosen)
    # Guarda o id no tipo original (int, no SQLite) para nao divergir de quem
    # grava esta mesma chave a partir do registro.
    st.session_state[state_key] = record[id_key]
    st.session_state[shadow_key] = chosen

    if meta:
        pairs = list(meta(record))
        if pairs:
            with st.sidebar:
                st.markdown(
                    '<div style="display:flex;gap:.8rem;flex-wrap:wrap;'
                    'font-size:.7rem;color:var(--nenc-muted);'
                    'padding:.1rem 0 .5rem">{}</div>'.format(
                        "".join(
                            '<span style="display:inline-flex;'
                            'align-items:center;gap:.3rem">{i}{t}</span>'.format(
                                i=icon(icon_name, 12), t=text
                            )
                            for icon_name, text in pairs
                        )
                    ),
                    unsafe_allow_html=True,
                )

    return record


# ---------------------------------------------------------------------------
# Barra de acoes da linha selecionada
# ---------------------------------------------------------------------------

def selection_bar(
    count: int,
    actions: Sequence[tuple[str, str, str]],
    *,
    noun: str = "item",
    noun_plural: str | None = None,
    empty_hint: str = "Selecione uma linha na tabela para agir sobre ela.",
) -> str | None:
    """
    Barra de acoes ancorada logo abaixo da tabela.

    `actions` sao trios `(chave, icone, rotulo)`. A ultima acao e tratada
    como destrutiva e recebe alinhamento e cor propios.

    Devolve a chave da acao acionada, ou None.
    """
    if count <= 0:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:.55rem;'
            'padding:.6rem .75rem;border-radius:8px;border:1px solid '
            "var(--nenc-border);background:var(--nenc-surface);"
            'font-size:.78rem;color:var(--nenc-muted)">{i}{t}</div>'.format(
                i=icon("info", 15), t=empty_hint
            ),
            unsafe_allow_html=True,
        )
        return None

    plural = noun_plural or (noun + "s")
    st.markdown(
        '<div style="display:flex;align-items:center;gap:.5rem;'
        'font-size:.78rem;color:var(--nenc-accent-300);'
        'padding:.15rem 0 .35rem">{i}{n} {w} selecionada{s}</div>'.format(
            i=icon("check", 14),
            n=count,
            w=noun if count == 1 else plural,
            s="" if count == 1 else "s",
        ),
        unsafe_allow_html=True,
    )

    triggered: str | None = None
    columns = st.columns(len(actions) + 1)
    for column, (key, icon_name, text) in zip(columns, actions):
        with column:
            if st.button(
                text,
                key="_sel_" + key,
                icon=None,
                width="stretch",
                help=text,
            ):
                triggered = key
    return triggered
