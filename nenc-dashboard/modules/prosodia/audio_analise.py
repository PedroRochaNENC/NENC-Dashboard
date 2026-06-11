"""
Prosódia — Análise do Áudio.

Exibe:
- Análise de IA (última + histórico)
- Seção de Verificação de Qualidade (checks objetivos + cobertura de perguntas)
- Botões de Regenerar Análise e Reverificar Qualidade
"""

import io
import json
import streamlit as st
import pandas as pd

from utils.prosodia_db import (
    init_db,
    get_audio,
    get_project,
    get_project_questions,
    get_latest_analysis,
    get_analyses,
    save_analysis,
    get_latest_quality_check,
    save_quality_check,
    update_audio_openai_ids,
)
from utils.prosodia_loader import load_prosodia_from_uploads
from utils.prosodia_quality import (
    run_quality_checks,
    check_question_coverage_keywords,
    check_question_coverage_ai,
    merge_coverage,
    compute_overall_status,
    status_badge,
)
from utils.prosodia_prompts import (
    PROSODIA_SYSTEM_PROMPT,
    PROSODIA_SYSTEM_PROMPT_STATISTICAL,
    PROSODIA_SYSTEM_PROMPT_STRATEGIC,
    build_prosodia_user_prompt,
)
from utils.ai_provider import (
    get_openai_client,
    get_prosodia_vector_store_id,
    create_analysis as ai_create_analysis,
)

init_db()

# ------------------------------------------------------------------
# Carregar áudio do banco
# ------------------------------------------------------------------
audio_id = st.session_state.get("pros_audio_id")
project_id = st.session_state.get("pros_project_id")

if not audio_id:
    st.warning("Nenhum áudio selecionado.")
    if st.button("← Áudios"):
        st.switch_page("modules/prosodia/audios.py")
    st.stop()

audio = get_audio(audio_id)
if not audio:
    st.error("Áudio não encontrado no banco.")
    if st.button("← Áudios"):
        st.switch_page("modules/prosodia/audios.py")
    st.stop()

project = get_project(project_id) if project_id else {}
sid = audio["session_id"]

# ------------------------------------------------------------------
# Helper: reconstruir DataFrames
# ------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _rebuild_data(a_id: int, _audio: dict) -> dict:
    class _BF:
        def __init__(self, data, name):
            self._buf = io.BytesIO(data)
            self.name = name
        def read(self): return self._buf.read()
        def seek(self, p): return self._buf.seek(p)

    session_id = _audio["session_id"]
    return load_prosodia_from_uploads(
        json_files=[_BF(_audio["prosodia_json"], f"Prosodia-{session_id}.json")] if _audio.get("prosodia_json") else [],
        csv_files=[_BF(_audio["transcricao_csv"], f"Transcricao-{session_id}.csv")] if _audio.get("transcricao_csv") else [],
        sincronizado_files=[_BF(_audio["sincronizado_csv"], f"Sincronizado-{session_id}.csv")] if _audio.get("sincronizado_csv") else [],
    )

data = _rebuild_data(audio_id, audio)
vad_df: pd.DataFrame = data.get("vad", pd.DataFrame())
tr_df: pd.DataFrame = data.get("transcricao", pd.DataFrame())
sinc_df = pd.DataFrame()
if audio.get("sincronizado_csv"):
    try:
        sinc_df = pd.read_csv(io.BytesIO(audio["sincronizado_csv"]))
    except Exception:
        pass

transcript_text = " ".join(tr_df["Text"].fillna("").astype(str).tolist()) if not tr_df.empty and "Text" in tr_df.columns else ""

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
h1, h2, h3 = st.columns([5, 1, 1])
with h1:
    st.title(f"🤖 Análise — {sid}")
    if project:
        st.caption(f"Projeto: {project.get('name', '')}")
with h2:
    st.write("")
    if st.button("📊 Timeline", width='stretch'):
        st.switch_page("modules/prosodia/audio_timeline.py")
with h3:
    st.write("")
    if st.button("← Áudios", width='stretch'):
        st.switch_page("modules/prosodia/audios.py")

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Controles")
    analysis_mode = st.radio("Modo de análise", ["Rápida (1 chamada)", "Aprofundada (2 etapas)"])
    use_kb = st.checkbox("Usar Base de Conhecimento", value=True)
    openai_model = st.selectbox(
        "Modelo OpenAI",
        ["gpt-4.1-mini", "gpt-4.1", "gpt-4o"],
        key="an_oai_model",
    )
    groq_key = st.text_input("Chave API Groq (alternativa)", type="password", key="an_groq_key")
    groq_model = st.selectbox(
        "Modelo Groq",
        ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile"],
        key="an_groq_model",
    )

# ------------------------------------------------------------------
# Montar contexto de análise
# ------------------------------------------------------------------
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
    by_spk = (
        tr_df.groupby("SpeakerName")
        .agg(msgs=("Text", "count"), words=("word_count", "sum"))
        .reset_index()
    )
    tables_lines.append("Participação por locutor:\n" + by_spk.to_string(index=False))
tables_text = "\n\n".join(tables_lines)

openai_client = get_openai_client()
vs_id = get_prosodia_vector_store_id() if use_kb else None

# ------------------------------------------------------------------
# Seção 1: Análise de IA
# ------------------------------------------------------------------
st.subheader("🤖 Análise de IA")

latest_analysis = get_latest_analysis(audio_id)

if latest_analysis:
    st.caption(f"Última análise: {latest_analysis['created_at']} — Modelo: {latest_analysis.get('model', '—')}")
    st.markdown(latest_analysis["analysis_text"])

    citations = latest_analysis.get("citations", [])
    if citations:
        with st.expander("📎 Referências da Base de Conhecimento"):
            for i, cit in enumerate(citations, 1):
                st.markdown(f"**[{i}]** {cit.get('filename', 'Documento')} — _{cit.get('quote', '')}_")

    with st.expander(f"📋 Histórico de análises ({len(get_analyses(audio_id))} registros)"):
        for an in get_analyses(audio_id):
            st.markdown(f"**{an['created_at']} — {an.get('model', '—')}**")
            st.markdown(an["analysis_text"][:500] + ("…" if len(an["analysis_text"]) > 500 else ""))
            st.divider()
else:
    st.info("Nenhuma análise disponível. Clique em **Gerar Análise** para criar a primeira.")

# Botão de gerar/regenerar
btn_label = "🔄 Regenerar Análise" if latest_analysis else "🔍 Gerar Análise"
if st.button(btn_label, type="primary"):
    groq_client = None
    if not openai_client and groq_key:
        try:
            from groq import Groq
            groq_client = Groq(api_key=groq_key)
        except Exception:
            st.error("Groq não instalado. Execute: pip install groq")
            st.stop()

    ai_client = openai_client or groq_client
    if not ai_client:
        st.error("Configure uma chave de API (OpenAI via .env ou Groq na barra lateral).")
        st.stop()

    with st.spinner("Gerando análise…"):
        try:
            result = {"text": "", "citations": []}

            if analysis_mode == "Rápida (1 chamada)":
                user_prompt = build_prosodia_user_prompt(tables_text, proj_ctx, transcript_text[:3000])

                if openai_client:
                    result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        model=openai_model,
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=3000,
                    )
                else:
                    resp = groq_client.chat.completions.create(
                        model=groq_model,
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.5,
                        max_tokens=3000,
                    )
                    result = {"text": resp.choices[0].message.content, "citations": []}

            else:  # Aprofundada
                user_prompt = build_prosodia_user_prompt(tables_text, proj_ctx, transcript_text[:3000])

                if openai_client:
                    stat_result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT_STATISTICAL,
                        user_prompt=user_prompt,
                        model=openai_model,
                        vector_store_id=None,
                        temperature=0.3,
                        max_tokens=2000,
                    )
                    strat_user = (
                        f"Análise estatística prévia:\n{stat_result['text']}\n\n"
                        f"Dados originais:\n{tables_text}"
                    )
                    strat_result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT_STRATEGIC,
                        user_prompt=strat_user,
                        model=openai_model,
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=2000,
                    )
                    combined = (
                        "## Análise Estatística\n\n" + stat_result["text"] +
                        "\n\n---\n\n## Análise Estratégica\n\n" + strat_result["text"]
                    )
                    result = {"text": combined, "citations": strat_result.get("citations", [])}
                else:
                    resp_stat = groq_client.chat.completions.create(
                        model=groq_model,
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT_STATISTICAL},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.3, max_tokens=2000,
                    )
                    stat_text = resp_stat.choices[0].message.content
                    strat_user = f"Análise prévia:\n{stat_text}\n\nDados:\n{tables_text}"
                    resp_strat = groq_client.chat.completions.create(
                        model=groq_model,
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT_STRATEGIC},
                            {"role": "user", "content": strat_user},
                        ],
                        temperature=0.5, max_tokens=2000,
                    )
                    result = {
                        "text": "## Análise Estatística\n\n" + stat_text +
                                "\n\n---\n\n## Análise Estratégica\n\n" + resp_strat.choices[0].message.content,
                        "citations": [],
                    }

            used_model = openai_model if openai_client else groq_model
            save_analysis(audio_id, used_model, result["text"], result["citations"])
            st.success("Análise salva!")
            st.rerun()

        except Exception as e:
            st.error(f"Erro ao gerar análise: {e}")

# ------------------------------------------------------------------
# Seção 2: Verificação de Qualidade
# ------------------------------------------------------------------
st.divider()
st.subheader("🔍 Verificação de Qualidade da Entrevista")

quality = get_latest_quality_check(audio_id)
questions = get_project_questions(project_id) if project_id else []

if quality:
    overall = quality.get("overall_status", "pass")
    checks = quality.get("checks", [])
    coverage = quality.get("coverage", [])

    # Métricas de topo
    n_pass = sum(1 for c in checks if c.get("status") == "pass")
    n_warn = sum(1 for c in checks if c.get("status") == "warn")
    n_fail = sum(1 for c in checks if c.get("status") == "fail")

    badge = status_badge(overall)
    overall_label = {"pass": "OK", "warn": "Atenção", "fail": "Problema"}.get(overall, overall)
    st.markdown(f"### {badge} Status Geral: **{overall_label}**")
    st.caption(f"Última verificação: {quality.get('created_at', '—')}")

    qc1, qc2, qc3 = st.columns(3)
    qc1.metric("✅ Checks OK", n_pass)
    qc2.metric("⚠️ Alertas", n_warn)
    qc3.metric("❌ Problemas", n_fail)

    # Checks objetivos
    with st.expander("📋 Checks Objetivos", expanded=(overall != "pass")):
        rows = []
        for c in checks:
            rows.append({
                "Status": status_badge(c.get("status", "pass")),
                "Verificação": c.get("label", c.get("id", "")),
                "Detalhe": c.get("detail", ""),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    # Cobertura de perguntas
    if coverage:
        with st.expander(f"❓ Cobertura das Perguntas ({len(coverage)} perguntas)", expanded=True):
            cov_rows = []
            for c in coverage:
                kw = c.get("covered_keywords")
                ai_cov = c.get("covered_ai")
                cov_rows.append({
                    "Pergunta": c.get("question", ""),
                    "Keywords": "✅" if kw else ("❌" if kw is False else "—"),
                    "IA": "✅" if ai_cov else ("❌" if ai_cov is False else "—"),
                    "Confiança": f"{c.get('confidence', 0)*100:.0f}%",
                    "Evidência": c.get("evidence", ""),
                })
            st.dataframe(pd.DataFrame(cov_rows), width='stretch', hide_index=True)
    elif questions:
        st.info("Verificação de cobertura de perguntas não realizada. Clique em 'Reverificar'.")
    else:
        st.caption("Nenhuma pergunta cadastrada no projeto.")

else:
    st.info("Verificação de qualidade ainda não realizada para este áudio.")

# Botão Reverificar
if st.button("🔄 Reverificar Qualidade"):
    questions = get_project_questions(project_id) if project_id else []
    openai_client = get_openai_client()
    groq_client = None
    if not openai_client and st.session_state.get("an_groq_key"):
        try:
            from groq import Groq
            groq_client = Groq(api_key=st.session_state["an_groq_key"])
        except Exception:
            pass

    ai_client = openai_client or groq_client

    with st.spinner("Reverificando qualidade…"):
        try:
            new_checks = run_quality_checks(vad_df, tr_df, sinc_df if not sinc_df.empty else None)
            cov_kw = check_question_coverage_keywords(tr_df, questions)
            cov_ai = []
            if ai_client and questions and transcript_text:
                q_model = st.session_state.get("an_groq_model", "llama-3.3-70b-versatile") if groq_client else "gpt-4.1-mini"
                cov_ai = check_question_coverage_ai(transcript_text, questions, ai_client, model=q_model)
            cov_merged = merge_coverage(cov_kw, cov_ai) if cov_ai else cov_kw
            new_overall = compute_overall_status(new_checks)
            save_quality_check(audio_id, new_overall, new_checks, cov_merged)
            st.success("Qualidade reverificada!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro na reverificação: {e}")
