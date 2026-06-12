"""
Prosódia — Entrevistas.

Tela principal de consulta das entrevistas de um projeto:
- tabela robusta com busca e filtros
- métricas de qualidade e cobertura por entrevista
- ações de timeline, análise, download e exclusão
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from utils.prosodia_db import (
    init_db,
    get_project,
    get_audios_for_interviews,
    delete_audio,
)
from utils.prosodia_quality import status_badge

init_db()


_STATUS_LABEL = {
    "pass": "OK",
    "warn": "Atenção",
    "fail": "Problema",
    "pending": "Pendente",
}


def _status_text(status: str) -> str:
    s = status or "pending"
    return f"{status_badge(s)} {_STATUS_LABEL.get(s, s)}"


def _to_date(value: str) -> date:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return date.today()


# ------------------------------------------------------------------
# Verificar projeto selecionado
# ------------------------------------------------------------------
project_id = st.session_state.get("pros_project_id")
if not project_id:
    st.warning("Nenhum projeto selecionado. Volte à lista de projetos.")
    if st.button("← Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

project = get_project(project_id)
if not project:
    st.error("Projeto não encontrado.")
    if st.button("← Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
h1, h2, h3, h4, h5 = st.columns([4, 1, 1, 1, 1])
with h1:
    st.title(f"🗂️ Entrevistas — {project['name']}")
with h2:
    st.write("")
    if st.button("🧠 Análise Geral", width="stretch"):
        st.switch_page("modules/prosodia/analise_geral.py")
with h3:
    st.write("")
    if st.button("📤 Uploads", width="stretch"):
        st.switch_page("modules/prosodia/audios.py")
with h4:
    st.write("")
    if st.button("✏️ Editar", width="stretch"):
        st.switch_page("modules/prosodia/preparacao.py")
with h5:
    st.write("")
    if st.button("← Projetos", width="stretch"):
        st.switch_page("modules/prosodia/projetos.py")

st.divider()

# ------------------------------------------------------------------
# Dados
# ------------------------------------------------------------------
audios = get_audios_for_interviews(project_id)

if not audios:
    st.info("Nenhuma entrevista carregada ainda. Faça upload dos arquivos para começar.")
    if st.button("📤 Ir para Uploads", type="primary"):
        st.switch_page("modules/prosodia/audios.py")
    st.stop()

# ------------------------------------------------------------------
# Filtros
# ------------------------------------------------------------------
st.subheader("🔎 Busca e Filtros")

all_dates = [_to_date(a.get("created_at", "")) for a in audios]
min_date = min(all_dates) if all_dates else date.today()
max_date = max(all_dates) if all_dates else date.today()

f1, f2 = st.columns([2, 2])
with f1:
    search = st.text_input(
        "Buscar por sessão/ID",
        placeholder="Ex: entrevista_001, produtor_12...",
        key="en_search",
    ).strip().lower()
with f2:
    status_filter = st.multiselect(
        "Status Geral",
        options=["pass", "warn", "fail", "pending"],
        default=["pass", "warn", "fail", "pending"],
        format_func=lambda s: _status_text(s),
        key="en_status_filter",
    )

f3, f4, f5 = st.columns([2, 1, 1])
with f3:
    selected_period = st.date_input(
        "Data de criação",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        key="en_date_filter",
    )
with f4:
    ai_range = st.slider("Cobertura IA (%)", 0, 100, (0, 100), key="en_ai_range")
with f5:
    kw_range = st.slider("Cobertura Keywords (%)", 0, 100, (0, 100), key="en_kw_range")

if isinstance(selected_period, tuple) and len(selected_period) == 2:
    dt_start, dt_end = selected_period
elif isinstance(selected_period, list) and len(selected_period) == 2:
    dt_start, dt_end = selected_period[0], selected_period[1]
else:
    dt_start = dt_end = selected_period

filtered = []
for audio in audios:
    sid = str(audio.get("session_id", ""))
    audio_id_text = str(audio.get("id", ""))
    status = audio.get("quality_status", "pending")
    created_date = _to_date(audio.get("created_at", ""))
    ai_pct = float(audio.get("coverage_ai_pct", 0.0))
    kw_pct = float(audio.get("coverage_kw_pct", 0.0))

    if search and search not in sid.lower() and search not in audio_id_text:
        continue
    if status_filter and status not in status_filter:
        continue
    if created_date < dt_start or created_date > dt_end:
        continue
    if ai_pct < ai_range[0] or ai_pct > ai_range[1]:
        continue
    if kw_pct < kw_range[0] or kw_pct > kw_range[1]:
        continue

    filtered.append(audio)

st.caption(f"{len(filtered)} entrevista(s) encontrada(s) de {len(audios)} no projeto.")

# ------------------------------------------------------------------
# Tabela
# ------------------------------------------------------------------
selected_audio = None

if not filtered:
    st.warning("Nenhuma entrevista atende aos filtros selecionados.")
else:
    rows = []
    for a in filtered:
        cov_total = int(a.get("coverage_total", 0))
        ai_found = int(a.get("coverage_ai_found", 0))
        kw_found = int(a.get("coverage_kw_found", 0))

        rows.append({
            "Sessão": a.get("session_id", ""),
            "Data": str(a.get("created_at", ""))[:10],
            "Status Geral": _status_text(a.get("quality_status", "pending")),
            "✅ Checks OK": int(a.get("checks_ok", 0)),
            "⚠️ Alertas": int(a.get("checks_warn", 0)),
            "❌ Problemas": int(a.get("checks_fail", 0)),
            "🧠 IA cobertas": f"{ai_found}/{cov_total}",
            "🔎 Keywords cobertas": f"{kw_found}/{cov_total}",
            "IA %": round(float(a.get("coverage_ai_pct", 0.0)), 1),
            "Keywords %": round(float(a.get("coverage_kw_pct", 0.0)), 1),
            "Análises": int(a.get("n_analyses", 0)),
            "KB": "✅" if a.get("kb_ok") else "⚠️",
        })

    table_event = st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="en_interviews_table",
    )

    selected_rows = []
    if table_event:
        selection = getattr(table_event, "selection", None)
        if isinstance(selection, dict):
            selected_rows = selection.get("rows", [])
        elif selection is not None:
            selected_rows = getattr(selection, "rows", []) or []

    if selected_rows:
        selected_audio = filtered[int(selected_rows[0])]

# ------------------------------------------------------------------
# Ações da linha selecionada
# ------------------------------------------------------------------
st.divider()
st.subheader("⚙️ Ações da Linha Selecionada")

if not selected_audio:
    st.info("Selecione uma linha na tabela para abrir ou excluir a entrevista.")
else:
    selected_id = selected_audio["id"]

    st.caption(
        f"Entrevista selecionada: {selected_audio.get('session_id', '')} "
        f"({str(selected_audio.get('created_at', ''))[:10]})"
    )

    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        if st.button("📊 Abrir Timeline", width="stretch", key=f"en_tl_{selected_id}"):
            st.session_state["pros_audio_id"] = selected_id
            st.switch_page("modules/prosodia/audio_timeline.py")
    with ac2:
        if st.button("🤖 Abrir Análise", width="stretch", key=f"en_an_{selected_id}"):
            st.session_state["pros_audio_id"] = selected_id
            st.switch_page("modules/prosodia/audio_analise.py")
    with ac3:
        if st.button("🗑️ Excluir Entrevista", width="stretch", key=f"en_del_{selected_id}"):
            st.session_state[f"confirm_del_interview_{selected_id}"] = True

    if st.session_state.get(f"confirm_del_interview_{selected_id}"):
        st.warning(
            f"Excluir entrevista **{selected_audio.get('session_id', '')}**? Esta ação não pode ser desfeita."
        )
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("✅ Confirmar exclusão", width="stretch", key=f"en_del_yes_{selected_id}"):
                delete_audio(selected_id)
                st.session_state.pop(f"confirm_del_interview_{selected_id}", None)
                st.rerun()
        with dc2:
            if st.button("❌ Cancelar", width="stretch", key=f"en_del_no_{selected_id}"):
                st.session_state.pop(f"confirm_del_interview_{selected_id}", None)
                st.rerun()

    st.divider()
    d1, d2 = st.columns(2)
    with d1:
        if selected_audio.get("prosodia_json"):
            st.download_button(
                "⬇️ Download Prosódia JSON",
                data=selected_audio["prosodia_json"],
                file_name=f"Prosodia-{selected_audio.get('session_id', 'sessao')}.json",
                mime="application/json",
                width="stretch",
                key=f"en_dl_json_{selected_id}",
            )
    with d2:
        if selected_audio.get("transcricao_csv"):
            st.download_button(
                "⬇️ Download Transcrição CSV",
                data=selected_audio["transcricao_csv"],
                file_name=f"Transcricao-{selected_audio.get('session_id', 'sessao')}.csv",
                mime="text/csv",
                width="stretch",
                key=f"en_dl_csv_{selected_id}",
            )
