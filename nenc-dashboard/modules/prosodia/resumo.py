"""
NencBoost — Resumo do projeto.

Primeira página da seção do projeto e destino de "Abrir" na lista de
projetos. Responde "como vai a coleta" (entradas por QR code e por hora) e "o
que os dados dizem" (emoção na voz, sentimento do texto e o índice combinado),
e leva às demais páginas do projeto.
"""

import html

import streamlit as st
from utils import auth, ui
from utils.icons import icon, material

user = auth.require_module("prosodia")

from utils import prosodia_db
from utils import prosodia_summary as summary
from utils.project_ui import active_project
from utils.prosodia_project_types import PROJECT_TYPE_LABELS, normalize_project_type

prosodia_db.init_db()
project = active_project(prosodia_db, "pros_project_id", "modules/prosodia/projetos.py")

ACCENT = "#9184d9"
ACCENT_600 = "#796cbf"
ACCENT_700 = "#5d5294"
LAVENDER = "#d2cefd"
NEUTRAL = "#595d6c"
ROSE = "#c9708b"
GREEN = "#7bc0a8"
EMOTION_COLORS = {"Alegria": ACCENT, "Neutro": NEUTRAL, "Tristeza": LAVENDER, "Raiva": ROSE}
SENTIMENT_COLORS = {"Positivo": ACCENT, "Neutro": NEUTRAL, "Negativo": ROSE}

PAGES = {
    "qr": "modules/prosodia/qr_codes.py",
    "audios": "modules/prosodia/entrevistas.py",
    "analise": "modules/prosodia/analise_geral.py",
    "monitor": "modules/prosodia/whatsapp_monitor.py",
    "dados": "modules/prosodia/preparacao.py",
    "uploads": "modules/prosodia/audios.py",
}


# ---------------------------------------------------------------------------
# Pedaços de HTML
# ---------------------------------------------------------------------------

def _pct(share: float) -> str:
    return "{:.0f}%".format(share * 100)


def _card_head(title: str, sub: str = "", tag: str = "") -> str:
    tag_html = (
        '<span style="font-size:.6rem;padding:.1rem .4rem;border-radius:4px;white-space:nowrap;'
        'border:1px dashed var(--nenc-accent-700);color:var(--nenc-accent-300)">{}</span>'.format(tag)
        if tag else ""
    )
    sub_html = '<div style="font-size:.7rem;color:var(--nenc-faint)">{}</div>'.format(sub) if sub else ""
    return (
        '<div style="display:flex;flex-direction:column;gap:.15rem;margin-bottom:.8rem">'
        '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:.3rem .5rem;font-size:.9rem;'
        'font-weight:600">{t}{g}</div>'
        "{s}</div>".format(t=title, g=tag_html, s=sub_html)
    )


def _kpi(icon_name: str, label: str, value: str, note: str, good: bool = False) -> str:
    return (
        '<div style="border:1px solid var(--nenc-border);border-radius:8px;padding:.9rem 1rem;'
        'display:flex;flex-direction:column;gap:.45rem">'
        '<div style="display:flex;align-items:center;gap:.4rem;font-size:.64rem;letter-spacing:.09em;'
        'text-transform:uppercase;color:var(--nenc-faint)">{i}{l}</div>'
        '<div style="font-size:2rem;font-weight:500;letter-spacing:-.02em;line-height:1">{v}</div>'
        '<div style="font-size:.7rem;color:{c}">{n}</div></div>'
    ).format(i=icon(icon_name, 13), l=label, v=value, n=note, c=GREEN if good else "var(--nenc-muted)")


def _qr_bars(ranking: dict) -> str:
    rows = []
    for index, row in enumerate(ranking["linhas"]):
        top = index == 0
        rows.append(
            '<div style="display:flex;flex-direction:column;gap:.3rem">'
            '<div style="display:flex;align-items:baseline;gap:.5rem;font-size:.75rem">'
            '<span style="color:{nc}">{n}</span>'
            '<span style="margin-left:auto;font-variant-numeric:tabular-nums">{e}</span>'
            '<span style="width:2.2rem;text-align:right;color:var(--nenc-faint);font-variant-numeric:tabular-nums">{p}</span></div>'
            '<div style="height:6px;border-radius:3px;background:rgba(233,233,237,.06)">'
            '<div style="width:{w:.1f}%;height:100%;border-radius:3px;background:{bc}"></div></div></div>'.format(
                nc="var(--nenc-text)" if top else "var(--nenc-muted)",
                n=html.escape(row["nome"]),
                e=row["entradas"],
                p=_pct(row["fatia"]),
                w=100 * row["entradas"] / ranking["maximo"],
                bc=ACCENT if top else ACCENT_600,
            )
        )
    footer = []
    if ranking["outros_qr"]:
        footer.append("Outros {} QR codes · {} entradas".format(ranking["outros_qr"], ranking["outros_entradas"]))
    if ranking["sem_qr"]:
        footer.append("Upload direto, sem QR · {} áudios".format(ranking["sem_qr"]))
    footer_html = (
        '<div style="display:flex;flex-direction:column;gap:.3rem;margin-top:.9rem;padding-top:.7rem;'
        'border-top:1px solid rgba(233,233,237,.08);font-size:.72rem;color:var(--nenc-faint)">{}</div>'.format(
            "".join("<span>{}</span>".format(line) for line in footer)
        )
        if footer else ""
    )
    return '<div style="display:flex;flex-direction:column;gap:.7rem">{}</div>{}'.format("".join(rows), footer_html)


def _hour_bars(hours) -> str:
    peak_hour = int(hours.idxmax())
    peak = int(hours.max())
    columns = len(hours)
    bars = "".join(
        '<div title="{h}h · {n}" style="height:{px}px;background:{c};border-radius:2px 2px 0 0"></div>'.format(
            h=hour, n=int(n), px=round(140 * n / peak), c=ACCENT if hour == peak_hour else ACCENT_700
        )
        for hour, n in hours.items()
    )
    labels = "".join(
        '<span style="color:{c}">{t}</span>'.format(
            c="var(--nenc-accent-300)" if hour == peak_hour else "var(--nenc-faint)",
            t="{}h".format(hour) if hour % 2 == 0 or hour == peak_hour else "",
        )
        for hour in hours.index
    )
    return (
        '<div style="display:flex;align-items:baseline;gap:.5rem;margin-bottom:.8rem">'
        '<span style="font-size:1.4rem;font-weight:500;letter-spacing:-.015em">{p}h</span>'
        '<span style="font-size:.75rem;color:var(--nenc-muted)">horário de pico · {n} entradas</span></div>'
        '<div style="height:140px;display:grid;grid-template-columns:repeat({c},minmax(0,1fr));gap:4px;'
        'align-items:end;border-bottom:1px solid rgba(233,233,237,.12)">{b}</div>'
        '<div style="display:grid;grid-template-columns:repeat({c},minmax(0,1fr));gap:4px;margin-top:.35rem;'
        'font-size:.62rem;text-align:center;font-variant-numeric:tabular-nums">{l}</div>'
    ).format(p=peak_hour, n=peak, c=columns, b=bars, l=labels)


def _donut(slices, big: str, small: str) -> str:
    """`slices`: (rótulo, fatia 0..1, cor). A legenda segue a ordem recebida."""
    stops, start = [], 0.0
    for position, (_, share, color) in enumerate(slices):
        end = 100.0 if position == len(slices) - 1 else start + share * 100
        stops.append("{} {:.2f}% {:.2f}%".format(color, start, end))
        start = end
    legend = "".join(
        '<div style="display:flex;align-items:center;gap:.45rem;font-size:.72rem;color:var(--nenc-muted)">'
        '<span style="width:8px;height:8px;border-radius:2px;background:{c};flex:none"></span>{l}'
        '<span style="margin-left:auto;color:var(--nenc-text);font-variant-numeric:tabular-nums">{p}</span></div>'.format(
            c=color, l=label, p=_pct(share)
        )
        for label, share, color in slices
    )
    return (
        '<div style="display:flex;align-items:center;gap:1rem">'
        '<div style="position:relative;width:104px;height:104px;flex:none;border-radius:50%;'
        'background:conic-gradient({g})">'
        '<div style="position:absolute;inset:27px;border-radius:50%;background:var(--nenc-bg);display:flex;'
        'flex-direction:column;align-items:center;justify-content:center">'
        '<span style="font-size:1rem;font-weight:500;line-height:1">{b}</span>'
        '<span style="font-size:.52rem;letter-spacing:.07em;text-transform:uppercase;color:var(--nenc-faint)">{s}</span>'
        '</div></div>'
        '<div style="display:flex;flex-direction:column;gap:.4rem;flex:1;min-width:0">{l}</div></div>'
    ).format(g=",".join(stops), b=big, s=small, l=legend)


@st.cache_data(show_spinner="Lendo os sinais do projeto…", ttl=600)
def _signals(project_id: int, version: tuple) -> dict:
    tr_df, sinc_df = summary.load_project_signals(project_id)
    return {
        "emocoes": summary.emotion_shares(sinc_df),
        "texto": summary.text_sentiment(tr_df, sinc_df),
        "divergencias": summary.divergences(sinc_df),
        "indice": summary.combined_index(sinc_df),
    }


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

ui.inject_theme()
ui.breadcrumb("NencBoost", project["name"], "Resumo")

entries = summary.project_entries(project["id"])
numbers = summary.kpis(entries)
ranking = summary.qr_ranking(entries)

api_id = project.get("api_project_id")
type_label = PROJECT_TYPE_LABELS.get(normalize_project_type(project.get("tipo_projeto")), "")
meta = [part for part in (
    type_label,
    "{} QR codes".format(ranking["qr_distintos"]) if ranking["qr_distintos"] else "",
    "{} áudios".format(summary.number_text(numbers["total"])),
    "criado em {}".format(str(project.get("created_at") or "")[:10]),
) if part]

col_title, col_qr, col_ai = st.columns([5, 1.6, 1.3], vertical_alignment="center")
with col_title:
    st.markdown(
        '<div style="display:flex;flex-direction:column;gap:.35rem;margin-bottom:.4rem">'
        '<div style="display:flex;align-items:center;gap:.7rem;flex-wrap:wrap">'
        '<h1 style="margin:0;font-size:1.75rem;font-weight:500;letter-spacing:-.018em">{n}</h1>{c}</div>'
        '<div style="font-size:.82rem;color:var(--nenc-muted)">{m}</div></div>'.format(
            n=html.escape(project["name"]),
            c=ui.status_chip("plug", "API #{}".format(api_id), tone="accent") if api_id
            else ui.status_chip("plug", "sem API"),
            m=" · ".join(meta),
        ),
        unsafe_allow_html=True,
    )
with col_qr:
    st.page_link(PAGES["qr"], label="Gerenciar QR codes", icon=material("qr-code"))
with col_ai:
    st.page_link(PAGES["analise"], label="Análise Geral", icon=material("sparkle"))

if entries.empty:
    st.info("Ainda não há áudios neste projeto. Cadastre um QR code ou envie áudios em Uploads.")
    c1, c2, _ = st.columns([1, 1, 3])
    c1.page_link(PAGES["qr"], label="QR codes", icon=material("qr-code"))
    c2.page_link(PAGES["uploads"], label="Uploads", icon=material("upload-simple"))
    st.stop()

version = (len(entries), str(entries["entrou_em"].max()), int(entries["n_analyses"].sum()))
signals = _signals(project["id"], version)

divergence = signals["divergencias"]
st.markdown(
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.75rem;margin:.6rem 0 1rem">'
    "{}{}{}{}</div>".format(
        _kpi("qr-code", "Entradas por QR", summary.number_text(numbers["entradas_qr"]),
             "+{} nas últimas 24h".format(numbers["entradas_24h"]) if numbers["entradas_24h"] else "nenhuma nas últimas 24h",
             good=bool(numbers["entradas_24h"])),
        _kpi("sparkle", "Áudios analisados", summary.number_text(numbers["analisados"]),
             "de {} no projeto".format(summary.number_text(numbers["total"]))),
        _kpi("clock-counter-clockwise", "Duração média", summary.duration_text(numbers["duracao_media"]), "por áudio"),
        _kpi("arrows-left-right", "Voz × texto",
             str(divergence[0]) if divergence else "—",
             "divergências em {} áudios".format(divergence[1]) if divergence else "sem sentimento do texto"),
    ),
    unsafe_allow_html=True,
)

col_rank, col_hour = st.columns([1, 1.15], gap="small")

with col_rank:
    with st.container(border=True):
        if ranking["total"]:
            st.markdown(
                _card_head(
                    "Entradas por QR code",
                    "{} QR codes · {} mais usados".format(ranking["qr_distintos"], len(ranking["linhas"]))
                    if ranking["qr_distintos"] > len(ranking["linhas"])
                    else "{} QR codes".format(ranking["qr_distintos"]),
                ) + _qr_bars(ranking),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_card_head("Entradas por QR code"), unsafe_allow_html=True)
            st.caption("Nenhum áudio deste projeto entrou por QR code.")
        st.page_link(PAGES["qr"], label="Ver todos os QR codes", icon=":material/arrow_forward:")

with col_hour:
    with st.container(border=True):
        h_title, h_toggle = st.columns([1.3, 1], vertical_alignment="center")
        h_title.markdown(_card_head("Entradas por hora"), unsafe_allow_html=True)
        period = h_toggle.segmented_control(
            "Período", ["Período todo", "Hoje"], default="Período todo",
            key="pros_resumo_periodo", label_visibility="collapsed",
        ) or "Período todo"
        hours = summary.entries_by_hour(entries, only_today=period == "Hoje")
        if hours.empty or not hours.sum():
            st.caption("Nenhuma entrada por QR code {}.".format("hoje" if period == "Hoje" else "no projeto"))
        else:
            st.markdown(_hour_bars(hours), unsafe_allow_html=True)
        with_qr = entries[entries["qr_code_name"] != ""]
        fallback = int((~with_qr["hora_real"]).sum())
        st.caption(
            "Hora de chegada da mensagem no WhatsApp."
            if not fallback
            else "Hora de chegada no WhatsApp; {} entrada(s) importadas antes do registro dessa hora usam a hora da importação.".format(fallback)
        )

col_emo, col_text, col_index = st.columns(3, gap="small")

with col_emo:
    with st.container(border=True):
        emotions = signals["emocoes"]
        if emotions:
            top_label, top_share = emotions[0]
            st.markdown(
                _card_head("Emoção na voz", "classe predominante, % dos segmentos")
                + _donut([(label, share, EMOTION_COLORS.get(label, NEUTRAL)) for label, share in emotions],
                         _pct(top_share), top_label.lower()),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_card_head("Emoção na voz"), unsafe_allow_html=True)
            st.caption("Nenhum áudio com as categorias de emoção do Sincronizado.")

with col_text:
    with st.container(border=True):
        sentiment = signals["texto"]
        if sentiment:
            st.markdown(
                _card_head("Sentimento do texto", "% do tempo de fala")
                + _donut([(label, share, SENTIMENT_COLORS[label]) for label, share in sentiment["fatias"]],
                         summary.signed_text(sentiment["media"]), "média"),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_card_head("Sentimento do texto"), unsafe_allow_html=True)
            st.caption("A transcrição deste projeto não traz sentimento do texto.")

with col_index:
    with st.container(border=True):
        combined = signals["indice"]
        # Mesma fórmula da Análise Geral: a voz é medida contra o projeto, então
        # o número compara áudios entre si e não vale como medida absoluta.
        if combined:
            st.markdown(
                _card_head("Índice combinado de sentimento", "texto + voz, % do tempo de fala",
                           tag="relativo ao projeto")
                + _donut([(label, share, SENTIMENT_COLORS[label]) for label, share in combined["fatias"]],
                         summary.signed_text(combined["media"]), "média"),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(_card_head("Índice combinado de sentimento", tag="relativo ao projeto"),
                        unsafe_allow_html=True)
            st.caption("Precisa de sentimento do texto e de valência vocal nos mesmos trechos.")

st.markdown(
    '<div style="font-size:.64rem;letter-spacing:.09em;text-transform:uppercase;color:var(--nenc-faint);'
    'margin:1.1rem 0 .4rem">Continuar em</div>',
    unsafe_allow_html=True,
)
doors = (
    ("audios", "Áudios", "list-bullets", "Transcrição, timeline e análise de cada entrada"),
    ("analise", "Análise Geral", "chart-bar", "Gráficos do projeto e leitura por IA"),
    ("monitor", "Monitor", "broadcast", "Áudios chegando pelo WhatsApp"),
    ("dados", "Dados do Projeto", "note-pencil", "Perguntas, briefing e contexto da análise"),
)
for column, (key, label, icon_name, description) in zip(st.columns(4, gap="small"), doors):
    with column:
        with st.container(border=True):
            st.page_link(PAGES[key], label=label, icon=material(icon_name))
            st.caption(description)
