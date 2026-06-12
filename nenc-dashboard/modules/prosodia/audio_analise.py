"""
Prosódia — Análise do Áudio.

Exibe:
- Análise de IA (última + histórico)
- Seção de Verificação de Qualidade (checks objetivos + cobertura de perguntas)
- Botões de Regenerar Análise e Reverificar Qualidade
"""

import io
import json
import re
import unicodedata
from datetime import datetime
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

_STOPWORDS_PT = {
    "a", "o", "as", "os", "de", "do", "da", "dos", "das", "e", "ou", "no", "na",
    "nos", "nas", "em", "para", "por", "com", "sem", "um", "uma", "uns", "umas",
    "que", "qual", "quais", "como", "onde", "quando", "se", "seu", "sua", "seus", "suas",
    "voce", "vocês", "voces", "ele", "ela", "eles", "elas", "isso", "isto", "aquele",
}


def _normalize_text(text: str) -> str:
    raw = str(text or "").lower().strip()
    raw = "".join(
        ch for ch in unicodedata.normalize("NFD", raw)
        if unicodedata.category(ch) != "Mn"
    )
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _tokenize_pt(text: str) -> set[str]:
    norm = _normalize_text(text)
    tokens = {
        t for t in norm.split()
        if len(t) >= 3 and t not in _STOPWORDS_PT
    }
    return tokens


def _extract_keyword_tokens(evidence_keywords: str) -> set[str]:
    """Extrai tokens de evidência no formato 'Termos encontrados: ...'."""
    text = str(evidence_keywords or "")
    match = re.search(r"Termos\s+encontrados\s*:\s*(.*?)(?:\(|$)", text, flags=re.IGNORECASE)
    if not match:
        return _tokenize_pt(text)
    raw_terms = match.group(1)
    parts = [p.strip() for p in raw_terms.split(",") if p.strip()]
    return _tokenize_pt(" ".join(parts))


def _timestamp_to_seconds(ts: str) -> float | None:
    """Converte timestamp HH:MM:SS(.ms) ou MM:SS(.ms) em segundos."""
    val = str(ts or "").strip()
    if not val:
        return None
    try:
        if re.match(r"^\d+(?:\.\d+)?$", val):
            return float(val)

        parts = val.split(":")
        nums = [float(p) for p in parts]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
    except Exception:
        return None
    return None


def _find_question_moment(
    transcricao_df: pd.DataFrame,
    question: str,
    evidence_ai: str,
    evidence_keywords: str,
) -> dict | None:
    """
    Localiza o melhor turno da transcrição para a pergunta.
    Prioridade: evidência IA > evidência keywords > tokens da pergunta.
    """
    if transcricao_df.empty or "Text" not in transcricao_df.columns:
        return None

    work = transcricao_df.copy().reset_index(drop=True)
    work["_text"] = work["Text"].fillna("").astype(str)
    work = work[work["_text"].str.strip() != ""].copy()
    if work.empty:
        return None

    if "seconds" in work.columns:
        work["_seconds"] = pd.to_numeric(work["seconds"], errors="coerce")
    else:
        work["_seconds"] = pd.Series([None] * len(work), dtype="float")

    if "Timestamp" in work.columns:
        ts_seconds = work["Timestamp"].apply(_timestamp_to_seconds)
        work["_seconds"] = work["_seconds"].where(work["_seconds"].notna(), ts_seconds)

    token_sources = []
    ai_tokens = _tokenize_pt(evidence_ai)
    if ai_tokens:
        token_sources.append(("ia", ai_tokens))

    kw_tokens = _extract_keyword_tokens(evidence_keywords)
    if kw_tokens:
        token_sources.append(("keywords", kw_tokens))

    q_tokens = _tokenize_pt(question)
    if q_tokens:
        token_sources.append(("pergunta", q_tokens))

    if not token_sources:
        return None

    for source, target_tokens in token_sources:
        best = None
        best_score = 0.0

        for idx, row in work.iterrows():
            row_tokens = _tokenize_pt(row["_text"])
            if not row_tokens:
                continue
            overlap = len(target_tokens & row_tokens)
            if overlap == 0:
                continue

            score = overlap / max(len(target_tokens), 1)
            row_seconds = row.get("_seconds")
            row_seconds = float(row_seconds) if pd.notna(row_seconds) else float("inf")

            is_better = False
            if score > best_score:
                is_better = True
            elif score == best_score and best is not None:
                best_seconds = best["seconds"] if best["seconds"] is not None else float("inf")
                if row_seconds < best_seconds:
                    is_better = True
                elif row_seconds == best_seconds and idx < best["index"]:
                    is_better = True
            elif score == best_score and best is None:
                is_better = True

            if is_better:
                best_score = score
                best = {
                    "index": idx,
                    "seconds": None if row_seconds == float("inf") else row_seconds,
                    "timestamp": str(row.get("Timestamp", "")) if "Timestamp" in work.columns else "",
                    "speaker": str(row.get("SpeakerName", "")) if "SpeakerName" in work.columns else "",
                    "text": str(row.get("_text", "")),
                    "source": source,
                    "score": score,
                }

        if best is not None:
            return best

    return None


def _slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "audio"


def _build_analysis_markdown(
    sid: str,
    project_name: str,
    model: str,
    created_at: str,
    text: str,
    citations: list,
) -> str:
    lines = [
        "# Análise de IA — Prosódia",
        "",
        f"- Sessão: {sid}",
        f"- Projeto: {project_name or '—'}",
        f"- Modelo: {model or '—'}",
        f"- Gerado em: {created_at}",
        "",
        "## Resultado",
        "",
        text or "",
        "",
    ]

    if citations:
        lines.extend(["## Referências", ""])
        for i, cit in enumerate(citations, 1):
            filename = cit.get("filename", "Documento")
            quote = cit.get("quote", "")
            lines.append(f"{i}. {filename}")
            if quote:
                lines.append(f"   - Trecho: {quote}")

    return "\n".join(lines)


def _build_quality_markdown(
    sid: str,
    project_name: str,
    created_at: str,
    overall_status: str,
    checks: list,
    coverage: list,
) -> str:
    n_pass = sum(1 for c in checks if c.get("status") == "pass")
    n_warn = sum(1 for c in checks if c.get("status") == "warn")
    n_fail = sum(1 for c in checks if c.get("status") == "fail")
    n_cov_total = len(coverage)
    n_kw_found = sum(1 for c in coverage if c.get("covered_keywords") is True)
    n_ai_found = sum(1 for c in coverage if c.get("covered_ai") is True)

    payload = {
        "overall_status": overall_status,
        "checks": checks,
        "coverage": coverage,
    }

    lines = [
        "# Verificação de Qualidade — Prosódia",
        "",
        f"- Sessão: {sid}",
        f"- Projeto: {project_name or '—'}",
        f"- Gerado em: {created_at}",
        f"- Status geral: {overall_status}",
        "",
        "## Resumo",
        "",
        f"- Checks OK: {n_pass}",
        f"- Alertas: {n_warn}",
        f"- Problemas: {n_fail}",
        f"- Cobertura IA: {n_ai_found}/{n_cov_total}",
        f"- Cobertura Keywords: {n_kw_found}/{n_cov_total}",
        "",
        "## Dados Completos (JSON)",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(lines)


def _append_result_to_kb(filename: str, content: str) -> tuple[bool, str]:
    """
    Adiciona documento de resultado (análise/qualidade) ao vector store da Prosódia.
    Não lança exceção para não quebrar o fluxo principal.
    """
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI não configurado para envio à base de conhecimento."
    if not prosodia_vs_id:
        return False, "PROSODIA_VECTOR_STORE_ID não configurado."

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

# ------------------------------------------------------------------
# Carregar áudio do banco
# ------------------------------------------------------------------
audio_id = st.session_state.get("pros_audio_id")
project_id = st.session_state.get("pros_project_id")

if not audio_id:
    st.warning("Nenhuma entrevista selecionada.")
    if st.button("← Entrevistas"):
        st.switch_page("modules/prosodia/entrevistas.py")
    st.stop()

audio = get_audio(audio_id)
if not audio:
    st.error("Entrevista não encontrada no banco.")
    if st.button("← Entrevistas"):
        st.switch_page("modules/prosodia/entrevistas.py")
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
    if st.button("← Entrevistas", width='stretch'):
        st.switch_page("modules/prosodia/entrevistas.py")

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

# Ordem visual da página: Qualidade primeiro, Análise depois
quality_section = st.container()
analysis_section = st.container()

# ------------------------------------------------------------------
# Seção 1: Análise de IA
# ------------------------------------------------------------------
with analysis_section:
    st.subheader("🤖 Análise de IA")

    latest_analysis = get_latest_analysis(audio_id)

    if latest_analysis:
        st.caption(f"Última análise: {latest_analysis['created_at']} — Modelo: {latest_analysis.get('model', '—')}")
        st.markdown(latest_analysis["analysis_text"])

        analysis_md = _build_analysis_markdown(
            sid=sid,
            project_name=project.get("name", "") if project else "",
            model=latest_analysis.get("model", ""),
            created_at=latest_analysis.get("created_at", ""),
            text=latest_analysis.get("analysis_text", ""),
            citations=latest_analysis.get("citations", []),
        )
        st.download_button(
            "⬇️ Download Análise IA (.md)",
            data=analysis_md,
            file_name=f"analise_ia_{_slugify(sid)}.md",
            mime="text/markdown",
            key="download_latest_ai_analysis",
        )

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

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                kb_doc_name = f"analise_ia_{_slugify(sid)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                kb_doc = _build_analysis_markdown(
                    sid=sid,
                    project_name=project.get("name", "") if project else "",
                    model=used_model,
                    created_at=now_str,
                    text=result.get("text", ""),
                    citations=result.get("citations", []),
                )
                kb_ok, kb_msg = _append_result_to_kb(kb_doc_name, kb_doc)

                st.success("Análise salva!")
                if kb_ok:
                    st.caption(f"Base de conhecimento atualizada com: {kb_msg}")
                else:
                    st.warning(f"Análise salva, mas não foi possível enviar para a base: {kb_msg}")
                st.rerun()

            except Exception as e:
                st.error(f"Erro ao gerar análise: {e}")

# ------------------------------------------------------------------
# Seção 2: Verificação de Qualidade
# ------------------------------------------------------------------
with quality_section:
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
        n_cov_total = len(coverage)
        n_kw_found = sum(1 for c in coverage if c.get("covered_keywords") is True)
        n_ai_found = sum(1 for c in coverage if c.get("covered_ai") is True)

        badge = status_badge(overall)
        overall_label = {"pass": "OK", "warn": "Atenção", "fail": "Problema"}.get(overall, overall)
        st.markdown(f"### {badge} Status Geral: **{overall_label}**")
        st.caption(f"Última verificação: {quality.get('created_at', '—')}")

        qc1, qc2, qc3, qc4, qc5 = st.columns(5)
        qc1.metric("✅ Checks OK", n_pass)
        qc2.metric("⚠️ Alertas", n_warn)
        qc3.metric("❌ Problemas", n_fail)
        qc4.metric("🧠 IA cobertas", f"{n_ai_found}/{n_cov_total}")
        qc5.metric("🔎 Keywords cobertas", f"{n_kw_found}/{n_cov_total}")

        quality_md = _build_quality_markdown(
            sid=sid,
            project_name=project.get("name", "") if project else "",
            created_at=quality.get("created_at", ""),
            overall_status=overall,
            checks=checks,
            coverage=coverage,
        )
        st.download_button(
            "⬇️ Download Verificação de Qualidade (.md)",
            data=quality_md,
            file_name=f"qualidade_entrevista_{_slugify(sid)}.md",
            mime="text/markdown",
            key="download_latest_quality_check",
        )

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
                coverage_records = []
                for c in coverage:
                    kw = c.get("covered_keywords")
                    ai_cov = c.get("covered_ai")
                    evidence_kw = c.get("evidence_keywords")
                    evidence_ai = c.get("evidence_ai")

                    # Compatibilidade com registros antigos (sem campos separados)
                    if evidence_kw is None:
                        evidence_kw = c.get("evidence") if kw is not None else ""
                    if evidence_ai is None:
                        evidence_ai = c.get("evidence") if ai_cov is not None else ""

                    coverage_records.append({
                        "question": c.get("question", ""),
                        "evidence_keywords": evidence_kw or "",
                        "evidence_ai": evidence_ai or "",
                    })

                    cov_rows.append({
                        "Pergunta": c.get("question", ""),
                        "Keywords": "✅" if kw else ("❌" if kw is False else "—"),
                        "IA": "✅" if ai_cov else ("❌" if ai_cov is False else "—"),
                        "Confiança": f"{c.get('confidence', 0)*100:.0f}%",
                        "Evidência Keywords": evidence_kw or "",
                        "Evidência IA": evidence_ai or "",
                    })

                coverage_table_event = st.dataframe(
                    pd.DataFrame(cov_rows),
                    width='stretch',
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key=f"coverage_table_select_{audio_id}",
                )

                selected_rows = []
                if coverage_table_event:
                    selection = getattr(coverage_table_event, "selection", None)
                    if isinstance(selection, dict):
                        selected_rows = selection.get("rows", [])
                    elif selection is not None:
                        selected_rows = getattr(selection, "rows", []) or []

                if st.button("🎯 Ir para momento na Timeline", key=f"go_timeline_moment_{audio_id}"):
                    if not selected_rows:
                        st.info("Selecione uma pergunta na tabela de cobertura para localizar o momento na entrevista.")
                    else:
                        idx = int(selected_rows[0])
                        if idx < 0 or idx >= len(coverage_records):
                            st.warning("Não foi possível identificar a pergunta selecionada.")
                        else:
                            selected = coverage_records[idx]
                            focus = _find_question_moment(
                                transcricao_df=tr_df,
                                question=selected.get("question", ""),
                                evidence_ai=selected.get("evidence_ai", ""),
                                evidence_keywords=selected.get("evidence_keywords", ""),
                            )
                            if not focus:
                                st.warning("Não foi possível localizar esse momento na transcrição.")
                            else:
                                st.session_state["pros_timeline_focus"] = {
                                    "audio_id": audio_id,
                                    "session_id": sid,
                                    "question": selected.get("question", ""),
                                    "seconds": focus.get("seconds"),
                                    "timestamp": focus.get("timestamp", ""),
                                    "speaker": focus.get("speaker", ""),
                                    "text": focus.get("text", ""),
                                    "source": focus.get("source", ""),
                                }
                                st.switch_page("modules/prosodia/audio_timeline.py")
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

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                kb_doc_name = f"qualidade_entrevista_{_slugify(sid)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                kb_doc = _build_quality_markdown(
                    sid=sid,
                    project_name=project.get("name", "") if project else "",
                    created_at=now_str,
                    overall_status=new_overall,
                    checks=new_checks,
                    coverage=cov_merged,
                )
                kb_ok, kb_msg = _append_result_to_kb(kb_doc_name, kb_doc)

                st.success("Qualidade reverificada!")
                if kb_ok:
                    st.caption(f"Base de conhecimento atualizada com: {kb_msg}")
                else:
                    st.warning(f"Qualidade salva, mas não foi possível enviar para a base: {kb_msg}")
                st.rerun()
            except Exception as e:
                st.error(f"Erro na reverificação: {e}")
