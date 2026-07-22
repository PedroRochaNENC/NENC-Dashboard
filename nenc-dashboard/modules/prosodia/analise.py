"""
Prosódia — Análise.

Visualização de segmentos VAD, estatísticas por locutor, perfil acústico
e análise de IA via OpenAI (com base de conhecimento) ou Groq.
"""

import streamlit as st
import pandas as pd
from io import BytesIO
from fpdf import FPDF

from utils import auth
from utils.prosodia_loader import (
    get_prosodia_sessions,
    get_prosodia_speakers,
    get_prosodia_summary,
)
from utils.prosodia_charts import (
    create_vad_timeline,
    create_speaker_stats,
)
from utils.ai_provider import (
    get_openai_client,
    get_prosodia_vector_store_id,
    create_analysis,
)
from utils.prosodia_prompts import (
    PROSODIA_SYSTEM_PROMPT,
    PROSODIA_SYSTEM_PROMPT_STATISTICAL,
    PROSODIA_SYSTEM_PROMPT_STRATEGIC,
    build_prosodia_user_prompt,
)

auth.require_module("prosodia")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACOUSTIC_COLS = [
    "f0_media", "f0_variacao", "f0_min", "f0_max",
    "loudness_media", "loudness_variacao",
    "speaking_rate", "intonation_score",
    "emocao_angry", "emocao_happy", "emocao_neutral", "emocao_sad",
    "dim_arousal", "dim_dominance", "dim_valence",
]


def _filter_tr(tr_df: pd.DataFrame, session_id: str | None, speakers: list[str] | None) -> pd.DataFrame:
    work = tr_df.copy()
    if session_id and "session_id" in work.columns:
        work = work[work["session_id"] == session_id]
    if speakers and "SpeakerName" in work.columns:
        work = work[work["SpeakerName"].isin(speakers)]
    return work


def _build_acoustic_summary(tr_df: pd.DataFrame, session_id: str | None, speakers: list[str] | None) -> pd.DataFrame:
    work = _filter_tr(tr_df, session_id, speakers)
    if work.empty or "SpeakerName" not in work.columns:
        return pd.DataFrame()
    avail = [c for c in _ACOUSTIC_COLS if c in work.columns]
    if not avail:
        return pd.DataFrame()
    return work.groupby("SpeakerName")[avail].mean().round(4)


def _build_tables_text(
    tr_df: pd.DataFrame,
    vad_df: pd.DataFrame,
    session_id: str | None,
    speakers: list[str] | None,
) -> str:
    parts = []

    # VAD summary
    vad_work = vad_df.copy()
    if session_id and "session_id" in vad_work.columns:
        vad_work = vad_work[vad_work["session_id"] == session_id]
    if not vad_work.empty and "duration" in vad_work.columns:
        parts.append(
            f"### Resumo VAD\n"
            f"Total de segmentos: {len(vad_work)}\n"
            f"Tempo total de fala (s): {vad_work['duration'].sum():.2f}\n"
            f"Duração média (s): {vad_work['duration'].mean():.2f}\n"
            f"Duração mínima (s): {vad_work['duration'].min():.2f}\n"
            f"Duração máxima (s): {vad_work['duration'].max():.2f}"
        )

    # Speaker participation
    tr_work = _filter_tr(tr_df, session_id, speakers)
    if not tr_work.empty and "SpeakerName" in tr_work.columns:
        agg = {"n_mensagens": ("Text", "count")}
        if "word_count" in tr_work.columns:
            agg["total_palavras"] = ("word_count", "sum")
        spk_stats = tr_work.groupby("SpeakerName").agg(**agg)
        parts.append(f"### Participação por Locutor\n{spk_stats.to_string()}")

    # Acoustic summary
    acoustic = _build_acoustic_summary(tr_df, session_id, speakers)
    if not acoustic.empty:
        parts.append(f"### Perfil Acústico por Locutor\n{acoustic.to_string()}")

    return "\n\n".join(parts)


def _build_transcript_text(
    tr_df: pd.DataFrame,
    session_id: str | None,
    speakers: list[str] | None,
    max_chars: int = 6000,
) -> str:
    work = _filter_tr(tr_df, session_id, speakers)
    if work.empty:
        return ""
    if "seconds" in work.columns:
        work = work.sort_values("seconds")
    lines = []
    for _, row in work.iterrows():
        spk = row.get("SpeakerName", "?")
        ts = row.get("Timestamp", "")
        text = str(row.get("Text", "")).strip()
        lines.append(f"[{ts}] {spk}: {text}")
    full = "\n".join(lines)
    if len(full) > max_chars:
        full = full[:max_chars] + "\n...[transcrição truncada]"
    return full


def _sanitize(text: str) -> str:
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _build_pdf(projeto: dict, tables_text: str, ai_text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "NENC Insights - NencLex", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    if projeto.get("nome"):
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, _sanitize(f"Projeto: {projeto['nome']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    for label, key in [
        ("Especialidade", "especialidade"),
        ("Historico", "historico"),
        ("Perguntas centrais", "problemas"),
    ]:
        value = projeto.get(key, "")
        if value:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 7, f"{label}:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, _sanitize(value))
            pdf.ln(2)

    if tables_text.strip():
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Dados", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 8)
        for line in tables_text.splitlines():
            pdf.cell(0, 4, _sanitize(line[:120]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    if ai_text:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Analise de IA", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize(ai_text))

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


# ===========================================================================
# Página principal
# ===========================================================================

st.title("🔍 Análise — NencLex")

data = st.session_state.get("pr_data", {})
sessions = get_prosodia_sessions(data)

if not sessions:
    st.warning(
        "⚠️ Nenhum dado carregado. "
        "Volte à página de Preparação de Dados e carregue os arquivos."
    )
    st.stop()

vad_df: pd.DataFrame = data.get("vad", pd.DataFrame())
tr_df: pd.DataFrame = data.get("transcricao", pd.DataFrame())

# ===========================================================================
# Sidebar — Filtros
# ===========================================================================
with st.sidebar:
    st.header("⚙️ Controles")

    session_options = ["Todas"] + sessions
    selected_session_label = st.selectbox(
        "Sessão",
        session_options,
        index=0,
        key="pr_session",
    )
    selected_session = None if selected_session_label == "Todas" else selected_session_label

    st.divider()

    all_speakers = get_prosodia_speakers(data, session_id=selected_session)
    if all_speakers:
        selected_speakers = st.multiselect(
            "Locutores",
            options=all_speakers,
            default=all_speakers,
            key="pr_speakers",
        )
    else:
        selected_speakers = []

    st.divider()

    st.subheader("🤖 Análise de IA")

    _client = get_openai_client()
    if _client:
        st.success("OpenAI configurado ✅")
    else:
        st.warning("OpenAI não configurado")
        st.caption("Defina OPENAI_API_KEY no .env")

    ai_model = st.selectbox(
        "Modelo",
        ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"],
        index=0,
        key="pr_ai_model",
    )

    ai_mode = st.radio(
        "Modo de análise",
        ["Rápida (1 chamada)", "Aprofundada (2 etapas)"],
        index=0,
        key="pr_ai_mode",
    )

    _vs_id = get_prosodia_vector_store_id()
    use_kb = False
    if _vs_id:
        use_kb = st.toggle(
            "📚 Consultar base de conhecimento",
            value=True,
            key="pr_use_kb",
        )
    else:
        st.caption("📚 Base de conhecimento não configurada")

    st.divider()

    st.subheader("⚡ Análise Rápida (Groq)")
    groq_api_key = st.text_input(
        "Chave da API Groq",
        type="password",
        key="pr_groq_key",
        help="Alternativa gratuita — console.groq.com/keys",
    )

# ===========================================================================
# Filtros aplicados
# ===========================================================================
vad_filtered = vad_df.copy()
tr_filtered = tr_df.copy()

if selected_session:
    if not vad_filtered.empty and "session_id" in vad_filtered.columns:
        vad_filtered = vad_filtered[vad_filtered["session_id"] == selected_session]
    if not tr_filtered.empty and "session_id" in tr_filtered.columns:
        tr_filtered = tr_filtered[tr_filtered["session_id"] == selected_session]

if selected_speakers and not tr_filtered.empty and "SpeakerName" in tr_filtered.columns:
    tr_filtered = tr_filtered[tr_filtered["SpeakerName"].isin(selected_speakers)]

# ===========================================================================
# Métricas de resumo
# ===========================================================================
n_segs = len(vad_filtered)
total_speech = round(vad_filtered["duration"].sum(), 2) if not vad_filtered.empty and "duration" in vad_filtered.columns else 0
n_msgs = len(tr_filtered)
n_words = int(tr_filtered["word_count"].sum()) if not tr_filtered.empty and "word_count" in tr_filtered.columns else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Segmentos VAD", n_segs)
col2.metric("Tempo de fala (s)", total_speech)
col3.metric("Mensagens", n_msgs)
col4.metric("Palavras", n_words)

# ===========================================================================
# Visualizações
# ===========================================================================
with st.expander("📊 Visualizar Dados", expanded=False):

    st.subheader("🟦 Timeline VAD — Segmentos de Fala")
    if not vad_df.empty:
        fig_vad = create_vad_timeline(
            vad_df=vad_df,
            session_id=selected_session,
            title=f"Segmentos de Fala — {selected_session_label}",
        )
        st.plotly_chart(fig_vad, width='stretch')
        with st.expander("📋 Tabela de segmentos VAD"):
            st.dataframe(vad_filtered, width='stretch')
    else:
        st.info("Nenhum dado VAD carregado.")

    st.divider()

    st.subheader("👥 Participação por Locutor")
    if not tr_df.empty and "SpeakerName" in tr_df.columns:
        fig_stats = create_speaker_stats(
            transcricao_df=tr_df,
            session_id=selected_session,
            title=f"Participação — {selected_session_label}",
        )
        st.plotly_chart(fig_stats, width='stretch')
    else:
        st.info("Nenhum dado de transcrição carregado.")

    acoustic_summary = _build_acoustic_summary(tr_df, selected_session, selected_speakers or None)
    if not acoustic_summary.empty:
        st.divider()
        st.subheader("🎵 Perfil Acústico por Locutor")
        st.dataframe(acoustic_summary, width='stretch')

# ===========================================================================
# Contexto do projeto
# ===========================================================================
projeto = st.session_state.get("pr_projeto", {})

if projeto.get("nome"):
    with st.expander("📝 Contexto do projeto", expanded=False):
        st.markdown(f"**Projeto:** {projeto['nome']}")
        if projeto.get("especialidade"):
            st.markdown(f"**Contexto:** {projeto['especialidade'][:300]}")
        if projeto.get("problemas"):
            st.markdown(f"**Perguntas centrais:** {projeto['problemas'][:300]}")

# ===========================================================================
# Preparar dados para IA
# ===========================================================================
tables_text = _build_tables_text(
    tr_df=tr_df,
    vad_df=vad_df,
    session_id=selected_session,
    speakers=selected_speakers or None,
)
transcript_sample = _build_transcript_text(
    tr_df=tr_df,
    session_id=selected_session,
    speakers=selected_speakers or None,
)

# ===========================================================================
# Análise de IA — OpenAI
# ===========================================================================
st.divider()
st.subheader("🤖 Análise de IA")

if not _client:
    st.info(
        "Configure **OPENAI_API_KEY** no arquivo `.env` para análise com base de conhecimento."
    )
elif not tables_text.strip():
    st.warning("Nenhum dado disponível para análise.")
else:
    if st.button("🔍 Gerar Análise (OpenAI)", key="btn_ai_pr_openai"):
        vs_id = get_prosodia_vector_store_id() if use_kb else None
        user_prompt = build_prosodia_user_prompt(
            tables_text=tables_text,
            project_context=projeto,
            transcript_sample=transcript_sample,
        )
        is_deep = ai_mode == "Aprofundada (2 etapas)"

        with st.spinner("Gerando análise..."):
            try:
                if is_deep:
                    stat_result = create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT_STATISTICAL,
                        user_prompt=user_prompt,
                        model=ai_model,
                        vector_store_id=vs_id,
                        temperature=0.3,
                        max_tokens=3000,
                    )
                    strategic_prompt = (
                        "## Análise Estatística Prévia\n"
                        f"{stat_result['text']}\n\n"
                        "## Dados Originais\n"
                        f"{user_prompt}"
                    )
                    strat_result = create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT_STRATEGIC,
                        user_prompt=strategic_prompt,
                        model=ai_model,
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=4000,
                    )

                    tab_stat, tab_strat, tab_refs = st.tabs([
                        "📊 Análise Estatística",
                        "💡 Interpretação Estratégica",
                        "📚 Referências",
                    ])
                    with tab_stat:
                        st.markdown(stat_result["text"])
                    with tab_strat:
                        st.markdown(strat_result["text"])
                    with tab_refs:
                        all_citations = stat_result["citations"] + strat_result["citations"]
                        if all_citations:
                            for i, cit in enumerate(all_citations, 1):
                                st.markdown(f"**[{i}]** {cit['filename']}")
                                if cit.get("quote"):
                                    st.caption(cit["quote"][:300])
                        else:
                            st.info("Nenhuma citação de documentos da base nesta análise.")

                    st.session_state["pr_ai_result"] = (
                        "## Análise Estatística\n\n" + stat_result["text"]
                        + "\n\n## Interpretação Estratégica\n\n" + strat_result["text"]
                    )

                else:
                    result = create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        model=ai_model,
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=4000,
                    )
                    if result["citations"]:
                        tab_analysis, tab_refs = st.tabs(["📊 Análise", "📚 Referências"])
                        with tab_analysis:
                            st.markdown(result["text"])
                        with tab_refs:
                            for i, cit in enumerate(result["citations"], 1):
                                st.markdown(f"**[{i}]** {cit['filename']}")
                                if cit.get("quote"):
                                    st.caption(cit["quote"][:300])
                    else:
                        st.markdown(result["text"])

                    st.session_state["pr_ai_result"] = result["text"]

            except Exception as e:
                st.error(f"Erro ao chamar a API OpenAI: {e}")

    elif "pr_ai_result" in st.session_state:
        st.markdown(st.session_state["pr_ai_result"])

# ===========================================================================
# Análise Rápida — Groq
# ===========================================================================
st.divider()
st.subheader("⚡ Análise Rápida (Groq)")

if not groq_api_key:
    st.info(
        "Insira sua chave Groq na barra lateral para análise rápida da transcrição. "
        "Obtenha gratuitamente em **console.groq.com/keys**."
    )
elif not transcript_sample.strip():
    st.warning("Nenhuma transcrição disponível para a sessão/locutores selecionados.")
else:
    if st.button("⚡ Gerar Análise Rápida (Groq)", key="btn_ai_pr_groq"):
        from groq import Groq

        project_ctx = ""
        for label, key in [
            ("Projeto", "nome"), ("Contexto", "especialidade"),
            ("Histórico", "historico"), ("Perguntas centrais", "problemas"),
        ]:
            if projeto.get(key):
                project_ctx += f"{label}: {projeto[key]}\n"

        session_label = f"sessão '{selected_session}'" if selected_session else "todas as sessões"
        speaker_label = (
            f"locutores: {', '.join(selected_speakers)}" if selected_speakers else "todos os locutores"
        )

        system_prompt = (
            "Você é um especialista em análise de entrevistas qualitativas e prosódia verbal. "
            "Analise a transcrição e forneça insights sobre padrões de comunicação, temas "
            "recorrentes e dinâmica entre locutores. Responda em português do Brasil."
        )
        user_prompt = (
            f"{project_ctx}\n---\n"
            f"Transcrição ({session_label} | {speaker_label}):\n\n{transcript_sample}\n\n---\n"
            "Por favor, forneça:\n"
            "1. **Resumo geral** da conversa\n"
            "2. **Dinâmica entre locutores** — quem fala mais, padrões de turno\n"
            "3. **Temas e perguntas centrais** identificados\n"
            "4. **Pontos de destaque** — respostas relevantes, informações-chave\n"
            "5. **Insights e recomendações** para a pesquisa"
        )

        with st.spinner("Gerando análise rápida..."):
            try:
                groq_client = Groq(api_key=groq_api_key)
                response = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.5,
                    max_tokens=3000,
                )
                st.session_state["pr_groq_result"] = response.choices[0].message.content
            except Exception as e:
                st.error(f"Erro ao chamar a API Groq: {e}")

    if "pr_groq_result" in st.session_state:
        st.markdown(st.session_state["pr_groq_result"])

# ===========================================================================
# Navegação + Exportar PDF
# ===========================================================================
st.divider()
col_nav1, col_nav2 = st.columns(2)

with col_nav1:
    if st.button("⬅️ Voltar para Preparação", width='stretch'):
        st.switch_page("modules/prosodia/preparacao.py")

with col_nav2:
    ai_text_for_pdf = (
        st.session_state.get("pr_ai_result", "")
        or st.session_state.get("pr_groq_result", "")
    )
    pdf_bytes = _build_pdf(projeto, tables_text, ai_text_for_pdf)
    st.download_button(
        "📄 Exportar PDF",
        data=pdf_bytes,
        file_name=f"{projeto.get('nome', 'analise_nenclex')}.pdf",
        mime="application/pdf",
        width='stretch',
        type="primary",
    )

