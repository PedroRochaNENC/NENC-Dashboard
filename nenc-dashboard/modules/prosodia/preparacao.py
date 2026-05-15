"""
Prosódia — Preparação de Dados.

Upload de arquivos JSON (prosódia/VAD) e CSV (transcrição) por sessão,
formulário de contexto do projeto e pré-visualização dos dados.
"""

import streamlit as st

from utils.prosodia_loader import (
    load_prosodia_from_uploads,
    get_prosodia_summary,
    get_prosodia_sessions,
    get_prosodia_speakers,
)


st.title("📂 Preparação de Dados — Prosódia")

# ==================================================================
# Seção 1: Upload de Arquivos
# ==================================================================
st.subheader("📤 Upload de Arquivos")
st.markdown(
    "Envie os arquivos gerados pelo pipeline de prosódia. "
    "Você pode carregar **múltiplas sessões** de uma vez.\n\n"
    "O matching entre JSON e CSV é feito automaticamente pelo ID da sessão "
    "extraído do nome do arquivo (ex: `Prosodia-**35523510_Fim**.json` ↔ `Transcricao-**35523510_Fim**.csv`)."
)

col1, col2 = st.columns(2)

with col1:
    st.markdown("**🎙️ Arquivos de Prosódia (JSON)**")
    st.caption("Formato: `Prosodia-<session_id>.json` — campo `result.vad` com segmentos de fala")
    json_files = st.file_uploader(
        "JSON de prosódia",
        type=["json"],
        accept_multiple_files=True,
        key="pr_json",
        label_visibility="collapsed",
    )

with col2:
    st.markdown("**📝 Arquivos de Transcrição (CSV)**")
    st.caption("Formato: `Transcricao-<session_id>.csv` — colunas: SpeakerName, Timestamp, Text")
    csv_files = st.file_uploader(
        "CSV de transcrição",
        type=["csv"],
        accept_multiple_files=True,
        key="pr_csv",
        label_visibility="collapsed",
    )

if json_files or csv_files:
    data = load_prosodia_from_uploads(
        json_files=json_files or [],
        csv_files=csv_files or [],
    )
    st.session_state["pr_data"] = data

# Recuperar dados da sessão
data = st.session_state.get("pr_data", {})

# ------------------------------------------------------------------
# Avisos de validação
# ------------------------------------------------------------------
if data.get("_errors"):
    for err in data["_errors"]:
        st.warning(err)

# ==================================================================
# Seção 2: Contexto do Projeto
# ==================================================================
st.divider()
st.subheader("📋 Contexto do Projeto")
st.markdown(
    "Preencha as informações abaixo para enriquecer a análise de IA "
    "com contexto sobre o estudo."
)

projeto = st.session_state.get("pr_projeto", {})

col_a, col_b = st.columns(2)
with col_a:
    nome = st.text_input(
        "Nome do projeto",
        value=projeto.get("nome", ""),
        placeholder="Ex: Kynetec — Mão de Obra Rural 2026",
        key="pr_nome",
    )
    especialidade = st.text_area(
        "Contexto / Área do estudo",
        value=projeto.get("especialidade", ""),
        placeholder="Descreva o objetivo da pesquisa, público-alvo e condições de coleta...",
        height=100,
        key="pr_especialidade",
    )

with col_b:
    historico = st.text_area(
        "Histórico / Informações adicionais",
        value=projeto.get("historico", ""),
        placeholder="Informações sobre a empresa, produto ou contexto da pesquisa...",
        height=100,
        key="pr_historico",
    )
    problemas = st.text_area(
        "Perguntas / Problemas centrais",
        value=projeto.get("problemas", ""),
        placeholder="Quais questões centrais devem ser respondidas pela análise?",
        height=100,
        key="pr_problemas",
    )

if st.button("💾 Salvar Contexto", key="btn_pr_projeto"):
    st.session_state["pr_projeto"] = {
        "nome": nome,
        "especialidade": especialidade,
        "historico": historico,
        "problemas": problemas,
    }
    st.success("Contexto salvo!")

# ==================================================================
# Seção 3: Resumo dos Dados
# ==================================================================
sessions = get_prosodia_sessions(data)

if sessions:
    st.divider()
    st.subheader("✅ Resumo dos Dados")

    summary = get_prosodia_summary(data)

    cols = st.columns(5)
    cols[0].metric("Sessões", summary["n_sessions"])
    cols[1].metric("Segmentos VAD", summary["n_segments_vad"])
    cols[2].metric("Tempo de fala (s)", summary["total_speech_s"])
    cols[3].metric("Mensagens", summary["n_messages"])
    cols[4].metric("Locutores", summary["n_speakers"])

    # Sessões detectadas
    with st.expander(f"Sessões detectadas ({len(sessions)})"):
        for sid in sessions:
            vad_df = data.get("vad", __import__("pandas").DataFrame())
            tr_df = data.get("transcricao", __import__("pandas").DataFrame())

            n_vad = len(vad_df[vad_df["session_id"] == sid]) if not vad_df.empty else 0
            n_tr = len(tr_df[tr_df["session_id"] == sid]) if not tr_df.empty else 0
            speakers = get_prosodia_speakers(data, session_id=sid)

            status_vad = "✅" if n_vad > 0 else "❌"
            status_tr = "✅" if n_tr > 0 else "❌"
            st.markdown(
                f"**{sid}** — "
                f"{status_vad} VAD ({n_vad} segs) | "
                f"{status_tr} Transcrição ({n_tr} msgs) | "
                f"Locutores: {', '.join(speakers) if speakers else '—'}"
            )

    # ==================================================================
    # Pré-visualização
    # ==================================================================
    st.divider()
    st.subheader("📋 Pré-visualização")

    vad_df = data.get("vad", __import__("pandas").DataFrame())
    tr_df = data.get("transcricao", __import__("pandas").DataFrame())

    if not vad_df.empty:
        with st.expander(f"Segmentos VAD — {len(vad_df)} registros"):
            st.dataframe(vad_df.head(100), use_container_width=True)

    if not tr_df.empty:
        with st.expander(f"Transcrições — {len(tr_df)} mensagens"):
            cols_display = [
                c for c in ["session_id", "SpeakerName", "Timestamp", "seconds", "word_count", "Text"]
                if c in tr_df.columns
            ]
            st.dataframe(tr_df[cols_display].head(100), use_container_width=True)

else:
    st.info("👆 Carregue os arquivos acima para começar.")

    st.markdown("### Formato esperado dos arquivos")

    with st.expander("JSON de prosódia (`Prosodia-<id>.json`)"):
        st.code(
            """{
  "result": {
    "vad": [
      { "start": 0.0, "end": 8.5 },
      { "start": 10.1, "end": 16.6 },
      ...
    ]
  }
}""",
            language="json",
        )

    with st.expander("CSV de transcrição (`Transcricao-<id>.csv`)"):
        st.markdown(
            """
| Coluna | Tipo | Descrição |
|--------|------|-----------|
| SpeakerName | str | Identificador do locutor (ex: `SPK_1`) |
| Timestamp | str | Tempo no formato `HH:MM:SS` |
| Text | str | Texto transcrito da fala |
"""
        )
