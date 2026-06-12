"""
Prosodia - Analise Geral do Projeto.

Consolida dados de todas as entrevistas de um projeto para gerar:
- visao agregada de metricas
- visualizacoes por locutor/features
- analise geral por IA com historico
"""

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from utils.prosodia_db import (
    init_db,
    get_project,
    get_audios_for_interviews,
    get_latest_project_analysis,
    get_project_analyses,
    save_project_analysis,
)
from utils.prosodia_loader import load_prosodia_from_uploads
from utils.prosodia_charts import create_speaker_stats, create_acoustic_timeline
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


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "projeto"


def _build_project_analysis_markdown(
    project_name: str,
    model: str,
    created_at: str,
    text: str,
    citations: list,
) -> str:
    lines = [
        "# Analise Geral do Projeto - Prosodia",
        "",
        f"- Projeto: {project_name or '-'}",
        f"- Modelo: {model or '-'}",
        f"- Gerado em: {created_at}",
        "",
        "## Resultado",
        "",
        text or "",
        "",
    ]

    if citations:
        lines.extend(["## Referencias", ""])
        for i, cit in enumerate(citations, 1):
            filename = cit.get("filename", "Documento")
            quote = cit.get("quote", "")
            lines.append(f"{i}. {filename}")
            if quote:
                lines.append(f"   - Trecho: {quote}")

    return "\n".join(lines)


def _append_result_to_kb(filename: str, content: str) -> tuple[bool, str]:
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI nao configurado para envio a base de conhecimento."
    if not prosodia_vs_id:
        return False, "PROSODIA_VECTOR_STORE_ID nao configurado."

    try:
        uploaded = client.files.create(
            file=(filename, content.encode("utf-8")),
            purpose="assistants",
        )
        client.vector_stores.files.create(
            vector_store_id=prosodia_vs_id,
            file_id=uploaded.id,
        )
        return True, filename
    except Exception as e:
        return False, str(e)


def _load_project_frames(audios: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    vad_parts = []
    tr_parts = []
    sinc_parts = []

    class _BytesFile:
        def __init__(self, data: bytes, name: str):
            self._buf = io.BytesIO(data)
            self.name = name

        def read(self):
            return self._buf.read()

        def seek(self, pos: int):
            return self._buf.seek(pos)

    for audio in audios:
        sid = audio.get("session_id", "")

        parsed = load_prosodia_from_uploads(
            json_files=[_BytesFile(audio["prosodia_json"], f"Prosodia-{sid}.json")] if audio.get("prosodia_json") else [],
            csv_files=[_BytesFile(audio["transcricao_csv"], f"Transcricao-{sid}.csv")] if audio.get("transcricao_csv") else [],
            sincronizado_files=[_BytesFile(audio["sincronizado_csv"], f"Sincronizado-{sid}.csv")] if audio.get("sincronizado_csv") else [],
        )

        vad_df = parsed.get("vad", pd.DataFrame())
        tr_df = parsed.get("transcricao", pd.DataFrame())

        if not vad_df.empty:
            if "session_id" not in vad_df.columns:
                vad_df = vad_df.copy()
                vad_df["session_id"] = sid
            vad_parts.append(vad_df)

        if not tr_df.empty:
            if "session_id" not in tr_df.columns:
                tr_df = tr_df.copy()
                tr_df["session_id"] = sid
            tr_parts.append(tr_df)

        if audio.get("sincronizado_csv"):
            try:
                sinc_df = pd.read_csv(io.BytesIO(audio["sincronizado_csv"]))
                if not sinc_df.empty:
                    if "session_id" not in sinc_df.columns:
                        sinc_df["session_id"] = sid
                    sinc_parts.append(sinc_df)
            except Exception:
                pass

    all_vad = pd.concat(vad_parts, ignore_index=True) if vad_parts else pd.DataFrame()
    all_tr = pd.concat(tr_parts, ignore_index=True) if tr_parts else pd.DataFrame()
    all_sinc = pd.concat(sinc_parts, ignore_index=True) if sinc_parts else pd.DataFrame()
    return all_vad, all_tr, all_sinc


def _build_transcript_sample(tr_df: pd.DataFrame, max_chars: int = 12000) -> str:
    if tr_df.empty or "Text" not in tr_df.columns:
        return ""

    work = tr_df.copy()
    sort_cols = [c for c in ["session_id", "seconds", "Timestamp"] if c in work.columns]
    if sort_cols:
        work = work.sort_values(sort_cols)

    lines = []
    for _, row in work.iterrows():
        sid = str(row.get("session_id", ""))
        ts = str(row.get("Timestamp", ""))
        speaker = str(row.get("SpeakerName", "?"))
        text = str(row.get("Text", "")).strip()
        if not text:
            continue
        prefix = f"[{sid}]"
        if ts:
            prefix += f"[{ts}]"
        lines.append(f"{prefix} {speaker}: {text}")

    full = "\n".join(lines)
    if len(full) > max_chars:
        return full[:max_chars] + "\n...[transcricao truncada]"
    return full


def _safe_word_sum(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    if "word_count" in df.columns:
        return int(df["word_count"].fillna(0).sum())
    if "Text" in df.columns:
        return int(df["Text"].fillna("").astype(str).apply(lambda t: len(t.split())).sum())
    return 0


# ------------------------------------------------------------------
# Carregar projeto
# ------------------------------------------------------------------
project_id = st.session_state.get("pros_project_id")
if not project_id:
    st.warning("Nenhum projeto selecionado. Volte a lista de projetos.")
    if st.button("<- Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

project = get_project(project_id)
if not project:
    st.error("Projeto nao encontrado.")
    if st.button("<- Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

audios = get_audios_for_interviews(project_id)

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
h1, h2, h3 = st.columns([5, 1, 1])
with h1:
    st.title(f"Analise Geral - {project.get('name', '')}")
with h2:
    st.write("")
    if st.button("Entrevistas", width="stretch"):
        st.switch_page("modules/prosodia/entrevistas.py")
with h3:
    st.write("")
    if st.button("Uploads", width="stretch"):
        st.switch_page("modules/prosodia/audios.py")

if not audios:
    st.info("Nenhuma entrevista disponivel para analise geral. Faca uploads primeiro.")
    st.stop()

all_vad, all_tr, all_sinc = _load_project_frames(audios)

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.header("Controles")
    analysis_mode = st.radio("Modo de analise", ["Rapida (1 chamada)", "Aprofundada (2 etapas)"])
    use_kb = st.checkbox("Usar Base de Conhecimento", value=True)
    openai_model = st.selectbox(
        "Modelo OpenAI",
        ["gpt-4.1-mini", "gpt-4.1", "gpt-4o"],
        key="prj_oai_model",
    )
    groq_key = st.text_input("Chave API Groq (alternativa)", type="password", key="prj_groq_key")
    groq_model = st.selectbox(
        "Modelo Groq",
        ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile"],
        key="prj_groq_model",
    )

# ------------------------------------------------------------------
# Resumo agregado
# ------------------------------------------------------------------
st.divider()
st.subheader("Resumo Agregado do Projeto")

n_interviews = len(audios)
n_speakers = all_tr["SpeakerName"].nunique() if not all_tr.empty and "SpeakerName" in all_tr.columns else 0
total_speech = float(all_vad["duration"].sum()) if not all_vad.empty and "duration" in all_vad.columns else 0.0
n_messages = len(all_tr)
n_words = _safe_word_sum(all_tr)

cov_total = int(sum(int(a.get("coverage_total", 0)) for a in audios))
ai_found = int(sum(int(a.get("coverage_ai_found", 0)) for a in audios))
kw_found = int(sum(int(a.get("coverage_kw_found", 0)) for a in audios))

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Entrevistas", n_interviews)
m2.metric("Locutores", n_speakers)
m3.metric("Fala total (s)", f"{total_speech:.1f}")
m4.metric("Mensagens", n_messages)
m5.metric("Palavras", n_words)
m6.metric("Cobertura IA/Keywords", f"{ai_found}/{cov_total} - {kw_found}/{cov_total}" if cov_total else "-")

if not all_tr.empty and "SpeakerName" in all_tr.columns:
    st.divider()
    st.subheader("Participacao por Locutor (Projeto)")
    fig_stats = create_speaker_stats(all_tr, session_id=None, title="Participacao geral por locutor")
    st.plotly_chart(fig_stats, width="stretch")

if not all_sinc.empty:
    st.divider()
    st.subheader("Features Acusticas (Projeto)")
    acoustic_cols = [
        "f0_media", "f0_variacao", "loudness_media", "loudness_variacao",
        "speaking_rate", "intonation_score",
        "emocao_angry", "emocao_happy", "emocao_neutral", "emocao_sad",
        "dim_arousal", "dim_dominance", "dim_valence",
    ]
    available = [c for c in acoustic_cols if c in all_sinc.columns]
    default_acoustic = [
        c for c in ["f0_media", "loudness_media", "emocao_happy", "emocao_neutral"]
        if c in available
    ]

    selected_acoustic = st.multiselect(
        "Indicadores acusticos",
        options=available,
        default=default_acoustic,
        key="prj_acoustic",
    )

    if selected_acoustic:
        fig_acoustic = create_acoustic_timeline(
            all_sinc,
            session_id=None,
            indicators=selected_acoustic,
            title="Features acusticas consolidadas",
        )
        st.plotly_chart(fig_acoustic, width="stretch")

# ------------------------------------------------------------------
# Construir contexto para IA
# ------------------------------------------------------------------
proj_ctx = {
    "nome": project.get("name", ""),
    "especialidade": project.get("especialidade", ""),
    "historico": project.get("historico", ""),
    "problemas": project.get("problemas", ""),
}

quality_counts = {"pass": 0, "warn": 0, "fail": 0, "pending": 0}
for a in audios:
    status = str(a.get("quality_status", "pending"))
    quality_counts[status if status in quality_counts else "pending"] += 1

tables_lines = [
    f"Entrevistas totais: {n_interviews}",
    f"Locutores totais: {n_speakers}",
    f"Fala total (s): {total_speech:.1f}",
    f"Mensagens totais: {n_messages}",
    f"Palavras totais: {n_words}",
    (
        "Qualidade (OK/Atencao/Problema/Pendente): "
        f"{quality_counts.get('pass', 0)}/{quality_counts.get('warn', 0)}/"
        f"{quality_counts.get('fail', 0)}/{quality_counts.get('pending', 0)}"
    ),
    f"Cobertura IA: {ai_found}/{cov_total}",
    f"Cobertura Keywords: {kw_found}/{cov_total}",
]

if not all_tr.empty and "SpeakerName" in all_tr.columns:
    if "word_count" in all_tr.columns:
        by_spk = (
            all_tr.groupby("SpeakerName")
            .agg(msgs=("Text", "count"), words=("word_count", "sum"))
            .reset_index()
        )
    else:
        by_spk = (
            all_tr.assign(_words=all_tr["Text"].fillna("").astype(str).apply(lambda t: len(t.split())))
            .groupby("SpeakerName")
            .agg(msgs=("Text", "count"), words=("_words", "sum"))
            .reset_index()
        )
    tables_lines.append("Participacao por locutor:\n" + by_spk.to_string(index=False))

tables_text = "\n\n".join(tables_lines)
transcript_sample = _build_transcript_sample(all_tr)

# ------------------------------------------------------------------
# Analise Geral por IA
# ------------------------------------------------------------------
st.divider()
st.subheader("Analise Geral por IA")

latest_analysis = get_latest_project_analysis(project_id)

if latest_analysis:
    st.caption(
        f"Ultima analise geral: {latest_analysis['created_at']} - Modelo: {latest_analysis.get('model', '-')}"
    )
    st.markdown(latest_analysis.get("analysis_text", ""))

    analysis_md = _build_project_analysis_markdown(
        project_name=project.get("name", ""),
        model=latest_analysis.get("model", ""),
        created_at=latest_analysis.get("created_at", ""),
        text=latest_analysis.get("analysis_text", ""),
        citations=latest_analysis.get("citations", []),
    )
    st.download_button(
        "Download Analise Geral (.md)",
        data=analysis_md,
        file_name=f"analise_geral_{_slugify(project.get('name', 'projeto'))}.md",
        mime="text/markdown",
        key="download_latest_project_analysis",
    )

    citations = latest_analysis.get("citations", [])
    if citations:
        with st.expander("Referencias da Base de Conhecimento"):
            for i, cit in enumerate(citations, 1):
                st.markdown(f"**[{i}]** {cit.get('filename', 'Documento')} - _{cit.get('quote', '')}_")

    history = get_project_analyses(project_id)
    with st.expander(f"Historico de analises gerais ({len(history)} registros)"):
        for an in history:
            st.markdown(f"**{an['created_at']} - {an.get('model', '-')}**")
            text = an.get("analysis_text", "")
            st.markdown(text[:500] + ("..." if len(text) > 500 else ""))
            st.divider()
else:
    st.info("Nenhuma analise geral disponivel. Clique em Gerar Analise Geral.")

btn_label = "Regenerar Analise Geral" if latest_analysis else "Gerar Analise Geral"
if st.button(btn_label, type="primary"):
    openai_client = get_openai_client()
    groq_client = None

    if not openai_client and groq_key:
        try:
            from groq import Groq

            groq_client = Groq(api_key=groq_key)
        except Exception:
            st.error("Groq nao instalado. Execute: pip install groq")
            st.stop()

    ai_client = openai_client or groq_client
    if not ai_client:
        st.error("Configure uma chave de API (OpenAI via .env ou Groq na barra lateral).")
        st.stop()

    vs_id = get_prosodia_vector_store_id() if use_kb else None

    with st.spinner("Gerando analise geral..."):
        try:
            result = {"text": "", "citations": []}
            user_prompt = build_prosodia_user_prompt(tables_text, proj_ctx, transcript_sample[:12000])

            if analysis_mode == "Rapida (1 chamada)":
                if openai_client:
                    result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        model=openai_model,
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=3500,
                    )
                else:
                    resp = groq_client.chat.completions.create(
                        model=groq_model,
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.5,
                        max_tokens=3500,
                    )
                    result = {"text": resp.choices[0].message.content, "citations": []}
            else:
                if openai_client:
                    stat_result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT_STATISTICAL,
                        user_prompt=user_prompt,
                        model=openai_model,
                        vector_store_id=None,
                        temperature=0.3,
                        max_tokens=2200,
                    )
                    strat_user = (
                        f"Analise estatistica previa:\n{stat_result['text']}\n\n"
                        f"Dados consolidados:\n{tables_text}"
                    )
                    strat_result = ai_create_analysis(
                        system_prompt=PROSODIA_SYSTEM_PROMPT_STRATEGIC,
                        user_prompt=strat_user,
                        model=openai_model,
                        vector_store_id=vs_id,
                        temperature=0.5,
                        max_tokens=2200,
                    )
                    result = {
                        "text": (
                            "## Analise Estatistica\n\n"
                            + stat_result["text"]
                            + "\n\n---\n\n## Analise Estrategica\n\n"
                            + strat_result["text"]
                        ),
                        "citations": strat_result.get("citations", []),
                    }
                else:
                    resp_stat = groq_client.chat.completions.create(
                        model=groq_model,
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT_STATISTICAL},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.3,
                        max_tokens=2200,
                    )
                    stat_text = resp_stat.choices[0].message.content
                    strat_user = f"Analise previa:\n{stat_text}\n\nDados:\n{tables_text}"
                    resp_strat = groq_client.chat.completions.create(
                        model=groq_model,
                        messages=[
                            {"role": "system", "content": PROSODIA_SYSTEM_PROMPT_STRATEGIC},
                            {"role": "user", "content": strat_user},
                        ],
                        temperature=0.5,
                        max_tokens=2200,
                    )
                    result = {
                        "text": (
                            "## Analise Estatistica\n\n"
                            + stat_text
                            + "\n\n---\n\n## Analise Estrategica\n\n"
                            + resp_strat.choices[0].message.content
                        ),
                        "citations": [],
                    }

            used_model = openai_model if openai_client else groq_model
            save_project_analysis(project_id, used_model, result.get("text", ""), result.get("citations", []))

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            kb_doc_name = (
                f"analise_geral_{_slugify(project.get('name', 'projeto'))}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            )
            kb_doc = _build_project_analysis_markdown(
                project_name=project.get("name", ""),
                model=used_model,
                created_at=now_str,
                text=result.get("text", ""),
                citations=result.get("citations", []),
            )
            kb_ok, kb_msg = _append_result_to_kb(kb_doc_name, kb_doc)

            st.success("Analise geral salva!")
            if kb_ok:
                st.caption(f"Base de conhecimento atualizada com: {kb_msg}")
            else:
                st.warning(f"Analise geral salva, mas nao foi possivel enviar para a base: {kb_msg}")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao gerar analise geral: {e}")
