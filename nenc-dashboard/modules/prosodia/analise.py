"""
Prosódia — Análise.

Visualização de segmentos VAD, estatísticas por locutor, timeline de
mensagens e análise de IA via Groq com contexto do projeto.
"""

import streamlit as st
import pandas as pd
from groq import Groq

from utils.prosodia_loader import (
    get_prosodia_sessions,
    get_prosodia_speakers,
    get_prosodia_summary,
)
from utils.prosodia_charts import (
    create_vad_timeline,
    create_speaker_stats,
    create_message_timeline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_transcript_text(
    tr_df: pd.DataFrame,
    session_id: str | None,
    speakers: list[str] | None,
    max_chars: int = 8000,
) -> str:
    """Formata a transcrição filtrada como texto para o prompt de IA."""
    work = tr_df.copy()
    if session_id:
        work = work[work["session_id"] == session_id]
    if speakers:
        work = work[work["SpeakerName"].isin(speakers)]
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


# ===========================================================================
# Página principal
# ===========================================================================

st.title("🔍 Análise — Prosódia")

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

    # Sessão
    session_options = ["Todas"] + sessions
    selected_session_label = st.selectbox(
        "Sessão",
        session_options,
        index=0,
        key="pr_session",
    )
    selected_session = None if selected_session_label == "Todas" else selected_session_label

    st.divider()

    # Locutores
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
    api_key = st.text_input(
        "Chave da API Groq",
        type="password",
        key="pr_groq_key",
        help="Obtenha gratuitamente em console.groq.com/keys",
    )

# ===========================================================================
# Métricas de resumo (contextuais à seleção)
# ===========================================================================
summary = get_prosodia_summary(data)

# Filtrar para sessão selecionada
vad_filtered = vad_df.copy()
tr_filtered = tr_df.copy()

if selected_session:
    if not vad_filtered.empty and "session_id" in vad_filtered.columns:
        vad_filtered = vad_filtered[vad_filtered["session_id"] == selected_session]
    if not tr_filtered.empty and "session_id" in tr_filtered.columns:
        tr_filtered = tr_filtered[tr_filtered["session_id"] == selected_session]

if selected_speakers and not tr_filtered.empty and "SpeakerName" in tr_filtered.columns:
    tr_filtered = tr_filtered[tr_filtered["SpeakerName"].isin(selected_speakers)]

# Métricas
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
# Seção 1 — Timeline VAD
# ===========================================================================
st.divider()
st.subheader("🟦 Timeline VAD — Segmentos de Fala")

if not vad_df.empty:
    fig_vad = create_vad_timeline(
        vad_df=vad_df,
        session_id=selected_session,
        title=f"Segmentos de Fala — {selected_session_label}",
    )
    st.plotly_chart(fig_vad, use_container_width=True)

    with st.expander("📋 Tabela de segmentos VAD"):
        st.dataframe(vad_filtered, use_container_width=True)
else:
    st.info("Nenhum dado VAD carregado.")

# ===========================================================================
# Seção 2 — Participação por Locutor
# ===========================================================================
st.divider()
st.subheader("👥 Participação por Locutor")

if not tr_df.empty and "SpeakerName" in tr_df.columns:
    fig_stats = create_speaker_stats(
        transcricao_df=tr_df,
        session_id=selected_session,
        title=f"Participação — {selected_session_label}",
    )
    st.plotly_chart(fig_stats, use_container_width=True)
else:
    st.info("Nenhum dado de transcrição carregado.")


# ===========================================================================
# Seção 4 — Análise de IA
# ===========================================================================
st.divider()
st.subheader("🤖 Análise de IA")

projeto = st.session_state.get("pr_projeto", {})

if projeto.get("nome"):
    with st.expander("📝 Contexto do projeto", expanded=False):
        st.markdown(f"**Projeto:** {projeto['nome']}")
        if projeto.get("especialidade"):
            st.markdown(f"**Contexto:** {projeto['especialidade'][:300]}")
        if projeto.get("problemas"):
            st.markdown(f"**Perguntas centrais:** {projeto['problemas'][:300]}")

transcript_text = _build_transcript_text(
    tr_df=tr_df,
    session_id=selected_session,
    speakers=selected_speakers if selected_speakers else None,
)

if not api_key:
    st.info(
        "Insira sua chave da API Groq na barra lateral para habilitar a análise automática. "
        "Obtenha gratuitamente em **console.groq.com/keys**."
    )
elif not transcript_text.strip():
    st.warning("Nenhuma transcrição disponível para a sessão/locutores selecionados.")
else:
    if st.button("🔍 Gerar Análise", key="btn_ai_pr"):
        # Montar contexto do projeto para o prompt
        project_ctx = ""
        if projeto.get("nome"):
            project_ctx += f"Projeto: {projeto['nome']}\n"
        if projeto.get("especialidade"):
            project_ctx += f"Contexto: {projeto['especialidade']}\n"
        if projeto.get("historico"):
            project_ctx += f"Histórico: {projeto['historico']}\n"
        if projeto.get("problemas"):
            project_ctx += f"Perguntas centrais: {projeto['problemas']}\n"

        system_prompt = (
            "Você é um especialista em análise de entrevistas qualitativas e prosódia verbal. "
            "Analise a transcrição abaixo e forneça insights aprofundados sobre a conversa. "
            "Identifique padrões de comunicação, temas recorrentes, dinâmica entre os locutores "
            "e pontos relevantes para o contexto da pesquisa. "
            "Responda em português do Brasil de forma clara, estruturada e objetiva."
        )

        session_label = f"sessão '{selected_session}'" if selected_session else "todas as sessões"
        speaker_label = (
            f"locutores: {', '.join(selected_speakers)}"
            if selected_speakers
            else "todos os locutores"
        )

        user_prompt = (
            f"{project_ctx}\n"
            f"---\n"
            f"Transcrição ({session_label} | {speaker_label}):\n\n"
            f"{transcript_text}\n\n"
            f"---\n"
            "Por favor, forneça:\n"
            "1. **Resumo geral** da conversa\n"
            "2. **Dinâmica entre locutores** — quem fala mais, padrões de turno\n"
            "3. **Temas e perguntas centrais** identificados\n"
            "4. **Pontos de destaque** — respostas relevantes, informações-chave\n"
            "5. **Insights e recomendações** para a pesquisa"
        )

        with st.spinner("Gerando análise..."):
            try:
                client = Groq(api_key=api_key)
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.5,
                    max_tokens=3000,
                )
                ai_result = response.choices[0].message.content
                st.session_state["pr_ai_result"] = ai_result
            except Exception as e:
                st.error(f"Erro ao chamar a API Groq: {e}")

    # Mostrar resultado persistido
    if "pr_ai_result" in st.session_state:
        st.markdown(st.session_state["pr_ai_result"])
