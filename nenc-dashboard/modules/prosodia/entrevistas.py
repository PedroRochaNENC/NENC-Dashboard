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
from utils import auth

auth.require_module("prosodia")

from utils.prosodia_db import (
    init_db,
    get_project,
    get_audios_for_interviews,
    delete_audio,
)
from utils.prosodia_quality import status_badge
from utils.organization_data import claim_external_resource, list_external_resources

init_db()


_STATUS_LABEL = {
    "pass": "OK",
    "warn": "Atenção",
    "fail": "Problema",
    "pending": "Pendente",
    "processing": "Processando",
    "failed": "Falhou",
}


def _status_text(status: str) -> str:
    s = status or "pending"
    if s == "failed":
        return f"❌ {_STATUS_LABEL.get(s, s)}"
    return f"{status_badge(s)} {_STATUS_LABEL.get(s, s)}"


def _to_date(value: str) -> date:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return date.today()


def _normalize_phone(value) -> str:
    return "".join(filter(str.isdigit, str(value or "")))


def _owned_contact_phones() -> set[str]:
    return {
        _normalize_phone(resource["metadata"].get("phone"))
        for resource in list_external_resources("whatsapp_contact")
        if _normalize_phone(resource["metadata"].get("phone"))
    }


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
    st.session_state.pop("pros_project_id", None)
    st.error("Projeto não encontrado.")
    if st.button("← Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

# Parse thresholds customizados se existirem
thresholds = None
if project and project.get("quality_thresholds"):
    try:
        import json
        thresholds = json.loads(project["quality_thresholds"])
    except Exception:
        pass

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
# Sincronização com WhatsApp API (em Segundo Plano)
# ------------------------------------------------------------------
from utils.whatsapp_api_client import is_configured as wa_configured
from utils.whatsapp_sync import get_sync_job_status, is_sync_running, start_background_sync

sync_status = get_sync_job_status(project_id)
if sync_status:
    s_state = sync_status.get("status")
    s_prog = sync_status.get("progress", "")
    s_comp = sync_status.get("completed", 0)
    s_tot = sync_status.get("total", 0)

    if s_state == "running":
        st.info(f"🔄 **Sincronização em segundo plano em andamento...** ({s_comp}/{s_tot}) — *{s_prog}*")
    elif s_state == "completed":
        st.success(f"✅ **{s_prog}**")
    elif s_state == "failed":
        st.error(f"❌ **Falha na sincronização:** {sync_status.get('error')}")

if wa_configured():
    campaign_id = project.get("whatsapp_campaign_id")
    api_project_id = project.get("api_project_id")

    sync_label = "🔄 Sincronizar com WhatsApp"
    if api_project_id:
        sync_label += f" (Projeto API #{api_project_id})"
    elif campaign_id:
        sync_label += f" (Campanha #{campaign_id})"
    else:
        sync_label += " (associe uma campanha ou projeto API)"

    if is_sync_running(project_id):
        st.button("🔄 Sincronização em Andamento...", disabled=True, key="wa_sync_btn_disabled")
    else:
        if st.button(sync_label, type="secondary", key="wa_sync_btn"):
            active_org_id = auth.active_organization_id(user)
            if api_project_id:
                claim_external_resource(
                    "whatsapp_api_project",
                    api_project_id,
                    {"project_id": project_id},
                )
            elif campaign_id:
                claim_external_resource(
                    "whatsapp_campaign",
                    campaign_id,
                    {"project_id": project_id},
                )

            started = start_background_sync(project_id, active_org_id)
            if started:
                st.toast("🚀 Sincronização iniciada em segundo plano!")
                st.rerun()
            else:
                st.warning("Já existe uma sincronização em andamento para este projeto.")

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
        options=["pass", "warn", "fail", "pending", "processing", "failed"],
        default=["pass", "warn", "fail", "pending", "processing", "failed"],
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
        status = a.get("quality_status", "pending")
        is_processing = status in ("pending", "processing", "running")
        is_failed = status == "failed"

        cov_total = int(a.get("coverage_total", 0))
        ai_found = int(a.get("coverage_ai_found", 0))
        kw_found = int(a.get("coverage_kw_found", 0))

        qr_code_label = a.get("qr_code_name") or ("Upload Direto" if not str(a.get("session_id", "")).startswith("wa_") else "Geral")

        rows.append({
            "Sessão": a.get("session_id", ""),
            "QR Code": qr_code_label,
            "Data": str(a.get("created_at", ""))[:10],
            "Duração": a.get("duration_str", "00:00") if not (is_processing or is_failed) else "—",
            "Status Geral": _status_text(status),
            "✅ Checks OK": int(a.get("checks_ok", 0)) if not (is_processing or is_failed) else "—",
            "⚠️ Alertas": int(a.get("checks_warn", 0)) if not (is_processing or is_failed) else "—",
            "❌ Problemas": int(a.get("checks_fail", 0)) if not (is_processing or is_failed) else "—",
            "🧠 IA cobertas": f"{ai_found}/{cov_total}" if not (is_processing or is_failed) else "—",
            "🔎 Keywords cobertas": f"{kw_found}/{cov_total}" if not (is_processing or is_failed) else "—",
            "IA %": round(float(a.get("coverage_ai_pct", 0.0)), 1) if not (is_processing or is_failed) else "—",
            "Keywords %": round(float(a.get("coverage_kw_pct", 0.0)), 1) if not (is_processing or is_failed) else "—",
            "Análises": int(a.get("n_analyses", 0)) if not (is_processing or is_failed) else "—",
            "KB": ("✅" if a.get("kb_ok") else "⚠️") if not (is_processing or is_failed) else "⏳",
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

    is_wa = str(selected_audio.get("session_id", "")).startswith("wa_")
    if is_wa:
        ac1, ac2, ac3, ac4 = st.columns(4)
    else:
        ac1, ac2, ac4 = st.columns(3)
        ac3 = None

    status = selected_audio.get("quality_status", "pending")
    is_processing = status in ("pending", "processing", "running")
    is_failed = status == "failed"

    with ac1:
        help_msg_tl = "A timeline estará disponível assim que o processamento for concluído." if (is_processing or is_failed) else ""
        if st.button("📊 Abrir Timeline", width="stretch", key=f"en_tl_{selected_id}", disabled=is_processing or is_failed, help=help_msg_tl):
            st.session_state["pros_audio_id"] = selected_id
            st.switch_page("modules/prosodia/audio_timeline.py")
    with ac2:
        help_msg_an = "A análise estará disponível assim que o processamento for concluído." if (is_processing or is_failed) else ""
        if st.button("🤖 Abrir Análise", width="stretch", key=f"en_an_{selected_id}", disabled=is_processing or is_failed, help=help_msg_an):
            st.session_state["pros_audio_id"] = selected_id
            st.switch_page("modules/prosodia/audio_analise.py")
            
    if is_wa and ac3 is not None:
        with ac3:
            if st.button("🔄 Reprocessar", width="stretch", key=f"en_reproc_{selected_id}", help="Solicitar reprocessamento da transcrição e NencLex via WhatsApp API"):
                parts = selected_audio.get("session_id", "").split("_")
                if len(parts) >= 3:
                    try:
                        audio_api_id = int(parts[-1])
                        
                        # 1. Enviar requisição de reprocessamento
                        with st.spinner("Solicitando reprocessamento na API..."):
                            from utils.whatsapp_api_client import reprocess_audio as api_reprocess_audio, get_audio_status, get_audio_result, map_api_result_to_all_formats
                            api_reprocess_audio(audio_api_id)
                        
                        # 2. Polling status
                        import time
                        status_container = st.empty()
                        
                        start_time = time.time()
                        timeout = 300  # 5 minutos
                        success = False
                        
                        while time.time() - start_time < timeout:
                            status_info = get_audio_status(audio_api_id)
                            job_status = status_info.get("status", "pending")
                            
                            if job_status == "done":
                                success = True
                                break
                            elif job_status == "failed":
                                st.error(f"Erro no processamento da API: {status_info.get('error_msg') or 'Falha desconhecida'}")
                                break
                            
                            status_container.info(f"⏳ Processando na API (status: {job_status.upper()}). Por favor, aguarde...")
                            time.sleep(3)
                        
                        if success:
                            status_container.success("✅ Processamento na API concluído! Atualizando dados locais...")
                            
                            # 3. Baixar resultados
                            result_json = get_audio_result(audio_api_id)
                            if result_json:
                                json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result_json, selected_audio["session_id"])
                                
                                # 4. Atualizar blobs locais no SQLite
                                from utils.prosodia_db import update_audio_content
                                update_audio_content(selected_id, json_bytes, csv_bytes, sinc_bytes)
                                
                                # 5. Limpar cache do Streamlit
                                st.cache_data.clear()
                                
                                # 6. Recarregar dados locais
                                import pandas as pd
                                import io
                                from utils.prosodia_loader import load_prosodia_from_uploads
                                class _BF:
                                    def __init__(self, data, name):
                                        self._buf = io.BytesIO(data)
                                        self.name = name
                                    def read(self): return self._buf.read()
                                    def seek(self, p): return self._buf.seek(p)
                                
                                parsed_new = load_prosodia_from_uploads(
                                    json_files=[_BF(json_bytes, f"Prosodia-{selected_audio['session_id']}.json")] if json_bytes else [],
                                    csv_files=[_BF(csv_bytes, f"Transcricao-{selected_audio['session_id']}.csv")] if csv_bytes else [],
                                    sincronizado_files=[_BF(sinc_bytes, f"Sincronizado-{selected_audio['session_id']}.csv")] if sinc_bytes else [],
                                )
                                new_vad_df = parsed_new.get("vad", pd.DataFrame())
                                new_tr_df = parsed_new.get("transcricao", pd.DataFrame())
                                new_sinc_df = pd.DataFrame()
                                if sinc_bytes:
                                    try:
                                        new_sinc_df = pd.read_csv(io.BytesIO(sinc_bytes))
                                    except Exception:
                                        pass
                                
                                new_transcript_text = " ".join(new_tr_df["Text"].fillna("").astype(str).tolist()) if not new_tr_df.empty and "Text" in new_tr_df.columns else ""
                                
                                # 7. Atualizar Qualidade
                                status_container.info("🔄 Atualizando verificação de qualidade...")
                                from utils.prosodia_db import get_project_questions, save_quality_check, save_analysis
                                from utils.prosodia_quality import run_quality_checks, check_question_coverage_keywords, check_question_coverage_ai, merge_coverage, compute_overall_status
                                from utils.prosodia_prompts import PROSODIA_SYSTEM_PROMPT, build_prosodia_user_prompt
                                from utils.ai_provider import get_openai_client, get_prosodia_vector_store_id, create_analysis as ai_create_analysis
                                
                                questions = get_project_questions(project_id)
                                openai_client = get_openai_client()
                                vs_id = get_prosodia_vector_store_id()
                                
                                new_checks = run_quality_checks(new_vad_df, new_tr_df, new_sinc_df if not new_sinc_df.empty else None, thresholds)
                                cov_kw = check_question_coverage_keywords(new_tr_df, questions)
                                cov_ai = []
                                if openai_client and questions and new_transcript_text:
                                    cov_ai = check_question_coverage_ai(new_transcript_text, questions, openai_client, model="gpt-4.1-mini")
                                cov_merged = merge_coverage(cov_kw, cov_ai) if cov_ai else cov_kw
                                new_overall = compute_overall_status(new_checks)
                                save_quality_check(selected_id, new_overall, new_checks, cov_merged)
                                
                                # Enviar nova qualidade para KB
                                if openai_client and vs_id:
                                    try:
                                        n_pass = sum(1 for c in new_checks if c.get("status") == "pass")
                                        n_warn = sum(1 for c in new_checks if c.get("status") == "warn")
                                        n_fail = sum(1 for c in new_checks if c.get("status") == "fail")
                                        
                                        quality_md = (
                                            f"# Verificação de Qualidade — Prosódia\n\n"
                                            f"- Sessão: {selected_audio['session_id']}\n"
                                            f"- Projeto: {project.get('name', '')}\n"
                                            f"- Status geral: {new_overall}\n\n"
                                            f"## Resumo\n\n"
                                            f"- Checks OK: {n_pass}\n- Alertas: {n_warn}\n- Problemas: {n_fail}\n"
                                        )
                                        q_name = f"qualidade_entrevista_{selected_audio['session_id']}.md"
                                        upload_file_to_vector_store(q_name, quality_md.encode("utf-8"), mime_type="text/markdown", vector_store_id=vs_id)
                                    except Exception:
                                        pass
                                
                                # 8. Atualizar Análise de IA
                                status_container.info("🧠 Atualizando análise de IA...")
                                new_tables_lines = []
                                if not new_vad_df.empty and "duration" in new_vad_df.columns:
                                    new_total_s = new_vad_df["duration"].sum()
                                    new_tables_lines.append(f"VAD: {len(new_vad_df)} segmentos, {new_total_s:.1f}s de fala total.")
                                if not new_tr_df.empty and "SpeakerName" in new_tr_df.columns:
                                    new_by_spk = new_tr_df.groupby("SpeakerName").agg(msgs=("Text", "count"), words=("word_count", "sum")).reset_index()
                                    new_tables_text = "Participação por locutor:\n" + new_by_spk.to_string(index=False)
                                else:
                                    new_tables_text = ""
                                
                                proj_ctx = {
                                    "nome": project.get("name", ""),
                                    "especialidade": project.get("especialidade", ""),
                                    "historico": project.get("historico", ""),
                                    "problemas": project.get("problemas", ""),
                                }
                                user_prompt = build_prosodia_user_prompt(new_tables_text, proj_ctx, new_transcript_text[:3000])
                                
                                # Chamar IA
                                result_ai = ai_create_analysis(
                                    system_prompt=PROSODIA_SYSTEM_PROMPT,
                                    user_prompt=user_prompt,
                                    model="gpt-4.1-mini",
                                    vector_store_id=vs_id,
                                    temperature=0.5,
                                    max_tokens=3000,
                                )
                                save_analysis(selected_id, "gpt-4.1-mini", result_ai["text"], result_ai.get("citations", []))
                                
                                # Enviar nova análise para KB
                                if openai_client and vs_id:
                                    try:
                                        analysis_md = (
                                            f"# Análise de IA — NencLex\n\n"
                                            f"- Sessão: {selected_audio['session_id']}\n"
                                            f"- Projeto: {project.get('name', '')}\n"
                                            f"- Modelo: gpt-4.1-mini\n\n"
                                            f"## Resultado\n\n{result_ai['text']}"
                                        )
                                        a_name = f"analise_ia_{selected_audio['session_id']}.md"
                                        upload_file_to_vector_store(a_name, analysis_md.encode("utf-8"), mime_type="text/markdown", vector_store_id=vs_id)
                                    except Exception:
                                        pass
                                
                                status_container.success("🎉 Áudio, transcrição, NencLex e análise reprocessados com sucesso!")
                                time.sleep(2)
                                st.rerun()
                            else:
                                st.error("Erro ao baixar o resultado do processamento da API.")
                    except Exception as e:
                        st.error(f"Ocorreu um erro no reprocessamento: {e}")
                        
    with ac4:
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
                "⬇️ Download NencLex JSON",
                data=selected_audio["prosodia_json"],
                file_name=f"NencLex-{selected_audio.get('session_id', 'sessao')}.json",
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
