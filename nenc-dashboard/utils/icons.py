"""
Conjunto de icones da interface NENC Insights.

Substitui os emojis usados em titulos, botoes e itens de menu. Sao SVGs
inline (sem CDN, sem dependencia externa), desenhados na grade de 24px com
traco de 1.75 e `currentColor`, para herdar a cor do contexto onde entram.

Uso tipico:

    from utils.icons import icon, page_title

    page_title("microphone-stage", "Projetos", "Cada projeto agrupa entrevistas.")
    st.markdown(icon("plus", 14) + " Novo projeto", unsafe_allow_html=True)

Para o menu lateral, `st.Page` aceita apenas emoji ou `:material/nome:`;
use `MATERIAL` abaixo, que mapeia cada conceito ao icone Material
equivalente, mantendo o mesmo vocabulario visual do resto da aplicacao.
"""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Corpo de cada icone (conteudo interno do <svg>, viewBox 0 0 24 24)
# ---------------------------------------------------------------------------

_BODIES: dict[str, str] = {
    # Marca e navegacao geral
    "brain": (
        '<path d="M12 5.5a3 3 0 0 0-5.7-1.3A2.8 2.8 0 0 0 4 7a2.9 2.9 0 0 0 .9 2.1'
        'A3 3 0 0 0 5 14.6a3.2 3.2 0 0 0 3.2 3.1 2.9 2.9 0 0 0 3.8 1.7Z"/>'
        '<path d="M12 5.5a3 3 0 0 1 5.7-1.3A2.8 2.8 0 0 1 20 7a2.9 2.9 0 0 1-.9 2.1'
        'A3 3 0 0 1 19 14.6a3.2 3.2 0 0 1-3.2 3.1 2.9 2.9 0 0 1-3.8 1.7Z"/>'
        '<path d="M12 5.5v14"/>'
    ),
    "squares-four": (
        '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/>'
        '<rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/>'
        '<rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/>'
        '<rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>'
    ),
    # Modulos
    "waveform": (
        '<path d="M3 12h1.6"/><path d="M7 7.5v9"/><path d="M10.3 4.5v15"/>'
        '<path d="M13.7 8.5v7"/><path d="M17 6v12"/><path d="M20.4 10v4"/>'
    ),
    "eye": (
        '<path d="M2.6 12S6 5.8 12 5.8 21.4 12 21.4 12 18 18.2 12 18.2 2.6 12 2.6 12Z"/>'
        '<circle cx="12" cy="12" r="3.1"/>'
    ),
    "microphone-stage": (
        '<path d="M12 2.8a3.4 3.4 0 0 1 3.4 3.4v1.2a3.4 3.4 0 0 1-6.8 0V6.2'
        'A3.4 3.4 0 0 1 12 2.8Z"/>'
        '<path d="M10.8 10.6 7.4 20.4"/><path d="M13.2 10.6l3.4 9.8"/>'
        '<path d="M9.1 17.4h5.8"/>'
    ),
    # Objetos de dominio
    "folders": (
        '<path d="M7 6.4V5.2a1.4 1.4 0 0 1 1.4-1.4h2.4l1.6 1.9h5.2A1.4 1.4 0 0 1 19 7.1v.9"/>'
        '<path d="M4.4 8h5.3l1.6 1.9h7.3a1.4 1.4 0 0 1 1.4 1.4v7.3a1.4 1.4 0 0 1-1.4 1.4'
        'H4.4A1.4 1.4 0 0 1 3 18.6V9.4A1.4 1.4 0 0 1 4.4 8Z"/>'
    ),
    "folder-open": (
        '<path d="M3 8.6V6.2a1.4 1.4 0 0 1 1.4-1.4h4.2l1.7 2h7.3a1.4 1.4 0 0 1 1.4 1.4v1.2"/>'
        '<path d="M3 8.6h18l-2 9.4a1.5 1.5 0 0 1-1.5 1.2H5.6A1.5 1.5 0 0 1 4 18Z"/>'
    ),
    "file-audio": (
        '<path d="M13.4 3.2H6.9a1.5 1.5 0 0 0-1.5 1.5v14.6a1.5 1.5 0 0 0 1.5 1.5h10.2'
        'a1.5 1.5 0 0 0 1.5-1.5V8.2Z"/>'
        '<path d="M13.4 3.2v5h5.2"/>'
        '<path d="M8.6 15.4v-2.2"/><path d="M11.2 16.8v-5"/><path d="M13.8 15.4v-2.2"/>'
    ),
    "note-pencil": (
        '<path d="M19 12.4v6.2a1.5 1.5 0 0 1-1.5 1.5H5.9a1.5 1.5 0 0 1-1.5-1.5V6.9'
        'a1.5 1.5 0 0 1 1.5-1.5h6.3"/>'
        '<path d="m15.4 4.6 4 4-6.6 6.6h-4v-4Z"/>'
    ),
    "qr-code": (
        '<rect x="3.6" y="3.6" width="6.2" height="6.2" rx="1"/>'
        '<rect x="14.2" y="3.6" width="6.2" height="6.2" rx="1"/>'
        '<rect x="3.6" y="14.2" width="6.2" height="6.2" rx="1"/>'
        '<path d="M14.2 14.2h2.6v2.6h-2.6Z"/><path d="M19 14.2h1.4"/>'
        '<path d="M20.4 17.6v2.8h-3.6"/>'
    ),
    "books": (
        '<path d="M4.4 4.8h4a1.2 1.2 0 0 1 1.2 1.2v13H5.6a1.2 1.2 0 0 1-1.2-1.2Z"/>'
        '<path d="M9.6 6h4a1.2 1.2 0 0 1 1.2 1.2v11.8H9.6Z"/>'
        '<path d="m15.9 7.4 3.3.7a1.2 1.2 0 0 1 .9 1.4l-1.9 9.1-4.2-.9"/>'
    ),
    # Analise
    "chart-line": (
        '<path d="M4 4v16h16"/><path d="m7 15.4 3.6-4.6 3 2.6 4.4-6"/>'
    ),
    "chart-bar": (
        '<path d="M4 4v16h16"/><path d="M8.2 20v-6.4"/><path d="M12 20V8.6"/>'
        '<path d="M15.8 20v-4.2"/><path d="M19.4 20v-9"/>'
    ),
    "sparkle": (
        '<path d="M12 3.4 13.7 9l5.6 1.7-5.6 1.7L12 18l-1.7-5.6L4.7 10.7 10.3 9Z"/>'
        '<path d="M18.4 16.6l.7 2.2 2.2.7-2.2.7-.7 2.2-.7-2.2-2.2-.7 2.2-.7Z"/>'
    ),
    "seal-check": (
        '<path d="m12 3 2.1 1.7 2.7-.2.9 2.5 2.3 1.4-.8 2.6.8 2.6-2.3 1.4-.9 2.5'
        '-2.7-.2L12 21l-2.1-1.7-2.7.2-.9-2.5L4 15.6l.8-2.6L4 10.4l2.3-1.4.9-2.5'
        '2.7.2Z"/>'
        '<path d="m9 12.2 2.1 2.1 4-4.2"/>'
    ),
    # Coleta via WhatsApp
    "address-book": (
        '<path d="M6.4 3.8h11.2a1.5 1.5 0 0 1 1.5 1.5v13.4a1.5 1.5 0 0 1-1.5 1.5H6.4'
        'a1.5 1.5 0 0 1-1.5-1.5V5.3a1.5 1.5 0 0 1 1.5-1.5Z"/>'
        '<circle cx="12" cy="10.2" r="2.2"/>'
        '<path d="M8.6 16.4a3.8 3.8 0 0 1 6.8 0"/>'
        '<path d="M3 8.2h2"/><path d="M3 12h2"/><path d="M3 15.8h2"/>'
    ),
    "megaphone": (
        '<path d="M4 10.4 17.6 5.2v13.6L4 13.6Z"/>'
        '<path d="M4 10.4H3.4A1.4 1.4 0 0 0 2 11.8v.4a1.4 1.4 0 0 0 1.4 1.4H4"/>'
        '<path d="M17.6 8.6a3.4 3.4 0 0 1 0 6.8"/>'
        '<path d="M8.2 12.2v6.4a1.6 1.6 0 0 0 3.2 0v-5.2"/>'
    ),
    "broadcast": (
        '<circle cx="12" cy="12" r="2.2"/>'
        '<path d="M8.1 8.1a5.5 5.5 0 0 0 0 7.8"/>'
        '<path d="M15.9 15.9a5.5 5.5 0 0 0 0-7.8"/>'
        '<path d="M5.4 5.4a9.3 9.3 0 0 0 0 13.2"/>'
        '<path d="M18.6 18.6a9.3 9.3 0 0 0 0-13.2"/>'
    ),
    "gear-six": (
        '<circle cx="12" cy="12" r="3.1"/>'
        '<path d="M12 2.8v2.6"/><path d="M12 18.6v2.6"/>'
        '<path d="M4.1 7.4 6.4 8.7"/><path d="M17.6 15.3l2.3 1.3"/>'
        '<path d="M4.1 16.6 6.4 15.3"/><path d="M17.6 8.7 19.9 7.4"/>'
    ),
    "plug": (
        '<path d="M9 3.4v4.2"/><path d="M15 3.4v4.2"/>'
        '<path d="M6.6 7.6h10.8v3.2a5.4 5.4 0 0 1-10.8 0Z"/>'
        '<path d="M12 16.2v4.4"/>'
    ),
    # Administracao
    "users-three": (
        '<circle cx="12" cy="9" r="2.6"/>'
        '<path d="M7.6 18.2a4.8 4.8 0 0 1 8.8 0"/>'
        '<circle cx="4.9" cy="10.8" r="2.1"/>'
        '<path d="M2 17.2a3.6 3.6 0 0 1 3.9-2.3"/>'
        '<circle cx="19.1" cy="10.8" r="2.1"/>'
        '<path d="M22 17.2a3.6 3.6 0 0 0-3.9-2.3"/>'
    ),
    "user-plus": (
        '<circle cx="10" cy="8.4" r="3.1"/>'
        '<path d="M4.4 19a5.9 5.9 0 0 1 11.2 0"/>'
        '<path d="M18 7.6v4.8"/><path d="M15.6 10h4.8"/>'
    ),
    "buildings": (
        '<path d="M4 20.4V8.2l6.4-2.6v14.8Z"/>'
        '<path d="M10.4 20.4V11h7.2a1.2 1.2 0 0 1 1.2 1.2v8.2Z"/>'
        '<path d="M6.6 11.4h1.4"/><path d="M6.6 14.6h1.4"/>'
        '<path d="M13.4 14.2h2.4"/><path d="M13.4 17.2h2.4"/>'
    ),
    # Acoes
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "upload-simple": (
        '<path d="M4.4 14.6v3.6a1.4 1.4 0 0 0 1.4 1.4h12.4a1.4 1.4 0 0 0 1.4-1.4v-3.6"/>'
        '<path d="M12 15.2V4.4"/><path d="m7.8 8.6 4.2-4.2 4.2 4.2"/>'
    ),
    "download-simple": (
        '<path d="M4.4 14.6v3.6a1.4 1.4 0 0 0 1.4 1.4h12.4a1.4 1.4 0 0 0 1.4-1.4v-3.6"/>'
        '<path d="M12 4.4v10.8"/><path d="m7.8 11 4.2 4.2 4.2-4.2"/>'
    ),
    "arrows-clockwise": (
        '<path d="M4.6 12a7.4 7.4 0 0 1 12.6-5.2l2.2 2.2"/>'
        '<path d="M19.4 4.6v4.4H15"/>'
        '<path d="M19.4 12a7.4 7.4 0 0 1-12.6 5.2l-2.2-2.2"/>'
        '<path d="M4.6 19.4V15H9"/>'
    ),
    "magnifying-glass": (
        '<circle cx="10.8" cy="10.8" r="6.2"/><path d="m15.4 15.4 4.4 4.4"/>'
    ),
    "sign-out": (
        '<path d="M9.4 4.4H5.8a1.4 1.4 0 0 0-1.4 1.4v12.4a1.4 1.4 0 0 0 1.4 1.4h3.6"/>'
        '<path d="M14.6 8.2 18.4 12l-3.8 3.8"/><path d="M18.4 12H8.6"/>'
    ),
    "trash": (
        '<path d="M4.6 6.8h14.8"/>'
        '<path d="M9 6.8V4.9a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1.9"/>'
        '<path d="M6.6 6.8 7.5 19a1.4 1.4 0 0 0 1.4 1.3h6.2a1.4 1.4 0 0 0 1.4-1.3l.9-12.2"/>'
        '<path d="M10.4 10.6v5.6"/><path d="M13.6 10.6v5.6"/>'
    ),
    "link": (
        '<path d="M9.6 14.4 14.4 9.6"/>'
        '<path d="M12.8 6.4 14.4 4.8a3.6 3.6 0 0 1 5.1 5.1l-1.6 1.6"/>'
        '<path d="M11.2 17.6 9.6 19.2a3.6 3.6 0 0 1-5.1-5.1l1.6-1.6"/>'
    ),
    "funnel": (
        '<path d="M4.4 5.2h15.2l-5.9 7v6.4l-3.4-1.9V12.2Z"/>'
    ),
    "clock-counter-clockwise": (
        '<path d="M12 7.4V12l3.4 2"/>'
        '<path d="M4.8 9.6A7.6 7.6 0 1 1 4.6 15"/>'
        '<path d="M4.6 5.2v4.4H9"/>'
    ),
    "database": (
        '<ellipse cx="12" cy="6.4" rx="7.4" ry="2.8"/>'
        '<path d="M4.6 6.4v11.2c0 1.5 3.3 2.8 7.4 2.8s7.4-1.3 7.4-2.8V6.4"/>'
        '<path d="M4.6 12c0 1.5 3.3 2.8 7.4 2.8s7.4-1.3 7.4-2.8"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="8.4"/><path d="M12 11v5.4"/>'
        '<path d="M12 7.8h.01"/>'
    ),
    "shield-check": (
        '<path d="M12 3.2 19.4 6v5.4c0 4.2-3 7.6-7.4 9.4-4.4-1.8-7.4-5.2-7.4-9.4V6Z"/>'
        '<path d="m8.8 11.8 2.2 2.2 4.2-4.4"/>'
    ),
    "caret-right": '<path d="m9.6 5.6 6.4 6.4-6.4 6.4"/>',
    "caret-down": '<path d="m5.6 9.6 6.4 6.4 6.4-6.4"/>',
    "caret-up-down": '<path d="m8 10 4-4 4 4"/><path d="m8 14 4 4 4-4"/>',
    "arrow-right": '<path d="M4.6 12h14.8"/><path d="m13.6 6.2 5.8 5.8-5.8 5.8"/>',
    "arrow-left": '<path d="M19.4 12H4.6"/><path d="m10.4 6.2-5.8 5.8 5.8 5.8"/>',
    "check": '<path d="m5.4 12.6 4.6 4.6 8.6-9.4"/>',
    "minus": '<path d="M5.4 12h13.2"/>',
    "x": '<path d="m6.2 6.2 11.6 11.6"/><path d="m17.8 6.2-11.6 11.6"/>',
    "play": '<path d="M7.6 4.8 19 12 7.6 19.2Z"/>',
    "list-bullets": (
        '<path d="M9 6.4h11"/><path d="M9 12h11"/><path d="M9 17.6h11"/>'
        '<path d="M4.6 6.4h.01"/><path d="M4.6 12h.01"/><path d="M4.6 17.6h.01"/>'
    ),
    "dots-three-vertical": (
        '<path d="M12 6.2h.01"/><path d="M12 12h.01"/><path d="M12 17.8h.01"/>'
    ),
    "sliders-horizontal": (
        '<path d="M4 7.6h9"/><path d="M17 7.6h3"/>'
        '<path d="M4 16.4h3"/><path d="M11 16.4h9"/>'
        '<circle cx="15" cy="7.6" r="2"/><circle cx="9" cy="16.4" r="2"/>'
    ),
}

FILLED = {"play", "sparkle", "funnel"}

# `st.Page` aceita apenas emoji ou ":material/nome:". Este mapa mantem o
# mesmo vocabulario do conjunto SVG dentro do menu lateral do Streamlit.
MATERIAL: dict[str, str] = {
    "brain": ":material/psychology:",
    "squares-four": ":material/grid_view:",
    "waveform": ":material/graphic_eq:",
    "eye": ":material/visibility:",
    "microphone-stage": ":material/mic:",
    "folders": ":material/folder_copy:",
    "folder-open": ":material/folder_open:",
    "file-audio": ":material/audio_file:",
    "note-pencil": ":material/edit_note:",
    "qr-code": ":material/qr_code_2:",
    "books": ":material/library_books:",
    "chart-line": ":material/show_chart:",
    "chart-bar": ":material/bar_chart:",
    "sparkle": ":material/auto_awesome:",
    "seal-check": ":material/verified:",
    "address-book": ":material/contacts:",
    "megaphone": ":material/campaign:",
    "broadcast": ":material/sensors:",
    "gear-six": ":material/settings:",
    "users-three": ":material/group:",
    "buildings": ":material/apartment:",
    "upload-simple": ":material/upload:",
    "list-bullets": ":material/list:",
}


def icon(name: str, size: int = 16, color: str = "currentColor") -> str:
    """Devolve o markup SVG inline de um icone. Vazio se o nome nao existir."""
    body = _BODIES.get(name)
    if body is None:
        return ""
    fill = color if name in FILLED else "none"
    stroke = "none" if name in FILLED else color
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
        'viewBox="0 0 24 24" fill="{f}" stroke="{k}" stroke-width="1.75" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
        'style="flex-shrink:0;vertical-align:-0.14em">{b}</svg>'
    ).format(s=size, f=fill, k=stroke, b=body)


def material(name: str, fallback: str = ":material/circle:") -> str:
    """Icone Material equivalente, para `st.Page(icon=...)`."""
    return MATERIAL.get(name, fallback)


def page_title(name: str, title: str, subtitle: str | None = None) -> None:
    """Titulo de pagina com icone, no lugar de `st.title("emoji Titulo")`."""
    st.markdown(
        '<div style="display:flex;align-items:center;gap:.6rem;'
        'margin:.2rem 0 .1rem">'
        '<span style="color:var(--nenc-accent-400,#b5abfc);display:flex">'
        "{i}</span>"
        '<h1 style="margin:0;font-size:1.75rem;font-weight:500;'
        'letter-spacing:-.018em">{t}</h1></div>'.format(
            i=icon(name, 26), t=title
        ),
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(
            '<p style="margin:.1rem 0 .9rem;font-size:.85rem;'
            'color:var(--nenc-muted,#9397ab)">{}</p>'.format(subtitle),
            unsafe_allow_html=True,
        )


def label(name: str, text: str, size: int = 15) -> str:
    """Rotulo curto com icone, para usar dentro de markdown."""
    return (
        '<span style="display:inline-flex;align-items:center;gap:.4rem">'
        "{i}{t}</span>".format(i=icon(name, size), t=text)
    )
