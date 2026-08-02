"""
Prosódia — Uploads do Projeto.

Upload e processamento em lote de entrevistas (JSON/CSV), com geração
automática de análise e verificação de qualidade.
"""

import io
import json
import streamlit as st
from utils import auth

auth.require_module("prosodia")

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
    _session_id_from_name,
    _read_bytes,
)
from utils.prosodia_quality import (
    run_quality_checks,
    check_question_coverage_keywords,
    check_question_coverage_ai,
    merge_coverage,
    compute_overall_status,
)
from utils.prosodia_prompts import PROSODIA_SYSTEM_PROMPT, build_prosodia_user_prompt
from utils.ai_provider import (
    get_openai_client,
    get_prosodia_vector_store_id,
    create_analysis as ai_create_analysis,
)
from utils.organization_data import claim_external_resource

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
h1, h2, h3 = st.columns([5, 1, 1])
with h1:
    st.title(f"📤 Uploads — {project['name']}")
with h2:
    st.write("")
    if st.button("✏️ Editar", width='stretch'):
        st.switch_page("modules/prosodia/preparacao.py")
with h3:
    st.write("")
    if st.button("← Projetos", width='stretch'):
        st.switch_page("modules/prosodia/projetos.py")

# ------------------------------------------------------------------
# Upload em lote
# ------------------------------------------------------------------
st.divider()
st.subheader("📤 Adicionar Entrevistas")
st.markdown(
    "O matching entre JSON e CSV é feito automaticamente pelo ID de sessão "
    "extraído do nome do arquivo "
    "(`NencLex-**<id>**.json` ↔ `Transcricao-**<id>**.csv`)."
)

uc1, uc2, uc3 = st.columns(3)
with uc1:
    st.markdown("**🎙️ NencLex (JSON)**")
    json_files = st.file_uploader(
        "JSON",
        type=["json"],
        accept_multiple_files=True,
        key="au_json",
        label_visibility="collapsed",
    )
with uc2:
    st.markdown("**📝 Transcrição (CSV)**")
    csv_files = st.file_uploader(
        "CSV transcrição",
        type=["csv"],
        accept_multiple_files=True,
        key="au_csv",
        label_visibility="collapsed",
    )
with uc3:
    st.markdown("**🔗 Sincronizado (CSV)**")
    sinc_files = st.file_uploader(
        "CSV sincronizado",
        type=["csv"],
        accept_multiple_files=True,
        key="au_sinc",
        label_visibility="collapsed",
    )

# Configuração de modelo para análise automática
with st.expander("⚙️ Configurações de análise automática", expanded=False):
    groq_key = st.text_input(
        "Chave API Groq (análise automática sem OpenAI)",
        type="password",
        key="au_groq_key",
    )
    auto_model = st.selectbox(
        "Modelo de análise",
        ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "llama-3.3-70b-versatile"],
        key="au_model",
    )

if json_files or csv_files or sinc_files:
    if st.button("💾 Processar e Salvar Uploads", type="primary"):
        questions = get_project_questions(project_id)
        thresholds = None
        if project.get("quality_thresholds"):
            try:
                thresholds = json.loads(project["quality_thresholds"])
            except Exception:
                pass
        openai_client = get_openai_client()
        groq_client = None

        # Tentar cliente Groq como fallback
        if not openai_client and groq_key:
            try:
                from groq import Groq
                groq_client = Groq(api_key=groq_key)
            except Exception:
                pass

        ai_client = openai_client or groq_client
        vs_id = get_prosodia_vector_store_id()
        model = auto_model

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
                    sinc_df = pd.read_csv(_io.BytesIO(sinc_bytes))
                except Exception:
                    pass

            # -- Upload OpenAI KB --
            file_id_prosodia = None
            file_id_transcricao = None
            if openai_client:
                try:
                    if json_bytes:
                        fp = openai_client.files.create(
                            file=(f"Prosodia-{sid}.json", io.BytesIO(json_bytes), "application/json"),
                            purpose="assistants",
                        )
                        file_id_prosodia = fp.id
                        if vs_id:
                            openai_client.vector_stores.files.create(
                                vector_store_id=vs_id, file_id=fp.id
                            )
                    if csv_bytes:
                        fc = openai_client.files.create(
                            file=(f"Transcricao-{sid}.csv", io.BytesIO(csv_bytes), "text/csv"),
                            purpose="assistants",
                        )
                        file_id_transcricao = fc.id
                        if vs_id:
                            openai_client.vector_stores.files.create(
                                vector_store_id=vs_id, file_id=fc.id
                            )
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

            # Montar texto de tabelas
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
                tables_lines.append("Participação por locutor:\n" + by_spk.to_string(index=False))

            tables_text = "\n\n".join(tables_lines)
            transcript_sample = " ".join(
                tr_df["Text"].fillna("").astype(str).tolist()
            )[:3000] if not tr_df.empty and "Text" in tr_df.columns else ""

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
                elif ai_client:
                    user_prompt = build_prosodia_user_prompt(
                        tables_text, proj_ctx, transcript_sample
                    )
                    resp = ai_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.5,
                        max_tokens=3000,
                    )
                    analysis_result = {"text": resp.choices[0].message.content, "citations": []}
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
            quality_checks = run_quality_checks(vad_df, tr_df, sinc_df if not sinc_df.empty else None, thresholds)
            coverage_kw = check_question_coverage_keywords(tr_df, questions)
            coverage_ai = []
            if ai_client and questions and transcript_sample:
                try:
                    coverage_ai = check_question_coverage_ai(
                        transcript_sample, questions, ai_client,
                        model="llama-3.3-70b-versatile" if groq_client else "gpt-4.1-mini",
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

        st.success(f"✅ {total} entrevista(s) processada(s) com sucesso!")
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
    st.subheader("📡 Upload Direto de Áudio para a API")
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
                placeholder="Ex: Entrevistado A, Sessão 1",
                key="api_audio_label"
            )
            
        if audio_file:
            if st.button("📤 Enviar Áudio para a API", type="primary", use_container_width=True):
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
                        st.success(f"✅ Áudio '{filename}' enviado com sucesso! ID na API: {audio_id}")
                        st.balloons()
                except Exception as e:
                    st.error(f"Erro ao enviar áudio: {e}")
    else:
        st.caption("⚠️ API de WhatsApp não configurada.")

# ------------------------------------------------------------------
# Acesso às entrevistas
# ------------------------------------------------------------------
st.divider()
st.subheader("🗂️ Entrevistas do Projeto")
st.caption("A listagem completa, busca, filtros e ações de cada entrevista ficam na tela Entrevistas.")
if st.button("🗂️ Ir para Entrevistas", type="primary"):
    st.switch_page("modules/prosodia/entrevistas.py")

# ------------------------------------------------------------------
# Base de Conhecimento
# ------------------------------------------------------------------
st.divider()
bc1, bc2 = st.columns([3, 1])
with bc1:
    st.markdown("**📚 Base de Conhecimento**")
    vs_id = get_prosodia_vector_store_id()
    if vs_id:
        st.caption(f"Vector Store ativa: `{vs_id}`")
    else:
        st.caption("Nenhuma Vector Store configurada.")
with bc2:
    if st.button("Gerenciar KB →", width='stretch'):
        st.switch_page("modules/prosodia/base_conhecimento.py")
