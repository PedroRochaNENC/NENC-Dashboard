"""
Prosódia — Monitoramento da API do WhatsApp.

Permite visualizar os áudios recebidos, monitorar o status dos jobs de processamento
e realizar a importação manual de áudios para qualquer projeto local.
"""

import streamlit as st
from utils import auth

auth.require_module("prosodia")

import pandas as pd
import io as _io
from utils.whatsapp_api_client import (
    get_all_audios,
    get_audio_status,
    get_audio_result,
    list_owned_jobs,
    map_api_result_to_all_formats,
    is_configured
)
from utils.prosodia_db import (
    get_projects,
    create_audio,
    get_project_questions,
    save_analysis,
    save_quality_check,
    update_audio_openai_ids,
    get_audios
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
from utils.organization_data import (
    claim_external_resource,
    list_external_resources,
    register_derived_external_resource,
)


_STATUS_EMOJIS = {
    "pending": "🟡 Pendente",
    "processing": "🔵 Processando",
    "done": "🟢 Concluído",
    "failed": "🔴 Falhou",
    "not_processed": "⚪ Não Processado",
}


def _normalize_phone(value) -> str:
    return "".join(filter(str.isdigit, str(value or "")))


def _owned_contact_ids_by_phone() -> dict[str, str]:
    contact_ids_by_phone = {}
    for resource in list_external_resources("whatsapp_contact"):
        phone = _normalize_phone(resource["metadata"].get("phone"))
        if phone:
            contact_ids_by_phone[phone] = resource["id"]
    return contact_ids_by_phone


def _owned_api_project_ids(projects: list[dict]) -> list[int]:
    api_project_ids = []
    for project in projects:
        api_project_id = project.get("api_project_id")
        if api_project_id is None:
            continue
        try:
            claim_external_resource(
                "whatsapp_api_project",
                api_project_id,
                {"project_id": project["id"]},
            )
        except auth.AuthorizationError:
            continue
        api_project_ids.append(api_project_id)
    return api_project_ids


def _register_owned_audio(audio: dict, parent_type: str, parent_id) -> bool:
    audio_id = audio.get("id")
    if audio_id is None:
        return False
    try:
        register_derived_external_resource(
            "whatsapp_audio",
            audio_id,
            parent_type,
            parent_id,
            {
                "phone": _normalize_phone(audio.get("contact_phone")),
                "whatsapp_message_id": str(audio.get("whatsapp_message_id") or ""),
            },
        )
    except auth.AuthorizationError:
        return False
    return True


def _owned_audios(
    phone_filter: str,
    limit: int,
    api_project_ids: list[int],
    contact_ids_by_phone: dict[str, str],
) -> list[dict]:
    contact_phones = set(contact_ids_by_phone)
    normalized_phone_filter = _normalize_phone(phone_filter)
    if normalized_phone_filter:
        if normalized_phone_filter not in contact_phones:
            return []
        return [
            audio
            for audio in get_all_audios(phone=normalized_phone_filter, limit=limit)
            if _register_owned_audio(
                audio,
                "whatsapp_contact",
                contact_ids_by_phone[normalized_phone_filter],
            )
        ]

    audios_by_id = {}
    for api_project_id in api_project_ids:
        for audio in get_all_audios(project_id=api_project_id, limit=limit):
            if _register_owned_audio(audio, "whatsapp_api_project", api_project_id):
                audios_by_id[str(audio.get("id"))] = audio
    for phone, contact_id in contact_ids_by_phone.items():
        for audio in get_all_audios(phone=phone, limit=limit):
            if _register_owned_audio(audio, "whatsapp_contact", contact_id):
                audios_by_id[str(audio.get("id"))] = audio
    return list(audios_by_id.values())[:limit]

st.title("📡 Monitor de Áudios e Jobs")
st.markdown(
    "Monitore os áudios recebidos via WhatsApp, acompanhe o status de transcrição/análise (DevAIce e Whisper) "
    "e importe novos áudios processados para seus projetos do dashboard."
)

# Botão Voltar para Projetos
nav_col, _ = st.columns([2, 8])
with nav_col:
    if st.button("← Projetos", width='stretch'):
        st.switch_page("modules/prosodia/projetos.py")

st.divider()

if not is_configured():
    st.warning("⚠️ API de WhatsApp não está configurada. Configure a URL e a Chave de API primeiro.")
    if st.button("⚙️ Ir para Configurações", type="primary"):
        st.switch_page("modules/prosodia/whatsapp_config.py")
    st.stop()

projects = get_projects()
owned_api_project_ids = _owned_api_project_ids(projects)
owned_contact_ids_by_phone = _owned_contact_ids_by_phone()

# Abas do Monitor
tab_audios, tab_jobs = st.tabs([
    "🎙️ Áudios Recebidos",
    "⚙️ Jobs de Processamento"
])

# ---------------------------------------------------------------------------
# TAB 1: Áudios Recebidos & Importação
# ---------------------------------------------------------------------------
with tab_audios:
    st.subheader("Filtros de Áudios")
    c_f1, c_f2 = st.columns(2)
    with c_f1:
        filtro_phone = st.text_input("Filtrar por Telefone", placeholder="Ex: 5511999999999", key="mon_filter_phone")
    with c_f2:
        filtro_limit = st.slider("Quantidade limite", min_value=10, max_value=200, value=50, step=10)
        
    try:
        with st.spinner("Buscando áudios na API..."):
            audios_api = _owned_audios(
                filtro_phone,
                filtro_limit,
                owned_api_project_ids,
                owned_contact_ids_by_phone,
            )
        if filtro_phone.strip() and not audios_api:
            if _normalize_phone(filtro_phone) not in owned_contact_ids_by_phone:
                st.warning("O telefone informado nao pertence a organizacao ativa.")
            
        if not audios_api:
            st.info("Nenhum áudio pertencente a esta organização foi encontrado.")
        else:
            # Enriquecer com status de cada áudio
            dados_enriquecidos = []
            for a in audios_api:
                status_info = get_audio_status(a["id"])
                dados_enriquecidos.append({
                    "ID": a["id"],
                    "Telefone": a.get("contact_phone", ""),
                    "Mensagem ID": a.get("whatsapp_message_id", ""),
                    "Duração (s)": a.get("duration_sec") or status_info.get("duration_sec") or 0.0,
                    "Status": status_info.get("status", "desconhecido"),
                    "Data": a.get("received_at", "")
                })
                
            df_audios = pd.DataFrame(dados_enriquecidos)
            
            # Formatação
            if "Data" in df_audios.columns:
                df_audios["Data"] = pd.to_datetime(df_audios["Data"]).dt.strftime("%d/%m/%Y %H:%M")
                
            df_audios["Status"] = df_audios["Status"].map(
                lambda value: _STATUS_EMOJIS.get(value, value)
            )
            
            st.markdown("### Selecione os áudios prontos (Concluídos) para importar")
            
            # Tabela interativa com checkbox de seleção múltipla usando st.data_editor
            df_selecionavel = df_audios.copy()
            df_selecionavel.insert(0, "Importar", False)
            
            # Somente habilitar importação para concluídos
            # st.data_editor é perfeito aqui
            edited_df = st.data_editor(
                df_selecionavel,
                use_container_width=True,
                hide_index=True,
                disabled=["ID", "Telefone", "Mensagem ID", "Duração (s)", "Status", "Data"],
                key="editor_importacao"
            )
            
            # Obter IDs selecionados
            selecionados = edited_df[edited_df["Importar"] == True]
            
            if not selecionados.empty:
                st.markdown("---")
                st.subheader("📥 Ação: Importar Selecionados")
                
                # Selecionar o projeto destino
                projetos = projects
                if not projetos:
                    st.error("Nenhum projeto cadastrado no dashboard. Crie um projeto na página principal primeiro.")
                else:
                    col_proj_dest, col_action_btn = st.columns([3, 1])
                    with col_proj_dest:
                        projeto_destino = st.selectbox(
                            "Selecione o projeto de destino",
                            options=projetos,
                            format_func=lambda x: x["name"]
                        )
                    with col_action_btn:
                        st.write("")
                        st.write("")
                        if st.button("📥 Importar para Projeto", type="primary", use_container_width=True):
                            project_id = projeto_destino["id"]
                            project = projeto_destino
                            
                            # Buscar áudios locais do projeto para evitar duplicação
                            audios_locais = get_audios(project_id)
                            existing_wa_ids = {a.get("whatsapp_message_id") for a in audios_locais if a.get("whatsapp_message_id")}
                            
                            sucesso_count = 0
                            pulados_count = 0
                            
                            total_imp = len(selecionados)
                            prog_bar = st.progress(0, text=f"Iniciando importação de {total_imp} áudios…")
                            
                            openai_client = get_openai_client()
                            vs_id = get_prosodia_vector_store_id()
                            questions = get_project_questions(project_id)
                            thresholds = None
                            if project and project.get("quality_thresholds"):
                                try:
                                    import json
                                    thresholds = json.loads(project["quality_thresholds"])
                                except Exception:
                                    pass
                            
                            # Iterar sobre as linhas selecionadas
                            for i, row in enumerate(selecionados.to_dict("records")):
                                wa_msg_id = row["Mensagem ID"]
                                audio_api_id = row["ID"]
                                phone = row["Telefone"]
                                status_txt = row["Status"]
                                
                                session_id = f"wa_{phone}_{audio_api_id}"
                                prog_bar.progress((i + 1) / total_imp, text=f"Importando {session_id} ({i+1}/{total_imp})…")
                                
                                if "Concluído" not in status_txt:
                                    st.warning(f"⚠️ Áudio {session_id} não está pronto. Pulando.")
                                    continue
                                
                                if wa_msg_id in existing_wa_ids:
                                    st.info(f"ℹ️ Áudio {session_id} já importado anteriormente. Pulando.")
                                    pulados_count += 1
                                    continue

                                owned_audio_ids = {
                                    resource["id"]
                                    for resource in list_external_resources("whatsapp_audio")
                                }
                                if str(audio_api_id) not in owned_audio_ids:
                                    st.warning(
                                        f"⚠️ Áudio {session_id} não pertence mais à organização ativa. Pulando."
                                    )
                                    pulados_count += 1
                                    continue
                                    
                                # Executar fluxo de importação completo (idêntico ao sync de entrevistas.py)
                                try:
                                    # 1. Baixar resultado
                                    result_json = get_audio_result(audio_api_id)
                                    if not result_json:
                                        st.error(f"Erro ao baixar resultado de {session_id}.")
                                        continue
                                        
                                    # 2. Mapear formatos
                                    json_bytes, csv_bytes, sinc_bytes = map_api_result_to_all_formats(result_json, session_id)
                                    
                                    # 3. Salvar no banco local
                                    audio_id = create_audio(
                                        project_id=project_id,
                                        session_id=session_id,
                                        prosodia_json=json_bytes,
                                        transcricao_csv=csv_bytes,
                                        sincronizado_csv=sinc_bytes,
                                        whatsapp_message_id=wa_msg_id
                                    )
                                    
                                    # 4. Enviar para OpenAI KB
                                    file_id_prosodia = None
                                    file_id_transcricao = None
                                    if openai_client:
                                        try:
                                            if json_bytes:
                                                fp = openai_client.files.create(
                                                    file=(f"Prosodia-{session_id}.json", _io.BytesIO(json_bytes), "application/json"),
                                                    purpose="assistants"
                                                )
                                                file_id_prosodia = fp.id
                                                if vs_id:
                                                    openai_client.vector_stores.files.create(
                                                        vector_store_id=vs_id, file_id=fp.id
                                                    )
                                            if csv_bytes:
                                                fc = openai_client.files.create(
                                                    file=(f"Transcricao-{session_id}.csv", _io.BytesIO(csv_bytes), "text/csv"),
                                                    purpose="assistants"
                                                )
                                                file_id_transcricao = fc.id
                                                if vs_id:
                                                    openai_client.vector_stores.files.create(
                                                        vector_store_id=vs_id, file_id=fc.id
                                                    )
                                            update_audio_openai_ids(audio_id, file_id_prosodia, file_id_transcricao)
                                        except Exception as e:
                                            st.warning(f"[{session_id}] Falha no upload para KB: {e}")
                                            
                                    # 5. Parse DataFrames
                                    from utils.prosodia_loader import load_prosodia_from_uploads
                                    class _BytesFile:
                                        def __init__(self, data: bytes, name: str):
                                            self._buf = _io.BytesIO(data)
                                            self.name = name
                                        def read(self):
                                            return self._buf.read()
                                        def seek(self, pos):
                                            return self._buf.seek(pos)
                                            
                                    json_files = [_BytesFile(json_bytes, f"Prosodia-{session_id}.json")] if json_bytes else []
                                    csv_files = [_BytesFile(csv_bytes, f"Transcricao-{session_id}.csv")] if csv_bytes else []
                                    sinc_files = [_BytesFile(sinc_bytes, f"Sincronizado-{session_id}.csv")] if sinc_bytes else []
                                    
                                    parsed = load_prosodia_from_uploads(
                                        json_files=json_files,
                                        csv_files=csv_files,
                                        sincronizado_files=sinc_files
                                    )
                                    vad_df = parsed.get("vad", pd.DataFrame())
                                    tr_df = parsed.get("transcricao", pd.DataFrame())
                                    sinc_df = pd.DataFrame()
                                    if sinc_bytes:
                                        try:
                                            sinc_df = pd.read_csv(_io.BytesIO(sinc_bytes))
                                        except Exception:
                                            pass
                                            
                                    # 6. Análise automática de IA
                                    proj_ctx = {
                                        "nome": project.get("name", ""),
                                        "especialidade": project.get("especialidade", ""),
                                        "historico": project.get("historico", ""),
                                        "problemas": project.get("problemas", ""),
                                    }
                                    
                                    tables_lines = []
                                    if not vad_df.empty and "duration" in vad_df.columns:
                                        total_s = vad_df["duration"].sum()
                                        tables_lines.append(f"VAD: {len(vad_df)} segmentos, {total_s:.1f}s de fala total.")
                                    if not tr_df.empty and "SpeakerName" in tr_df.columns:
                                        by_spk = tr_df.groupby("SpeakerName").agg(msgs=("Text", "count"), words=("word_count", "sum")).reset_index()
                                        tables_lines.append("Participação por locutor:\n" + by_spk.to_string(index=False))
                                    tables_text = "\n\n".join(tables_lines)
                                    transcript_sample = " ".join(tr_df["Text"].fillna("").astype(str).tolist())[:3000]
                                    
                                    # Criar prompt e chamar IA
                                    system_prompt = PROSODIA_SYSTEM_PROMPT
                                    user_prompt = build_prosodia_user_prompt(tables_text, proj_ctx, transcript_sample)
                                    
                                    with st.spinner(f"[{session_id}] Gerando análise de IA..."):
                                        ai_res = ai_create_analysis(
                                            system_prompt=system_prompt,
                                            user_prompt=user_prompt,
                                            model="gpt-4.1-mini",
                                            vector_store_id=vs_id
                                        )
                                    save_analysis(
                                        audio_id=audio_id,
                                        model="gpt-4.1-mini",
                                        analysis_text=ai_res.get("text", ""),
                                        citations=ai_res.get("citations", [])
                                    )
                                    
                                    # 7. Quality Checks & Coverage
                                    checks = run_quality_checks(vad_df, tr_df, sinc_df, thresholds)
                                    kw_cov = check_question_coverage_keywords(tr_df, questions)
                                    
                                    ai_cov = []
                                    if questions and openai_client:
                                        with st.spinner(f"[{session_id}] Verificando cobertura de perguntas com IA..."):
                                            ai_cov = check_question_coverage_ai(transcript_sample, questions, openai_client, "gpt-4.1-mini")
                                            
                                    merged_cov = merge_coverage(kw_cov, ai_cov)
                                    overall_st = compute_overall_status(checks)
                                    
                                    save_quality_check(
                                        audio_id=audio_id,
                                        overall_status=overall_st,
                                        checks=checks,
                                        coverage=merged_cov
                                    )
                                    
                                    sucesso_count += 1
                                except Exception as e:
                                    st.error(f"Erro ao processar importação do áudio {session_id}: {e}")
                                    
                            st.success(
                                f"🎉 Importação concluída!\n\n"
                                f"- **Sucessos:** {sucesso_count}\n"
                                f"- **Pulados/Existentes:** {pulados_count}"
                            )
                            st.rerun()
            
    except Exception as e:
        st.error(f"Não foi possível obter a lista de áudios da API: {e}")

# ---------------------------------------------------------------------------
# TAB 2: Jobs de Processamento
# ---------------------------------------------------------------------------
with tab_jobs:
    st.subheader("Jobs de Processamento Ativos e Recentes")
    
    col_job_status = st.selectbox(
        "Filtrar por Status do Job",
        options=["Todos", "pending", "processing", "done", "failed"],
        format_func=lambda x: "Todos" if x == "Todos" else x.upper()
    )
    
    try:
        status_filter = None if col_job_status == "Todos" else col_job_status
        jobs = list_owned_jobs(status=status_filter, limit=100)
        
        if not jobs:
            st.info("Nenhum job de processamento encontrado.")
        else:
            df_jobs = pd.DataFrame(jobs)
            
            # Renomear e formatar
            df_jobs = df_jobs.rename(columns={
                "id": "ID Job",
                "audio_id": "ID Áudio",
                "status": "Status",
                "started_at": "Início",
                "completed_at": "Conclusão",
                "error_msg": "Mensagem de Erro"
            })
            
            if "Status" in df_jobs.columns:
                df_jobs["Status"] = df_jobs["Status"].map(
                    lambda value: _STATUS_EMOJIS.get(value, value)
                )
            
            if "Início" in df_jobs.columns:
                df_jobs["Início"] = pd.to_datetime(df_jobs["Início"]).dt.strftime("%d/%m/%Y %H:%M")
            if "Conclusão" in df_jobs.columns:
                df_jobs["Conclusão"] = pd.to_datetime(df_jobs["Conclusão"]).dt.strftime("%d/%m/%Y %H:%M")
                
            cols_show = ["ID Job", "ID Áudio", "Status", "Início", "Conclusão"]
            if "Mensagem de Erro" in df_jobs.columns:
                cols_show.append("Mensagem de Erro")
                
            st.dataframe(df_jobs[cols_show], use_container_width=True, hide_index=True)
            
    except Exception as e:
        st.error(f"Erro ao obter lista de jobs da API: {e}")
