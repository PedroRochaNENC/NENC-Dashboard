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
# Sincronização com WhatsApp API
#
# Os áudios na API chegam via webhook do WhatsApp e NÃO estão
# obrigatoriamente vinculados a uma campanha. A sincronização pode:
# - Filtrar por telefones da campanha vinculada (se houver)
# - Importar TODOS os áudios processados (se não houver campanha)
# ------------------------------------------------------------------
from utils.whatsapp_api_client import is_configured as wa_configured

if wa_configured():
    campaign_id = project.get("whatsapp_campaign_id")

    sync_label = "🔄 Sincronizar com WhatsApp"
    if campaign_id:
        sync_label += f" (Campanha #{campaign_id})"
    else:
        sync_label += " (Todos os áudios)"

    if st.button(sync_label, type="secondary", key="wa_sync_btn"):
        from utils.whatsapp_api_client import (
            fetch_audios_for_sync,
            get_audio_result,
            map_api_result_to_all_formats,
            get_existing_whatsapp_message_ids,
        )

        try:
            with st.spinner("Buscando áudios na API do WhatsApp..."):
                api_audios = fetch_audios_for_sync(campaign_id=campaign_id)
                existing_ids = get_existing_whatsapp_message_ids(project_id)

                # Filtrar apenas áudios novos (não sincronizados)
                new_audios = [
                    a for a in api_audios
                    if a.get("whatsapp_message_id")
                    and a["whatsapp_message_id"] not in existing_ids
                ]

            if not new_audios:
                st.info("✅ Nenhum áudio novo encontrado para sincronizar.")
            else:
                # Importações necessárias para análise automática
                from utils.prosodia_db import (
                    create_audio,
                    get_project_questions,
                    save_analysis,
                    save_quality_check,
                    update_audio_openai_ids,
                )
                from utils.prosodia_quality import (
                    run_quality_checks,
                    check_question_coverage_keywords,
                    check_question_coverage_ai,
                    merge_coverage,
                    compute_overall_status,
                )
                from utils.prosodia_prompts import (
                    PROSODIA_SYSTEM_PROMPT,
                    build_prosodia_user_prompt,
                )
                from utils.ai_provider import (
                    get_openai_client,
                    get_prosodia_vector_store_id,
                    create_analysis as ai_create_analysis,
                )
                import io as _io

                questions = get_project_questions(project_id)
                openai_client = get_openai_client()
                vs_id = get_prosodia_vector_store_id()

                total = len(new_audios)
                synced = 0
                progress = st.progress(0, text=f"Sincronizando 0/{total}…")

                for idx, api_audio in enumerate(new_audios):
                    wa_msg_id = api_audio.get("whatsapp_message_id", "")
                    audio_api_id = api_audio["id"]
                    contact_phone = api_audio.get("contact_phone", "desconhecido")
                    session_id = f"wa_{contact_phone}_{audio_api_id}"

                    progress.progress(idx / total, text=f"Processando {session_id}…")

                    # Baixar resultado (DevAIce + Whisper)
                    try:
                        result_json = get_audio_result(audio_api_id)
                    except Exception as e:
                        st.warning(f"[{session_id}] Falha ao baixar resultado: {e}")
                        continue

                    if not result_json:
                        st.warning(f"[{session_id}] Resultado vazio, pulando.")
                        continue

                    # Converter para JSON de Prosódia, CSV de Transcrição e Sincronizado
                    json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result_json, session_id)

                    # Salvar no banco
                    audio_id = create_audio(
                        project_id=project_id,
                        session_id=session_id,
                        prosodia_json=json_bytes,
                        transcricao_csv=csv_bytes,
                        sincronizado_csv=sinc_bytes,
                        whatsapp_message_id=wa_msg_id,
                    )

                    # -- Upload OpenAI KB --
                    file_id_prosodia = None
                    file_id_transcricao = None
                    if openai_client:
                        try:
                            if json_bytes:
                                fp = openai_client.files.create(
                                    file=(f"Prosodia-{session_id}.json", _io.BytesIO(json_bytes), "application/json"),
                                    purpose="assistants",
                                )
                                file_id_prosodia = fp.id
                                if vs_id:
                                    openai_client.vector_stores.files.create(
                                        vector_store_id=vs_id, file_id=fp.id
                                    )
                            if csv_bytes:
                                fc = openai_client.files.create(
                                    file=(f"Transcricao-{session_id}.csv", _io.BytesIO(csv_bytes), "text/csv"),
                                    purpose="assistants",
                                )
                                file_id_transcricao = fc.id
                                if vs_id:
                                    openai_client.vector_stores.files.create(
                                        vector_store_id=vs_id, file_id=fc.id
                                    )
                            update_audio_openai_ids(audio_id, file_id_prosodia, file_id_transcricao)
                        except Exception as e:
                            st.warning(f"[{session_id}] Falha no upload para KB: {e}")

                    # Parse dos dados para análise usando o loader padrão
                    class _BytesFile:
                        def __init__(self, data: bytes, name: str):
                            self._buf = _io.BytesIO(data)
                            self.name = name

                        def read(self):
                            return self._buf.read()

                        def seek(self, pos):
                            return self._buf.seek(pos)

                    from utils.prosodia_loader import load_prosodia_from_uploads
                    json_files = [_BytesFile(json_bytes, f"Prosodia-{session_id}.json")] if json_bytes else []
                    csv_files = [_BytesFile(csv_bytes, f"Transcricao-{session_id}.csv")] if csv_bytes else []
                    sinc_files = [_BytesFile(sinc_bytes, f"Sincronizado-{session_id}.csv")] if sinc_bytes else []

                    parsed = load_prosodia_from_uploads(
                        json_files=json_files,
                        csv_files=csv_files,
                        sincronizado_files=sinc_files,
                    )
                    vad_df: pd.DataFrame = parsed.get("vad", pd.DataFrame())
                    tr_df: pd.DataFrame = parsed.get("transcricao", pd.DataFrame())

                    sinc_df = pd.DataFrame()
                    if sinc_bytes:
                        try:
                            sinc_df = pd.read_csv(_io.BytesIO(sinc_bytes))
                        except Exception:
                            pass

                    # -- Análise automática de IA --
                    proj_ctx = {
                        "nome": project.get("name", ""),
                        "especialidade": project.get("especialidade", ""),
                        "historico": project.get("historico", ""),
                        "problemas": project.get("problemas", ""),
                    }

                    tables_lines = []
                    if not vad_df.empty and "duration" in vad_df.columns:
                        total_s = vad_df["duration"].sum()
                        n_segs = len(vad_df)
                        tables_lines.append(
                            f"VAD: {n_segs} segmentos, {total_s:.1f}s de fala total."
                        )
                    if not tr_df.empty and "SpeakerName" in tr_df.columns:
                        by_spk = (
                            tr_df.groupby("SpeakerName")
                            .agg(msgs=("Text", "count"), words=("word_count", "sum"))
                            .reset_index()
                        )
                        tables_lines.append(
                            "Participação por locutor:\n" + by_spk.to_string(index=False)
                        )

                    tables_text = "\n\n".join(tables_lines)
                    transcript_sample = (
                        " ".join(tr_df["Text"].fillna("").astype(str).tolist())[:3000]
                        if not tr_df.empty and "Text" in tr_df.columns
                        else ""
                    )

                    analysis_result = {"text": "", "citations": []}
                    try:
                        if openai_client:
                            user_prompt = build_prosodia_user_prompt(
                                tables_text, proj_ctx, transcript_sample
                            )
                            analysis_result = ai_create_analysis(
                                system_prompt=PROSODIA_SYSTEM_PROMPT,
                                user_prompt=user_prompt,
                                model="gpt-4.1-mini",
                                vector_store_id=vs_id,
                                temperature=0.5,
                                max_tokens=3000,
                            )
                    except Exception as e:
                        st.warning(f"[{session_id}] Falha na análise de IA: {e}")

                    if analysis_result["text"]:
                        save_analysis(
                            audio_id=audio_id,
                            model="gpt-4.1-mini",
                            analysis_text=analysis_result["text"],
                            citations=analysis_result["citations"],
                        )

                    # -- Verificação de qualidade --
                    quality_checks = run_quality_checks(vad_df, tr_df, sinc_df)
                    coverage_kw = check_question_coverage_keywords(tr_df, questions)
                    coverage_ai = []
                    if openai_client and questions and transcript_sample:
                        try:
                            coverage_ai = check_question_coverage_ai(
                                transcript_sample,
                                questions,
                                openai_client,
                                model="gpt-4.1-mini",
                            )
                        except Exception:
                            pass

                    coverage_merged = (
                        merge_coverage(coverage_kw, coverage_ai)
                        if coverage_ai
                        else coverage_kw
                    )
                    overall = compute_overall_status(quality_checks)

                    save_quality_check(
                        audio_id=audio_id,
                        overall_status=overall,
                        checks=quality_checks,
                        coverage=coverage_merged,
                    )

                    synced += 1
                    progress.progress((idx + 1) / total, text=f"{session_id} concluído.")

                progress.empty()
                st.success(f"✅ {synced} áudio(s) sincronizado(s) com sucesso!")
                st.rerun()

        except Exception as e:
            st.error(f"Erro durante sincronização: {e}")

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
