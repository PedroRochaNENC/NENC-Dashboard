"""
Prosódia — Uploads do Projeto.

Upload e processamento em lote de áudios (JSON/CSV), com geração
automática de análise e verificação de qualidade.
"""

import logging
import io
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

# A pagina existe apenas para ingerir audios: nao ha nada aqui que uma
# conta somente leitura possa fazer.
auth.require_module_write("prosodia")

import pandas as pd

from utils.prosodia_db import (
    init_db,
    get_project,
    get_project_questions,
    create_audio,
    save_analysis,
    save_quality_check,
    update_audio_openai_ids,
)
from utils.prosodia_loader import (
    load_prosodia_from_uploads,
    normalizar_sincronizado,
    _session_id_from_name,
    _read_bytes,
)
from utils.prosodia_signals import montar_evidencias_audio
from utils.whatsapp_api_client import transcricao_para_base_conhecimento
from utils.prosodia_quality import (
    run_quality_checks,
    check_question_coverage_keywords,
    check_question_coverage_ai,
    merge_coverage,
    compute_overall_status,
    thresholds_for_project,
)
from utils.prosodia_prompts import get_prosodia_system_prompt, build_prosodia_user_prompt
from utils.ai_provider import (
    add_document_to_vector_store,
    coverage_client,
    generate_analysis,
    get_openai_client,
    get_prosodia_vector_store_id,
)
from utils.kb_attributes import build_kb_filter, project_document
from utils.organization_data import claim_external_resource

_LOGGER = logging.getLogger(__name__)

init_db()

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

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
ui.inject_theme()
ui.breadcrumb("NencBoost", project["name"], "Uploads")
page_title("upload-simple", "Uploads", project["name"])

# ------------------------------------------------------------------
# Upload em lote
# ------------------------------------------------------------------
st.divider()
st.subheader("Adicionar Áudios")
st.markdown(
    "O matching entre JSON e CSV é feito automaticamente pelo ID de sessão "
    "extraído do nome do arquivo "
    "(`NencLex-**<id>**.json` ↔ `Transcricao-**<id>**.csv`)."
)

uc1, uc2, uc3 = st.columns(3)
with uc1:
    st.markdown("**NencBoost (JSON)**")
    json_files = st.file_uploader(
        "JSON",
        type=["json"],
        accept_multiple_files=True,
        key="au_json",
        label_visibility="collapsed",
    )
with uc2:
    st.markdown("**Transcrição (CSV)**")
    csv_files = st.file_uploader(
        "CSV transcrição",
        type=["csv"],
        accept_multiple_files=True,
        key="au_csv",
        label_visibility="collapsed",
    )
with uc3:
    st.markdown("**Sincronizado (CSV)**")
    sinc_files = st.file_uploader(
        "CSV sincronizado",
        type=["csv"],
        accept_multiple_files=True,
        key="au_sinc",
        label_visibility="collapsed",
    )

# Configuração de modelo para análise automática
with st.expander("Configurações de análise automática", expanded=False):
    # Um unico seletor para todos os provedores; as chaves ficam no .env.
    ai_provider_id, ai_model = ui.ai_model_selector("au_ai_model", use_kb=True)

if json_files or csv_files or sinc_files:
    if st.button("Processar e Salvar Uploads", type="primary"):
        questions = get_project_questions(project_id)
        # O tipo do projeto escolhe o prompt da IA e os limiares padrão de qualidade.
        tipo_projeto = project.get("tipo_projeto")
        system_prompt = get_prosodia_system_prompt(tipo_projeto)
        thresholds = thresholds_for_project(project)
        openai_client = get_openai_client()
        ai_client, coverage_model = coverage_client(ai_provider_id, ai_model)
        vs_id = get_prosodia_vector_store_id()
        model = ai_model

        # Indexar arquivos por session_id
        json_by_sid = {_session_id_from_name(f.name): f for f in (json_files or [])}
        csv_by_sid = {_session_id_from_name(f.name): f for f in (csv_files or [])}
        sinc_by_sid = {_session_id_from_name(f.name): f for f in (sinc_files or [])}

        all_sids = sorted(set(json_by_sid) | set(csv_by_sid) | set(sinc_by_sid))

        progress = st.progress(0, text="Iniciando processamento...")
        total = len(all_sids)

        for i, sid in enumerate(all_sids):
            progress.progress((i) / total, text=f"Processando {sid}…")

            # Ler bytes
            json_bytes = _read_bytes(json_by_sid[sid]) if sid in json_by_sid else None
            csv_bytes = _read_bytes(csv_by_sid[sid]) if sid in csv_by_sid else None
            sinc_bytes = _read_bytes(sinc_by_sid[sid]) if sid in sinc_by_sid else None

            # Salvar no banco
            audio_id = create_audio(
                project_id=project_id,
                session_id=sid,
                prosodia_json=json_bytes,
                transcricao_csv=csv_bytes,
                sincronizado_csv=sinc_bytes,
            )

            # Parse dos dados
            parsed = load_prosodia_from_uploads(
                json_files=[json_by_sid[sid]] if sid in json_by_sid else [],
                csv_files=[csv_by_sid[sid]] if sid in csv_by_sid else [],
                sincronizado_files=[sinc_by_sid[sid]] if sid in sinc_by_sid else [],
            )
            vad_df: pd.DataFrame = parsed.get("vad", pd.DataFrame())
            tr_df: pd.DataFrame = parsed.get("transcricao", pd.DataFrame())

            sinc_df = pd.DataFrame()
            if sinc_bytes:
                import io as _io
                try:
                    sinc_df = normalizar_sincronizado(pd.read_csv(_io.BytesIO(sinc_bytes)), sid)
                except Exception:
                    # O upload segue, mas a analise automatica sai sem prosodia.
                    _LOGGER.exception("Sincronizado ilegivel no upload do audio %s.", sid)
                    st.warning(
                        f"[{sid}] NencBoost ilegível: a análise automática sai "
                        "sem as métricas acústicas."
                    )

            # -- Upload OpenAI KB --
            file_id_prosodia = None
            file_id_transcricao = None
            # Sem base configurada nao ha por que criar o arquivo: ele ficaria
            # na conta da OpenAI sem pertencer a base nenhuma.
            if openai_client and vs_id:
                try:
                    if json_bytes:
                        documento = add_document_to_vector_store(
                            vs_id,
                            f"Prosodia-{sid}.json",
                            json_bytes,
                            project_document(
                                "prosodia", project_id, session_id=sid, tipo="prosodia"
                            ),
                            wait=False,
                        )
                        file_id_prosodia = documento.id
                    if csv_bytes:
                        # O file_search da OpenAI nao indexa .csv: sobe a transcricao como texto puro.
                        # Sem colunas de sentimento, se o CSV as trouxer: a base cita a fala.
                        documento = add_document_to_vector_store(
                            vs_id,
                            f"Transcricao-{sid}.txt",
                            transcricao_para_base_conhecimento(csv_bytes),
                            project_document(
                                "prosodia", project_id, session_id=sid, tipo="transcricao"
                            ),
                            wait=False,
                        )
                        file_id_transcricao = documento.id
                    update_audio_openai_ids(audio_id, file_id_prosodia, file_id_transcricao)
                except Exception as e:
                    st.warning(f"[{sid}] Falha no upload para KB: {e}")

            # -- Análise automática de IA --
            proj_ctx = {
                "nome": project.get("name", ""),
                "especialidade": project.get("especialidade", ""),
                "historico": project.get("historico", ""),
                "problemas": project.get("problemas", ""),
            }

            evidencias = montar_evidencias_audio(vad_df, tr_df, sinc_df)
            transcript_sample = " ".join(
                tr_df["Text"].fillna("").astype(str).tolist()
            )[:3000] if not tr_df.empty and "Text" in tr_df.columns else ""

            analysis_result = {"text": "", "citations": []}
            try:
                if ai_provider_id:
                    user_prompt = build_prosodia_user_prompt(
                        evidencias.tabelas,
                        proj_ctx,
                        transcript_sample,
                        sentimento_texto=evidencias.sentimento_texto,
                        divergencias=evidencias.divergencias,
                    )
                    analysis_result = generate_analysis(
                        ai_provider_id,
                        ai_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        vector_store_id=vs_id,
                        kb_filter=build_kb_filter(project_id),
                        temperature=0.5,
                        max_tokens=3000,
                    )
            except Exception as e:
                st.warning(f"[{sid}] Falha na análise de IA: {e}")

            if analysis_result["text"]:
                save_analysis(
                    audio_id=audio_id,
                    model=model,
                    analysis_text=analysis_result["text"],
                    citations=analysis_result["citations"],
                )

            # -- Verificação de qualidade --
            quality_checks = run_quality_checks(
                vad_df, tr_df, sinc_df if not sinc_df.empty else None, thresholds,
                tipo_projeto=tipo_projeto,
            )
            coverage_kw = check_question_coverage_keywords(tr_df, questions)
            coverage_ai = []
            if ai_client and questions and transcript_sample:
                try:
                    coverage_ai = check_question_coverage_ai(
                        transcript_sample, questions, ai_client,
                        model=coverage_model,
                    )
                except Exception:
                    pass

            coverage_merged = merge_coverage(coverage_kw, coverage_ai) if coverage_ai else coverage_kw
            overall = compute_overall_status(quality_checks)

            save_quality_check(
                audio_id=audio_id,
                overall_status=overall,
                checks=quality_checks,
                coverage=coverage_merged,
            )

            progress.progress((i + 1) / total, text=f"{sid} concluído.")

        from utils.prosodia_indice import atualizar_sem_derrubar

        atualizar_sem_derrubar(project_id)
        st.success(f"{total} áudio(s) processado(s) com sucesso!")
        st.switch_page("modules/prosodia/entrevistas.py")

# ------------------------------------------------------------------
# Upload Direto de Áudio para a API (Opcional)
# ------------------------------------------------------------------
api_project_id = project.get("api_project_id")

if api_project_id:
    try:
        claim_external_resource(
            "whatsapp_api_project",
            api_project_id,
            {"project_id": project_id},
        )
    except auth.AuthorizationError:
        st.error("O projeto externo vinculado nao pertence a organizacao ativa.")
        api_project_id = None

if api_project_id:
    st.divider()
    st.subheader("Upload Direto de Áudio para a API")
    st.markdown(
        "Envie um arquivo de áudio diretamente para este projeto na API. "
        "O processamento será iniciado na API e o áudio poderá ser sincronizado posteriormente."
    )
    
    from utils.whatsapp_api_client import is_configured, upload_audio_to_project
    
    if is_configured():
        col_f, col_l = st.columns([3, 2])
        with col_f:
            audio_file = st.file_uploader(
                "Arquivo de Áudio",
                type=["wav", "mp3", "m4a", "ogg", "aac"],
                key="api_audio_file"
            )
        
        default_label = audio_file.name if audio_file else ""
        
        with col_l:
            audio_label = st.text_input(
                "Identificador / Marcador (Label)",
                value=default_label,
                placeholder="Ex: Respondente A, Sessão 1",
                key="api_audio_label"
            )
            
        if audio_file:
            if st.button("Enviar Áudio para a API", type="primary", use_container_width=True):
                try:
                    with st.spinner("Enviando arquivo para a API..."):
                        file_bytes = audio_file.getvalue()
                        filename = audio_file.name
                        
                        mimetype = "audio/wav"
                        if filename.endswith(".mp3"):
                            mimetype = "audio/mpeg"
                        elif filename.endswith(".m4a"):
                            mimetype = "audio/mp4"
                        elif filename.endswith(".ogg"):
                            mimetype = "audio/ogg"
                            
                        file_tuple = (filename, file_bytes, mimetype)
                        
                        resp = upload_audio_to_project(
                            project_id=api_project_id,
                            file=file_tuple,
                            label=audio_label.strip() if audio_label else None
                        )
                        audio_id = resp.get("audio_id") or resp.get("id")
                        st.success(f"Áudio '{filename}' enviado com sucesso! ID na API: {audio_id}")
                        st.balloons()
                except Exception as e:
                    st.error(f"Erro ao enviar áudio: {e}")
    else:
        st.caption("API de WhatsApp não configurada.")

# ------------------------------------------------------------------
# Acesso aos áudios
# ------------------------------------------------------------------
st.divider()
st.subheader("Áudios do Projeto")
st.caption("A listagem completa, busca, filtros e ações de cada áudio ficam na tela Áudios.")
if st.button("Ir para Áudios", type="primary"):
    st.switch_page("modules/prosodia/entrevistas.py")

# ------------------------------------------------------------------
# Base de Conhecimento
# ------------------------------------------------------------------
st.divider()
bc1, bc2 = st.columns([3, 1])
with bc1:
    st.markdown("**Base de Conhecimento**")
    vs_id = get_prosodia_vector_store_id()
    if vs_id:
        st.caption(f"Vector Store ativa: `{vs_id}`")
    else:
        st.caption("Nenhuma Vector Store configurada.")
with bc2:
    if st.button("Gerenciar KB →", width='stretch'):
        st.switch_page("modules/prosodia/base_conhecimento.py")
