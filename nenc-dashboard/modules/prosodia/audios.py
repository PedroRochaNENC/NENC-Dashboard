"""
Prosódia — Áudios do Projeto.

Tabela de todos os áudios de um projeto com upload em lote,
status de KB e qualidade, e navegação para Timeline e Análise por áudio.
"""

import io
import streamlit as st
import pandas as pd

from utils.prosodia_db import (
    init_db,
    get_project,
    get_audios,
    get_project_questions,
    create_audio,
    delete_audio,
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
    status_badge,
)
from utils.prosodia_prompts import PROSODIA_SYSTEM_PROMPT, build_prosodia_user_prompt
from utils.ai_provider import (
    get_openai_client,
    get_prosodia_vector_store_id,
    create_analysis as ai_create_analysis,
)

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
    st.error("Projeto não encontrado.")
    if st.button("← Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
h1, h2, h3 = st.columns([5, 1, 1])
with h1:
    st.title(f"🎵 {project['name']}")
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
st.subheader("📤 Adicionar Áudios")
st.markdown(
    "O matching entre JSON e CSV é feito automaticamente pelo ID de sessão "
    "extraído do nome do arquivo "
    "(`Prosodia-**<id>**.json` ↔ `Transcricao-**<id>**.csv`)."
)

uc1, uc2, uc3 = st.columns(3)
with uc1:
    st.markdown("**🎙️ Prosódia (JSON)**")
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
    if st.button("💾 Processar e Salvar Áudios", type="primary"):
        questions = get_project_questions(project_id)
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
            quality_checks = run_quality_checks(vad_df, tr_df, sinc_df if not sinc_df.empty else None)
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

        progress.empty()
        st.success(f"✅ {total} áudio(s) processado(s) com sucesso!")
        st.rerun()

# ------------------------------------------------------------------
# Tabela de áudios
# ------------------------------------------------------------------
st.divider()
st.subheader("🎵 Áudios do Projeto")

audios = get_audios(project_id)

if not audios:
    st.info("Nenhum áudio carregado ainda. Envie os arquivos acima.")
else:
    for audio in audios:
        a_id = audio["id"]
        sid = audio["session_id"]

        kb_ok = bool(audio.get("openai_file_id_prosodia") or audio.get("openai_file_id_transcricao"))
        kb_badge = "✅" if kb_ok else "⚠️"

        n_an = audio.get("n_analyses", 0)
        an_badge = "✅" if n_an > 0 else "⏳"

        q_status = audio.get("quality_status") or "pending"
        q_badge = status_badge(q_status) if q_status != "pending" else "⏳"

        with st.container(border=True):
            c1, c2, c3, c4, c5, c6, c7 = st.columns([4, 1, 1, 1, 1, 1, 1])

            with c1:
                st.markdown(f"**{sid}**")
                st.caption(
                    f"📅 {str(audio['created_at'])[:10]}  •  "
                    f"KB {kb_badge}  •  Análise {an_badge}  •  Qualidade {q_badge}"
                )

            with c2:
                st.write("")
                if st.button("📊 Timeline", key=f"tl_{a_id}", width='stretch'):
                    st.session_state["pros_audio_id"] = a_id
                    st.switch_page("modules/prosodia/audio_timeline.py")

            with c3:
                st.write("")
                if st.button("🤖 Análise", key=f"an_{a_id}", width='stretch'):
                    st.session_state["pros_audio_id"] = a_id
                    st.switch_page("modules/prosodia/audio_analise.py")

            with c4:
                st.write("")
                # Download JSON original
                if audio.get("prosodia_json"):
                    st.download_button(
                        "⬇ JSON",
                        data=audio["prosodia_json"],
                        file_name=f"Prosodia-{sid}.json",
                        mime="application/json",
                        key=f"dl_json_{a_id}",
                        width='stretch',
                    )

            with c5:
                st.write("")
                if audio.get("transcricao_csv"):
                    st.download_button(
                        "⬇ CSV",
                        data=audio["transcricao_csv"],
                        file_name=f"Transcricao-{sid}.csv",
                        mime="text/csv",
                        key=f"dl_csv_{a_id}",
                        width='stretch',
                    )

            with c6:
                st.write("")

            with c7:
                st.write("")
                if st.button("🗑️", key=f"del_{a_id}", width='stretch'):
                    st.session_state[f"confirm_del_audio_{a_id}"] = True

            if st.session_state.get(f"confirm_del_audio_{a_id}"):
                st.warning(f"Excluir áudio **{sid}**? Esta ação não pode ser desfeita.")
                dc1, dc2 = st.columns(2)
                with dc1:
                    if st.button("✅ Confirmar", key=f"del_yes_{a_id}", width='stretch'):
                        delete_audio(a_id)
                        st.session_state.pop(f"confirm_del_audio_{a_id}", None)
                        st.rerun()
                with dc2:
                    if st.button("❌ Cancelar", key=f"del_no_{a_id}", width='stretch'):
                        st.session_state.pop(f"confirm_del_audio_{a_id}", None)
                        st.rerun()

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
