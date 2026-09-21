"""
Tema visual do NENC Insights.

Fonte única dos tokens de design (cores, raios, tipografia) e dos componentes
HTML que dependem deles. Estilo novo sai daqui — não de mais um bloco <style>
solto dentro de uma página.

Tokens derivados de DESIGN-sentry.md: canvas violeta-meia-noite, lima elétrica
como destaque escasso, rosa como pontuação secundária, e a família violeta para
chips e traços. A superfície é de produto (não marketing), então o corpo usa
entrelinha 1.5 e os rótulos de ação usam caixa alta com tracking de 0.2px.
"""

from html import escape

import streamlit as st

# --- Cores ---------------------------------------------------------------
PRIMARY = "#150f23"
INK_DEEP = "#1f1633"
ON_PRIMARY = "#ffffff"
ACCENT_LIME = "#c2ef4e"
ACCENT_PINK = "#fa7faa"
ACCENT_VIOLET = "#6a5fc1"
ACCENT_VIOLET_DEEP = "#422082"
ACCENT_VIOLET_MID = "#79628c"
SURFACE_CANVAS_DARK = "#1f1633"
SURFACE_NIGHT = "#150f23"
HAIRLINE_VIOLET = "#362d59"
ON_DARK_MUTED = "#bdb8c0"
RING_FOCUS = "#9dc1f5"

# --- Raios ---------------------------------------------------------------
ROUNDED_XS = "4px"
ROUNDED_SM = "6px"
ROUNDED_MD = "8px"
ROUNDED_XL = "12px"

# --- Estados semânticos --------------------------------------------------
# A paleta de origem não define verde/vermelho/amarelo e proíbe acentos fora
# do par lima+rosa, então os estados de conexão são mapeados dentro dela:
# lima = ativo, rosa = falha, cinza = ausente (não é alarme, é falta de setup).
# (cor, pulsa)
_TONES = {
    "online": (ACCENT_LIME, True),
    "offline": (ACCENT_PINK, False),
    "neutro": (ON_DARK_MUTED, False),
}

# Chips de catálogo: o token de tag do sistema, e o violeta profundo para
# distinguir a organização sem gastar lima — que fica reservada ao status.
_BADGE_TONES = {
    "tag": ACCENT_VIOLET_MID,
    "org": ACCENT_VIOLET_DEEP,
}


def _rgba(hex_color: str, alpha: float) -> str:
    """Converte '#rrggbb' em rgba() para os fundos translúcidos dos banners."""
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def _tone(tone: str) -> tuple[str, bool]:
    return _TONES.get(tone, _TONES["neutro"])


_FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Rubik:wght@400;500;600;700"
    "&family=Space+Grotesk:wght@500;600;700&display=swap');"
)

_ROOT_VARS = f""":root {{
    --nenc-primary: {PRIMARY};
    --nenc-ink-deep: {INK_DEEP};
    --nenc-on-primary: {ON_PRIMARY};
    --nenc-accent-lime: {ACCENT_LIME};
    --nenc-accent-pink: {ACCENT_PINK};
    --nenc-accent-violet: {ACCENT_VIOLET};
    --nenc-accent-violet-deep: {ACCENT_VIOLET_DEEP};
    --nenc-accent-violet-mid: {ACCENT_VIOLET_MID};
    --nenc-surface-night: {SURFACE_NIGHT};
    --nenc-hairline-violet: {HAIRLINE_VIOLET};
    --nenc-on-dark-muted: {ON_DARK_MUTED};
    --nenc-ring-focus: {RING_FOCUS};
    --nenc-rounded-xs: {ROUNDED_XS};
    --nenc-rounded-sm: {ROUNDED_SM};
    --nenc-rounded-md: {ROUNDED_MD};
    --nenc-rounded-xl: {ROUNDED_XL};
    --nenc-font-ui: 'Rubik', -apple-system, system-ui, 'Segoe UI', Helvetica, Arial, sans-serif;
    --nenc-font-display: 'Space Grotesk', 'Rubik', system-ui, sans-serif;
    --nenc-font-code: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}}"""


def css_variables(include_fonts: bool = True) -> str:
    """Tokens como CSS custom properties, para embutir em outro documento.

    Um iframe de ``st.components.v1.html`` não herda o estilo da página, então
    precisa carregar os tokens no próprio ``<style>`` para usar ``var(--nenc-*)``.
    """
    return f"{_FONT_IMPORT}\n{_ROOT_VARS}" if include_fonts else _ROOT_VARS


_CSS = f"""
{_FONT_IMPORT}

{_ROOT_VARS}

html, body, .stApp,
[data-testid="stAppViewContainer"],
[data-testid="stSidebar"] {{
    font-family: var(--nenc-font-ui);
}}

.stApp {{ line-height: 1.5; }}

/* O Streamlit define a fonte dos títulos por classe gerada
   (.st-emotion-cache-* h1), que vence um seletor de elemento puro. O nome da
   classe muda entre versões, então a saída é !important em vez de imitá-lo. */
.stApp h1, .stApp h2, .stApp h3,
.stApp h4, .stApp h5, .stApp h6 {{
    font-family: var(--nenc-font-display) !important;
    letter-spacing: 0;
}}
.stApp h1 {{ font-weight: 700 !important; }}
.stApp h2, .stApp h3, .stApp h4 {{ font-weight: 500 !important; }}

code, pre, kbd, samp {{ font-family: var(--nenc-font-code); }}

.stMarkdown a {{
    color: var(--nenc-accent-violet);
    text-decoration: underline;
}}

/* Cadência de console: rótulo de ação em caixa alta com tracking de 0.2px. */
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button {{
    border-radius: var(--nenc-rounded-md);
    font-family: var(--nenc-font-ui);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.2px;
    text-transform: uppercase;
}}

.stTextInput input,
.stTextArea textarea,
.stNumberInput input {{
    border-radius: var(--nenc-rounded-sm);
}}
.stTextInput input:focus,
.stTextArea textarea:focus,
.stNumberInput input:focus {{
    box-shadow: 0 0 0 3px var(--nenc-ring-focus);
}}

@keyframes nenc-pulse {{
    0% {{ transform: scale(0.9); opacity: 0.7; }}
    50% {{ transform: scale(1.1); opacity: 1; }}
    100% {{ transform: scale(0.9); opacity: 0.7; }}
}}
.nenc-pulse {{ animation: nenc-pulse 1.5s infinite; }}

.nenc-status {{
    display: flex;
    align-items: center;
    gap: 8px;
    height: 38px;
}}
.nenc-status__dot {{
    height: 10px;
    width: 10px;
    border-radius: 50%;
    display: inline-block;
    flex: none;
}}
.nenc-status__label {{
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.25px;
    text-transform: uppercase;
}}

.nenc-banner {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 15px;
    padding: 10px;
    border-radius: var(--nenc-rounded-md);
    border-left: 5px solid;
}}
.nenc-banner__dot {{
    height: 12px;
    width: 12px;
    border-radius: 50%;
    display: inline-block;
    flex: none;
}}
.nenc-banner__label {{
    font-weight: 600;
    letter-spacing: 0.25px;
}}

.nenc-badge {{
    display: inline-block;
    margin-left: 8px;
    padding: 2px 6px;
    border-radius: var(--nenc-rounded-xs);
    color: var(--nenc-on-primary);
    font-size: 10px;
    font-weight: 600;
    line-height: 1.8;
    letter-spacing: 0.25px;
}}
"""


def inject_theme() -> None:
    """Injeta os tokens e as classes dos componentes.

    Chamada uma vez por execução, no topo de ``app.py`` — o Streamlit refaz o
    DOM a cada rerun, então não há guarda de sessão: ela deve rodar sempre.
    """
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def status_pill(label: str, tone: str) -> str:
    """Ponto de status + rótulo, para uso inline numa coluna."""
    color, pulse = _tone(tone)
    dot_class = "nenc-status__dot nenc-pulse" if pulse else "nenc-status__dot"
    return (
        '<div class="nenc-status">'
        f'<span class="{dot_class}" style="background-color: {color};"></span>'
        f'<strong class="nenc-status__label" style="color: {color};">{escape(label)}</strong>'
        "</div>"
    )


def status_banner(label: str, tone: str) -> str:
    """Faixa de status com borda à esquerda, para painéis de diagnóstico."""
    color, pulse = _tone(tone)
    dot_class = "nenc-banner__dot nenc-pulse" if pulse else "nenc-banner__dot"
    return (
        f'<div class="nenc-banner" style="background-color: {_rgba(color, 0.15)};'
        f' border-left-color: {color};">'
        f'<span class="{dot_class}" style="background-color: {color};"></span>'
        f'<strong class="nenc-banner__label" style="color: {color};">{escape(label)}</strong>'
        "</div>"
    )


def badge(label: str, tone: str = "tag") -> str:
    """Chip pequeno para metadados ao lado de um título."""
    color = _BADGE_TONES.get(tone, _BADGE_TONES["tag"])
    return (
        f'<span class="nenc-badge" style="background-color: {color};">'
        f"{escape(label)}</span>"
    )
