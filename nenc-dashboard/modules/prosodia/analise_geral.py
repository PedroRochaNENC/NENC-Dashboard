"""
Prosodia - Analise Geral do Projeto.

Consolida dados de todos os áudios de um projeto para gerar:
- visao agregada de metricas
- visualizacoes por locutor/features
- analise geral por IA com historico
"""

import io
import logging
from datetime import datetime

import pandas as pd
import streamlit as st
from utils import auth, ui
from utils.icons import page_title

_user = auth.require_module("prosodia")
pode_editar = auth.can_write(_user)
from fpdf import FPDF

from utils.prosodia_db import (
    init_db,
    get_project,
    get_audios_for_interviews,
    attach_audio_blobs,
    get_latest_project_analysis,
    get_project_analyses,
    save_project_analysis,
    delete_project_analyses,
)
from utils.prosodia_loader import (
    extract_topic_from_text,
    load_prosodia_from_uploads,
    normalizar_sincronizado,
)
from utils.prosodia_signals import (
    detectar_divergencias,
    divergencias_texto,
    emotion_distribution_text,
    formatar_tempo,
    selecionar_momentos_ativacao,
    tem_sentimento_texto,
    texto_sentimento_resumo,
)
# from utils.prosodia_powerbi_export import export_project_to_powerbi_excel
from utils.prosodia_charts import (
    create_speaker_stats,
    create_project_acoustic_comparison,
    create_project_emotion_distribution,
    create_project_text_sentiment_distribution,
    create_project_word_ranking,
)
from utils.prosodia_prompts import (
    get_prosodia_project_system_prompt,
    build_project_user_prompt,
    secoes_sentimento,
)
from utils.ai_provider import (
    PROVIDER_OPENAI,
    add_document_to_vector_store,
    chat_completion,
    generate_analysis,
    get_openai_client,
    get_prosodia_vector_store_id,
    create_analysis as ai_create_analysis,
)
from utils.kb_attributes import build_kb_filter, project_document

_LOGGER = logging.getLogger(__name__)

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
        "# Analise Geral do Projeto - NencBoost",
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


def _sanitize(text: str) -> str:
    """Remove caracteres fora do latin-1 para compatibilidade com fontes PDF."""
    return str(text or "").encode("latin-1", errors="replace").decode("latin-1")


def _build_project_analysis_pdf(
    project_name: str,
    project_info: dict,
    model: str,
    created_at: str,
    text: str,
    citations: list,
    acoustic_summary: str = "",
) -> bytes:
    """Gera um PDF formatado com a análise geral do projeto."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Titulo principal
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _sanitize("Relatório de Análise Geral - Prosódia"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Info do projeto
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, _sanitize(f"Projeto: {project_name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _sanitize(f"Modelo de IA: {model}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, _sanitize(f"Gerado em: {created_at}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Contexto/Metadados do projeto
    if project_info:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _sanitize("Contexto do Projeto"), new_x="LMARGIN", new_y="NEXT")
        pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 190, pdf.get_y())
        pdf.ln(2)
        
        for label, key in [
            ("Especialidade", "especialidade"),
            ("Histórico", "historico"),
            ("Problemas Centrais", "problemas"),
            ("Briefing", "briefing"),
        ]:
            val = project_info.get(key, "")
            if val:
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(0, 5, _sanitize(f"{label}:"), new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 9)
                pdf.multi_cell(0, 4.5, _sanitize(val), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1)

    # Resumo Acústico (se houver)
    if acoustic_summary:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _sanitize("Métricas Acústicas Resumidas"), new_x="LMARGIN", new_y="NEXT")
        pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 190, pdf.get_y())
        pdf.ln(2)
        
        pdf.set_font("Courier", "", 8)
        for line in acoustic_summary.splitlines():
            pdf.cell(0, 4, _sanitize(line[:120]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    # Resultado da Análise de IA
    if text:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _sanitize("Resultado da Análise Geral"), new_x="LMARGIN", new_y="NEXT")
        pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 190, pdf.get_y())
        pdf.ln(2)

        # Processar markdown simplificado
        lines = text.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                pdf.ln(2.5)
                continue
            
            # Cabeçalhos
            if line_str.startswith("### "):
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(0, 6, _sanitize(line_str[4:]), new_x="LMARGIN", new_y="NEXT")
            elif line_str.startswith("## "):
                pdf.ln(1)
                pdf.set_font("Helvetica", "B", 11)
                pdf.cell(0, 7, _sanitize(line_str[3:]), new_x="LMARGIN", new_y="NEXT")
            elif line_str.startswith("# "):
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", 13)
                pdf.cell(0, 9, _sanitize(line_str[2:]), new_x="LMARGIN", new_y="NEXT")
            elif line_str.startswith("- ") or line_str.startswith("* "):
                pdf.set_font("Helvetica", "", 9)
                pdf.multi_cell(0, 4.5, _sanitize(f"  - {line_str[2:]}"), new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_font("Helvetica", "", 9)
                pdf.multi_cell(0, 4.5, _sanitize(line_str), new_x="LMARGIN", new_y="NEXT")
                
        pdf.ln(3)

    # Referências/Citações
    if citations:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _sanitize("Referências e Citações"), new_x="LMARGIN", new_y="NEXT")
        pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 190, pdf.get_y())
        pdf.ln(2)
        
        for i, cit in enumerate(citations, 1):
            filename = cit.get("filename", "Documento")
            quote = cit.get("quote", "")
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 5, _sanitize(f"{i}. {filename}"), new_x="LMARGIN", new_y="NEXT")
            if quote:
                pdf.set_font("Helvetica", "I", 8.5)
                pdf.multi_cell(0, 4, _sanitize(f"   Trecho: \"{quote}\""), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def _append_result_to_kb(
    filename: str, content: str, project_id=None
) -> tuple[bool, str]:
    client = get_openai_client()
    prosodia_vs_id = get_prosodia_vector_store_id()

    if not client:
        return False, "OpenAI nao configurado para envio a base de conhecimento."
    if not prosodia_vs_id:
        return False, "A base de conhecimento do NencBoost nao esta configurada para a organizacao ativa."

    try:
        add_document_to_vector_store(
            prosodia_vs_id,
            filename,
            content.encode("utf-8"),
            project_document("prosodia", project_id, escopo="analise"),
            wait=False,
        )
        return True, filename
    except Exception as e:
        return False, str(e)


def _load_project_frames(
    project_id: int, audios: list[dict]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Dataframes consolidados do projeto, lidos do conteúdo dos áudios.

    Os áudios chegam da tabela de entrevistas, que carrega só metadado; os
    blobs vêm aqui, onde vão ser de fato lidos.
    """
    audios = attach_audio_blobs(project_id, audios)

    vad_parts = []
    tr_parts = []
    sinc_parts = []
    sincronizados_ilegiveis = []

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
                    sinc_parts.append(normalizar_sincronizado(sinc_df, sid))
            except Exception:
                # Engolir aqui apaga o audio da analise sem deixar rastro: o
                # prompt sai sem as metricas dele e o relatorio conclui que nao
                # havia dados. O projeto segue com os demais, mas registrado.
                _LOGGER.exception(
                    "Sincronizado ilegivel no audio %s do projeto %s; "
                    "ele fica fora das metricas acusticas do projeto.",
                    sid,
                    project_id,
                )
                sincronizados_ilegiveis.append(sid)

    if sincronizados_ilegiveis:
        st.warning(
            "Não foi possível ler o NencBoost de {} áudio(s): {}. "
            "Eles ficam de fora das métricas acústicas deste relatório.".format(
                len(sincronizados_ilegiveis), ", ".join(sincronizados_ilegiveis)
            )
        )

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


def _calculate_top_words_text(tr_df: pd.DataFrame, top_n: int = 30) -> str:
    if tr_df.empty or "Text" not in tr_df.columns:
        return "Nenhuma palavra encontrada."
    import re
    import unicodedata
    from collections import Counter
    
    stopwords = {
        "a", "o", "as", "os", "de", "do", "da", "dos", "das", "e", "ou", "no", "na",
        "nos", "nas", "em", "para", "por", "com", "sem", "um", "uma", "uns", "umas",
        "que", "qual", "quais", "como", "onde", "quando", "se", "seu", "sua", "seus", "suas",
        "voce", "vocês", "voces", "ele", "ela", "eles", "elas", "isso", "isto", "aquele",
        "mas", "tambem", "mais", "muito", "entao", "aqui", "la", "sim", "nao", "pra", "pro",
        "este", "esta", "estes", "estas", "tudo", "todo", "toda", "todos", "todas", "ser",
        "ter", "ir", "com", "por", "para", "uma", "um", "do", "da", "no", "na", "ao", "aos",
        "pelo", "pela", "pelos", "pelas", "num", "numa", "neste", "nesta", "disso", "disto",
        "dele", "dela", "deles", "delas", "mim", "me", "te", "se", "nos", "vos", "lhe", "lhes",
        "meu", "minha", "meus", "minhas", "teu", "tua", "teus", "tuas", "nosso", "nossa",
        "nossos", "nossas", "vosso", "vossa", "vossos", "vossas", "qualquer", "quaisquer",
        "algum", "alguma", "alguns", "algumas", "nenhum", "nenhuma", "outro", "outra", "outros",
        "outras", "mesmo", "mesma", "mesmos", "mesmas", "proprio", "propria", "proprios", "proprias",
        "acho", "acha", "achar", "coisa", "coisas", "aqui", "dai", "tipo", "ne", "ta", "entao",
        "porque", "porquê", "pois", "assim", "sobre", "outro", "outra", "outros", "outras",
        "gente", "entao", "bem", "vou", "vai", "tao", "aqui", "tudo"
    }
    
    words = []
    for text in tr_df["Text"].fillna("").astype(str):
        text_norm = "".join(
            ch for ch in unicodedata.normalize("NFD", text.lower())
            if unicodedata.category(ch) != "Mn"
        )
        for word in re.findall(r"\b[a-z]{3,}\b", text_norm):
            if word not in stopwords:
                words.append(word)
                
    counts = Counter(words).most_common(top_n)
    if not counts:
        return "Nenhuma palavra relevante encontrada."
    
    lines = ["| Palavra | Menções |", "|---|---|"]
    for w, c in counts:
        lines.append(f"| {w} | {c} |")
    return "\n".join(lines)


def _extract_high_activation_moments(sinc_df: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    # Mesma regra da análise do áudio; o mesmo trecho de fala conta uma vez.
    return selecionar_momentos_ativacao(sinc_df, top_n=top_n, min_altos=5)


def _limpo(valor) -> str:
    return "" if valor is None or (isinstance(valor, float) and pd.isna(valor)) else str(valor).strip()


def _tempo_do_momento(row) -> str:
    """O início do segmento do VAD, onde a ativação foi medida."""
    return formatar_tempo(row.get("seconds", row.get("start_s"))) or _limpo(row.get("Timestamp", ""))


def _group_similar_topics(moments: list) -> list:
    if not moments:
        return []
        
    import re
    from collections import Counter
    
    word_occurrences = []
    moment_words = []
    for m in moments:
        topic = m.get("topic", "") or ""
        words = [w.lower() for w in re.findall(r"\b[a-z]{3,}\b", topic.lower())]
        moment_words.append((m, words))
        word_occurrences.extend(words)
        
    word_counts = Counter(word_occurrences)
    
    groups = {}
    core_words_by_freq = [w for w, c in word_counts.most_common() if c > 1]
    
    assigned_moments = set()
    
    # Primeiro pass: agrupar por palavras compartilhadas
    for core in core_words_by_freq:
        for idx, (m, words) in enumerate(moment_words):
            if idx in assigned_moments:
                continue
            if core in words:
                if core not in groups:
                    groups[core] = []
                groups[core].append(m)
                assigned_moments.add(idx)
                
    # Segundo pass: momentos restantes ganham grupo próprio
    for idx, (m, words) in enumerate(moment_words):
        if idx in assigned_moments:
            continue
        core = words[0] if words else "Geral"
        if core not in groups:
            groups[core] = []
        groups[core].append(m)
        assigned_moments.add(idx)
        
    grouped_results = []
    for core_word, group_moments in groups.items():
        topic_counts = Counter(m.get("topic", "") for m in group_moments)
        representative_name = topic_counts.most_common(1)[0][0]
        
        arousals = [m.get("dim_arousal", 0.0) for m in group_moments]
        avg_arousal = sum(arousals) / len(arousals) if arousals else 0.0
        
        sessions = sorted(list(set(m.get("session_id", "") for m in group_moments)))
        sessions_str = ", ".join(sessions)
        
        best_moment = max(group_moments, key=lambda m: m.get("dim_arousal", 0.0))
        example_quote = f"\"{best_moment.get('Text', '')}\" ({best_moment.get('SpeakerName', '')})"
        
        grouped_results.append({
            "topic_group": representative_name,
            "count": len(group_moments),
            "avg_arousal": avg_arousal,
            "sessions": sessions_str,
            "example": example_quote,
        })
        
    grouped_results.sort(key=lambda x: (x["count"], x["avg_arousal"]), reverse=True)
    return grouped_results


def _format_high_activation_text(top_moments: pd.DataFrame) -> str:
    if top_moments.empty:
        return "Nenhum momento de alta ativação encontrado."
        
    # Valência e dominância andam junto com a ativação: pico com valência
    # negativa é fricção, com valência positiva é entusiasmo, e sem elas os
    # dois chegam ao modelo como o mesmo número.
    lines = [
        "| Tópico | Áudio | Locutor | Tempo | Fala | Arousal | Valência | Dominância | Variação Pitch | Variação Volume |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    moments_list = []
    for _, row in top_moments.iterrows():
        sid = row.get("session_id", "")
        speaker = _limpo(row.get("SpeakerName", ""))
        ts = _tempo_do_momento(row)
        text = _limpo(row.get("Text", "")).replace("\n", " ")
        topic = extract_topic_from_text(text)
        arousal = f"{row.get('dim_arousal', 0.0):.2f}" if pd.notna(row.get('dim_arousal')) else "-"
        valence = f"{row.get('dim_valence', 0.0):.2f}" if pd.notna(row.get('dim_valence')) else "-"
        dominance = f"{row.get('dim_dominance', 0.0):.2f}" if pd.notna(row.get('dim_dominance')) else "-"
        f0_var = f"{row.get('f0_variacao', 0.0):.2f}" if pd.notna(row.get('f0_variacao')) else "-"
        ld_var = f"{row.get('loudness_variacao', 0.0):.2f}" if pd.notna(row.get('loudness_variacao')) else "-"
        lines.append(
            f"| {topic} | {sid} | {speaker} | {ts} | \"{text}\" | {arousal} | {valence} | "
            f"{dominance} | {f0_var} | {ld_var} |"
        )
        
        moments_list.append({
            "session_id": sid,
            "SpeakerName": speaker,
            "Timestamp": ts,
            "Text": text,
            "dim_arousal": float(row.get("dim_arousal", 0.0)) if pd.notna(row.get("dim_arousal")) else 0.0,
            "topic": topic,
        })
        
    out = "\n".join(lines)
    
    # Add grouped topics to LLM prompt context
    grouped = _group_similar_topics(moments_list)
    if grouped:
        group_lines = [
            "\n### Tópicos Consolidados de Maior Ativação Prosódica (Agrupados):",
            "| Tópico Consolidado | Ocorrências | Arousal Médio | Áudios Relacionados | Exemplo de Destaque |",
            "|---|---|---|---|---|",
        ]
        for g in grouped:
            group_lines.append(
                f"| {g['topic_group']} | {g['count']} | {g['avg_arousal']:.2f} | {g['sessions']} | {g['example']} |"
            )
        out += "\n" + "\n".join(group_lines)
        
    return out


def _calculate_questions_activation(audios: list, all_sinc: pd.DataFrame) -> list:
    if not audios or all_sinc.empty:
        return []
        
    from utils.prosodia_db import get_latest_quality_check
    import re
    
    def _get_clean_tokens(txt: str) -> set:
        if not txt or not isinstance(txt, str):
            return set()
        cleaned = re.sub(r"[^\w\s]", " ", txt.lower())
        return {w for w in cleaned.split() if len(w) >= 3}
        
    question_matches = {}
    
    for audio in audios:
        sid = audio.get("session_id", "")
        audio_id = audio.get("id")
        
        quality = get_latest_quality_check(audio_id)
        if not quality or not quality.get("coverage"):
            continue
            
        sinc_sub = all_sinc[all_sinc["session_id"] == sid]
        if sinc_sub.empty:
            continue
            
        segment_tokens = []
        for _, row in sinc_sub.iterrows():
            txt = str(row.get("Text", ""))
            segment_tokens.append((row, _get_clean_tokens(txt)))
            
        for item in quality["coverage"]:
            q = item.get("question", "").strip()
            if not q:
                continue
            is_covered = item.get("covered_keywords") or item.get("covered_ai")
            evidence = item.get("evidence", "").strip()
            
            if not is_covered or not evidence:
                continue
                
            evidence_tokens = _get_clean_tokens(evidence)
            if not evidence_tokens:
                continue
                
            best_row = None
            best_overlap = 0.0
            
            for row, tokens in segment_tokens:
                if not tokens:
                    continue
                overlap = len(evidence_tokens & tokens) / len(evidence_tokens)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_row = row
                    
            if best_row is not None and best_overlap >= 0.20:
                if q not in question_matches:
                    question_matches[q] = []
                    
                question_matches[q].append({
                    "session_id": sid,
                    "SpeakerName": str(best_row.get("SpeakerName", "Desconhecido")),
                    "Text": str(best_row.get("Text", "")),
                    "dim_arousal": float(best_row.get("dim_arousal", 0.0)) if pd.notna(best_row.get("dim_arousal")) else 0.0,
                    "f0_variacao": float(best_row.get("f0_variacao", 0.0)) if pd.notna(best_row.get("f0_variacao")) else 0.0,
                    "loudness_variacao": float(best_row.get("loudness_variacao", 0.0)) if pd.notna(best_row.get("loudness_variacao")) else 0.0,
                    "Timestamp": str(best_row.get("Timestamp", "")),
                })
                
    results = []
    for q, matches in question_matches.items():
        arousals = [m["dim_arousal"] for m in matches]
        f0_vars = [m["f0_variacao"] for m in matches]
        ld_vars = [m["loudness_variacao"] for m in matches]
        
        avg_arousal = sum(arousals) / len(arousals) if arousals else 0.0
        avg_f0_var = sum(f0_vars) / len(f0_vars) if f0_vars else 0.0
        avg_ld_var = sum(ld_vars) / len(ld_vars) if ld_vars else 0.0
        
        best = max(matches, key=lambda m: m["dim_arousal"])
        example_str = f"\"{best['Text']}\" ({best['SpeakerName']}, {best['Timestamp']})"
        
        results.append({
            "question": q,
            "count": len(matches),
            "avg_arousal": avg_arousal,
            "avg_f0_var": avg_f0_var,
            "avg_ld_var": avg_ld_var,
            "example": example_str,
        })
        
    results.sort(key=lambda x: (x["avg_arousal"], x["count"]), reverse=True)
    return results


def _load_individual_analyses(audios: list[dict]) -> str:
    from utils.prosodia_db import get_latest_analysis
    lines = []
    for a in audios:
        sid = a.get("session_id", "")
        analysis = get_latest_analysis(a["id"])
        if analysis and analysis.get("analysis_text"):
            lines.append(f"### Áudio: {sid}")
            lines.append(f"Modelo da Análise: {analysis.get('model', '-')}")
            lines.append(analysis["analysis_text"])
            lines.append("\n---\n")
    return "\n".join(lines) if lines else "Nenhuma análise individual encontrada para os áudios."


def _calculate_acoustic_summary_text(sinc_df: pd.DataFrame) -> str:
    if sinc_df.empty:
        return "Nenhuma métrica acústica disponível."
        
    # Dominância entra junto com ativação e valência: é o trio VAD completo, e
    # sem ela não se separa uma crítica firme de um desabafo hesitante.
    metrics = ["f0_media", "f0_variacao", "loudness_media", "loudness_variacao", "speaking_rate", "dim_arousal", "dim_valence", "dim_dominance"]
    available = [m for m in metrics if m in sinc_df.columns]
    
    if not available:
        return "Nenhuma métrica compatível disponível."
        
    agg_sess = sinc_df.groupby("session_id")[available].mean().reset_index()
    lines = ["### Médias por Áudio", ""]
    cols_header = "| Áudio | " + " | ".join(available) + " |"
    cols_sep = "|---| " + " | ".join(["---"] * len(available)) + " |"
    lines.append(cols_header)
    lines.append(cols_sep)
    for _, row in agg_sess.iterrows():
        row_str = f"| {row['session_id']} | " + " | ".join(f"{row[m]:.3f}" if pd.notna(row[m]) else "-" for m in available) + " |"
        lines.append(row_str)
        
    lines.append("")
    
    if "SpeakerName" in sinc_df.columns:
        agg_spk = sinc_df.groupby("SpeakerName")[available].mean().reset_index()
        lines.append("### Médias por Locutor")
        lines.append("")
        cols_header = "| Locutor | " + " | ".join(available) + " |"
        lines.append(cols_header)
        lines.append(cols_sep)
        for _, row in agg_spk.iterrows():
            row_str = f"| {row['SpeakerName']} | " + " | ".join(f"{row[m]:.3f}" if pd.notna(row[m]) else "-" for m in available) + " |"
            lines.append(row_str)
            
    return "\n".join(lines)


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
    st.session_state.pop("pros_project_id", None)
    st.error("Projeto nao encontrado.")
    if st.button("<- Projetos"):
        st.switch_page("modules/prosodia/projetos.py")
    st.stop()

audios = get_audios_for_interviews(project_id)

# ------------------------------------------------------------------
# Header
# ------------------------------------------------------------------
ui.inject_theme()
ui.breadcrumb("NencBoost", project.get("name", ""), "Análise Geral")
h1, h2, h3 = st.columns([5, 1, 1])
with h1:
    page_title(
        "chart-bar", "Análise Geral", project.get("name", "")
    )
with h2:
    st.write("")
    if st.button("Áudios", width="stretch"):
        st.switch_page("modules/prosodia/entrevistas.py")
with h3:
    st.write("")
    if pode_editar and st.button("Uploads", width="stretch"):
        st.switch_page("modules/prosodia/audios.py")

# --------------------------------------------------------------------
# Exportacao para Power BI — desativada.
#
# O codigo fica: utils/prosodia_powerbi_export.py e
# tests/test_prosodia_powerbi_export.py seguem intactos e testados. So
# nao ha caminho ate eles pela interface. Para reativar, descomente
# este bloco e o import de export_project_to_powerbi_excel no topo.
# --------------------------------------------------------------------
# st.divider()
# st.subheader("Exportação para Power BI")
# st.caption(
#     "Baixe todas as tabelas relacionáveis do projeto, incluindo históricos de "
#     "análises, verificações de qualidade e ativações prosódicas."
# )
# try:
#     powerbi_workbook, powerbi_filename = export_project_to_powerbi_excel(project_id)
# except ValueError as error:
#     st.error(f"Não foi possível preparar a exportação: {error}")
# else:
#     st.download_button(
#         "Exportar para Power BI (.xlsx)",
#         data=powerbi_workbook,
#         file_name=powerbi_filename,
#         mime=(
#             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#         ),
#         key=f"download_powerbi_{project_id}",
#         type="primary",
#         use_container_width=False,
#     )
#
# with st.expander("Guia de importação e relacionamentos"):
#     st.markdown(
#         """
# 1. No Power BI Desktop, selecione **Obter Dados > Excel** e escolha o arquivo
#    exportado. Importe todas as abas.
# 2. Use `Projeto[id]` como a dimensão principal e relacione-o com
#    `Perguntas_Projeto[project_id]`, `Entrevistas[project_id]` e
#    `Analises_Projeto[project_id]`.
# 3. Use `Entrevistas[id]` para relacionar `Segmentos_VAD`, `Transcricoes`,
#    `Dados_Sincronizados`, `Analises_Entrevista`,
#    `Verificacoes_Qualidade` e `Momentos_Alta_Ativacao` por `audio_id`.
# 4. Relacione as tabelas filhas de IA e qualidade pelos IDs de origem:
#    `Citacoes_Analise_Entrevista[analysis_id]`,
#    `Citacoes_Analise_Projeto[project_analysis_id]`,
#    `Checks_Qualidade[quality_check_id]` e
#    `Cobertura_Perguntas[quality_check_id]`.
#
# As abas com sufixo numérico, como `Dados_Sincronizados_2`, são continuações da
# mesma tabela quando o limite de linhas do Excel é atingido. Anexe-as no Power
# Query antes de criar os relacionamentos.
#         """
#     )
#
if not audios:
    st.info("Nenhum áudio disponível para análise geral. Faça uploads primeiro.")
    st.stop()

all_vad, all_tr, all_sinc = _load_project_frames(project_id, audios)

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.header("Controles")
    analysis_mode = st.radio("Modo de analise", ["Rapida (1 chamada)", "Aprofundada (2 etapas)"])
    use_kb = st.checkbox("Usar Base de Conhecimento", value=True)
    # Um unico seletor para todos os provedores; as chaves ficam no .env.
    ai_provider_id, ai_model = ui.ai_model_selector("prj_ai_model", use_kb=use_kb)


def _run_ai(**kwargs) -> dict:
    """Chama o modelo escolhido no seletor (ver `generate_analysis`)."""
    return generate_analysis(ai_provider_id, ai_model, **kwargs)


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
m1.metric("Áudios", n_interviews)
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
    st.subheader("Métricas Acústicas Comparadas")
    
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        fig_comp = create_project_acoustic_comparison(
            all_sinc, title="Média de Indicadores por Áudio", tr_df=all_tr
        )
        st.plotly_chart(fig_comp, use_container_width=True)
    with col_c2:
        fig_emo = create_project_emotion_distribution(all_sinc, title="Distribuição de Emoções por Áudio (%)")
        st.plotly_chart(fig_emo, use_container_width=True)
        
if not all_tr.empty:
    st.divider()
    st.subheader("Ranking de Palavras Mais Frequentes (Projeto)")
    fig_words = create_project_word_ranking(all_tr, title="Palavras Mais Mencionadas nas Transcrições", top_n=15)
    st.plotly_chart(fig_words, use_container_width=True)

# ------------------------------------------------------------------
# Sentimento do texto × voz
# ------------------------------------------------------------------
fonte_sentimento = all_tr if tem_sentimento_texto(all_tr) else all_sinc
project_divergences = detectar_divergencias(all_sinc)
if tem_sentimento_texto(fonte_sentimento):
    st.divider()
    st.subheader("Sentimento do Texto × Voz")
    st.markdown(
        "Cada trecho transcrito recebe uma nota de -1 a +1 pelo sentimento do que foi dito "
        "(inferência automática de um modelo de linguagem, não verdade sobre o que o "
        "respondente sente). À esquerda, quanto do tempo de fala de cada áudio foi positivo, "
        "neutro ou negativo; à direita, o sentimento do texto ao lado da valência da voz."
    )
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.plotly_chart(
            create_project_text_sentiment_distribution(
                fonte_sentimento, title="Sentimento do Texto por Áudio (% do tempo de fala)"
            ),
            use_container_width=True,
        )
    with col_s2:
        st.plotly_chart(
            create_project_acoustic_comparison(
                all_sinc,
                title="Texto × Voz: Sentimento e Valência por Áudio",
                tr_df=fonte_sentimento,
                metrics=["dim_valence"],
            ),
            use_container_width=True,
        )

    st.write("")
    st.subheader("Momentos de Divergência Voz × Texto")
    st.markdown(
        "Trechos em que texto e voz apontam em sentidos opostos: texto claramente positivo com "
        "a valência vocal bem abaixo do habitual do locutor, ou o contrário. São candidatos a "
        "leitura qualitativa (ironia, cortesia protocolar, insatisfação normalizada), não prova. "
        "Selecione uma linha e clique no botão para navegar até a timeline do áudio."
    )
    if project_divergences.empty:
        st.caption("Nenhuma divergência acima dos limiares nos áudios do projeto.")
    else:
        df_div = pd.DataFrame({
            "Áudio": project_divergences["session_id"],
            "Tempo": [
                formatar_tempo(inicio) or ts
                for inicio, ts in zip(project_divergences["start_s"], project_divergences["Timestamp"])
            ],
            "Locutor": project_divergences["SpeakerName"].replace("", "Desconhecido"),
            "Fala (Transcrição)": project_divergences["Text"],
            "Texto (nota)": project_divergences["sentimento_texto"].map(lambda v: f"{v:+.2f}"),
            "Justificativa": project_divergences["sentimento_justificativa"],
            "Voz (valência)": [
                f"{v:+.2f} (z {z:+.1f})"
                for v, z in zip(project_divergences["valencia_voz"], project_divergences["z_valencia"])
            ],
            "Leitura": project_divergences["tipo"],
        })
        div_event = st.dataframe(
            df_div,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="prj_div_select",
        )

        div_rows = []
        if div_event:
            selection = getattr(div_event, "selection", None)
            if isinstance(selection, dict):
                div_rows = selection.get("rows", [])
            elif selection is not None:
                div_rows = getattr(selection, "rows", []) or []

        if st.button("Ir para divergência na Timeline do Áudio"):
            if not div_rows:
                st.info("Selecione uma divergência na tabela acima para localizar a timeline correspondente.")
            else:
                idx = int(div_rows[0])
                if idx < 0 or idx >= len(project_divergences):
                    st.warning("Não foi possível identificar a divergência selecionada.")
                else:
                    div_row = project_divergences.iloc[idx]
                    target_sess = div_row.get("session_id")
                    target_audio = next((a for a in audios if a.get("session_id") == target_sess), None)
                    if target_audio:
                        inicio = div_row.get("start_s")
                        st.session_state["pros_audio_id"] = target_audio["id"]
                        st.session_state["pros_timeline_focus"] = {
                            "audio_id": target_audio["id"],
                            "session_id": target_sess,
                            "question": "Divergência voz × texto",
                            "seconds": float(inicio) if pd.notna(inicio) else None,
                            "timestamp": str(div_row.get("Timestamp", "")),
                            "speaker": str(div_row.get("SpeakerName", "")),
                            "text": str(div_row.get("Text", "")),
                            "source": div_row.get("tipo", "Divergência voz × texto"),
                        }
                        # Cruza para o nivel do audio; ver `app.py`.
                        st.session_state["_navigate_to"] = (
                            "modules/prosodia/audio_timeline.py"
                        )
                        st.rerun()
                    else:
                        st.error("Não foi possível localizar o ID deste áudio.")

if not all_sinc.empty:
    st.divider()
    st.subheader("Momentos de Maior Ativação Prosódica (Projeto)")
    st.markdown(
        "Esta seção exibe os momentos dos áudios com a maior combinação de ativação emocional (Arousal) "
        "e variações de voz (Pitch e Volume). Selecione uma linha e clique no botão para navegar até a timeline detalhada."
    )
    
    top_moments = _extract_high_activation_moments(all_sinc, top_n=15)
    if not top_moments.empty:
        df_show = pd.DataFrame()
        txt_series = top_moments["Text"].fillna("") if "Text" in top_moments.columns else pd.Series([""] * len(top_moments))
        df_show["Tópico"] = [extract_topic_from_text(t) for t in txt_series]
        df_show["Áudio"] = top_moments["session_id"]
        
        spk_series = top_moments["SpeakerName"].fillna("Desconhecido") if "SpeakerName" in top_moments.columns else pd.Series(["Desconhecido"] * len(top_moments))
        df_show["Locutor"] = spk_series.astype(str).str.strip().replace("nan", "Desconhecido")
        
        df_show["Tempo"] = [_tempo_do_momento(row) for _, row in top_moments.iterrows()]
        
        df_show["Fala (Transcrição)"] = txt_series.astype(str).str.strip().replace("nan", "")
        
        if "dim_arousal" in top_moments.columns:
            df_show["Arousal"] = top_moments["dim_arousal"].fillna(0.0).map(lambda v: f"{v:.2f}")
        else:
            df_show["Arousal"] = "0.00"
        
        top_table_event = st.dataframe(
            df_show,
            width='stretch',
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"prj_top_moments_select",
        )
        
        selected_rows = []
        if top_table_event:
            selection = getattr(top_table_event, "selection", None)
            if isinstance(selection, dict):
                selected_rows = selection.get("rows", [])
            elif selection is not None:
                selected_rows = getattr(selection, "rows", []) or []
                
        if st.button("Ir para momento na Timeline do Áudio"):
            if not selected_rows:
                st.info("Selecione um momento na tabela acima para localizar a timeline correspondente.")
            else:
                idx = int(selected_rows[0])
                if idx < 0 or idx >= len(top_moments):
                    st.warning("Não foi possível identificar o momento selecionado.")
                else:
                    moment_row = top_moments.iloc[idx]
                    
                    target_sess = moment_row.get("session_id")
                    target_audio = next((a for a in audios if a.get("session_id") == target_sess), None)
                
                    if target_audio:
                        st.session_state["pros_audio_id"] = target_audio["id"]
                        st.session_state["pros_timeline_focus"] = {
                            "audio_id": target_audio["id"],
                            "session_id": target_sess,
                            "question": "Momento de Alta Ativação Geral",
                            "seconds": float(moment_row.get("seconds", moment_row.get("start_s", 0.0))),
                            "timestamp": _tempo_do_momento(moment_row),
                            "speaker": str(moment_row.get("SpeakerName", "")),
                            "text": str(moment_row.get("Text", "")),
                            "source": "Filtro de Ativação Consolidado",
                        }
                        # Cruza para o nivel do audio; ver `app.py`.
                        st.session_state["_navigate_to"] = (
                            "modules/prosodia/audio_timeline.py"
                        )
                        st.rerun()
                    else:
                        st.error("Não foi possível localizar o ID deste áudio.")
                    
        # Tabela de Tópicos Consolidados/Agrupados
        moments_list = []
        for _, row in top_moments.iterrows():
            moments_list.append({
                "session_id": str(row.get("session_id", "")),
                "SpeakerName": _limpo(row.get("SpeakerName")) or "Desconhecido",
                "Timestamp": _tempo_do_momento(row),
                "Text": _limpo(row.get("Text", "")),
                "dim_arousal": float(row.get("dim_arousal", 0.0)) if pd.notna(row.get("dim_arousal")) else 0.0,
                "topic": extract_topic_from_text(_limpo(row.get("Text", ""))),
            })
            
        grouped_topics = _group_similar_topics(moments_list)
        if grouped_topics:
            st.write("")
            st.subheader("Tópicos Consolidados de Maior Ativação")
            st.markdown(
                "Agrupamento temático dos momentos de maior expressividade e arousal do projeto. "
                "Agrupa tópicos equivalentes que compartilham termos centrais."
            )
            
            df_grouped = pd.DataFrame(grouped_topics)
            df_grouped.columns = ["Tópico Consolidado", "Ocorrências", "Arousal Médio", "Áudios Relacionados", "Exemplo de Destaque"]
            df_grouped["Arousal Médio"] = df_grouped["Arousal Médio"].map(lambda v: f"{v:.2f}")
            
            st.dataframe(
                df_grouped,
                width='stretch',
                hide_index=True,
            )
            
        # Seção: Perguntas com Maior Ativação Prosódica
        q_activations = _calculate_questions_activation(audios, all_sinc)
        if q_activations:
            st.write("")
            st.subheader("Perguntas com Maior Ativação Prosódica")
            st.markdown(
                "Análise de quais perguntas do roteiro geraram maior expressividade de voz e arousal emocional nas respostas dos respondentes."
            )
            
            df_q = pd.DataFrame(q_activations)
            df_q.columns = ["Pergunta", "Respostas Cobertas", "Arousal Médio", "Variação Pitch Médio", "Variação Volume Médio", "Destaque (Maior Arousal)"]
            df_q["Arousal Médio"] = df_q["Arousal Médio"].map(lambda v: f"{v:.2f}")
            df_q["Variação Pitch Médio"] = df_q["Variação Pitch Médio"].map(lambda v: f"{v:.2f}")
            df_q["Variação Volume Médio"] = df_q["Variação Volume Médio"].map(lambda v: f"{v:.2f}")
            
            st.dataframe(
                df_q,
                width='stretch',
                hide_index=True,
            )
    else:
        st.info("Não foi possível extrair momentos de alta ativação acústica no projeto.")

# ------------------------------------------------------------------
# Construir contexto para IA
# ------------------------------------------------------------------
proj_ctx = {
    "nome": project.get("name", ""),
    "especialidade": project.get("especialidade", ""),
    "historico": project.get("historico", ""),
    "problemas": project.get("problemas", ""),
    "briefing": project.get("briefing_text", ""),
}

quality_counts = {"pass": 0, "warn": 0, "fail": 0, "pending": 0}
for a in audios:
    status = str(a.get("quality_status", "pending"))
    quality_counts[status if status in quality_counts else "pending"] += 1

tables_lines = [
    f"Áudios totais: {n_interviews}",
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
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.download_button(
            "Download Analise Geral (.md)",
            data=analysis_md,
            file_name=f"analise_geral_{_slugify(project.get('name', 'projeto'))}.md",
            mime="text/markdown",
            key="download_latest_project_analysis",
            use_container_width=True,
        )
    with col_d2:
        pdf_data = _build_project_analysis_pdf(
            project_name=project.get("name", ""),
            project_info=proj_ctx,
            model=latest_analysis.get("model", ""),
            created_at=latest_analysis.get("created_at", ""),
            text=latest_analysis.get("analysis_text", ""),
            citations=latest_analysis.get("citations", []),
            acoustic_summary=_calculate_acoustic_summary_text(all_sinc) if not all_sinc.empty else "",
        )
        st.download_button(
            "Download Analise Geral (.pdf)",
            data=pdf_data,
            file_name=f"analise_geral_{_slugify(project.get('name', 'projeto'))}.pdf",
            mime="application/pdf",
            key="download_latest_project_analysis_pdf",
            use_container_width=True,
        )

    with st.expander("Referencias da Base de Conhecimento"):
        ui.knowledge_base_references(
            {
                "citations": latest_analysis.get("citations", []),
                # A tela recarrega depois de salvar e o banco guarda so as
                # citacoes: o que a busca fez fica na sessao desta rodada.
                "search": st.session_state.get(
                    f"pr_kb_search_project_{project_id}", {}
                ),
            }
        )

    chat_key = f"prj_chat_history_{project_id}"
    confirm_key = f"prj_an_confirm_{project_id}"

    history = get_project_analyses(project_id)
    with st.expander(f"Historico de analises gerais ({len(history)} registros)"):
        if pode_editar and len(history) > 1:
            if st.button("Apagar análises anteriores", key=f"prj_an_del_old_{project_id}"):
                st.session_state[confirm_key] = {
                    "ids": [an["id"] for an in history[1:]],
                    "label": f"as {len(history) - 1} análises anteriores (a mais recente será mantida)",
                }
                st.rerun()
        for an in history:
            col_h, col_del = st.columns([5, 1])
            with col_h:
                st.markdown(f"**{an['created_at']} - {an.get('model', '-')}**")
            with col_del:
                if pode_editar and st.button("Excluir", key=f"prj_an_del_{an['id']}", width="stretch"):
                    st.session_state[confirm_key] = {
                        "ids": [an["id"]],
                        "label": f"a análise de **{an['created_at']}**",
                    }
                    st.rerun()
            text = an.get("analysis_text", "")
            st.markdown(text[:500] + ("..." if len(text) > 500 else ""))
            st.divider()

    pending = st.session_state.get(confirm_key)
    if pending and pode_editar:
        st.warning(f"Excluir {pending['label']}? Esta ação não pode ser desfeita.")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("Confirmar exclusão", key=f"prj_an_del_yes_{project_id}", width="stretch"):
                delete_project_analyses(project_id, pending["ids"])
                # O chat conversa sobre a analise mais recente: se ela saiu,
                # a conversa perdeu o contexto.
                if latest_analysis["id"] in pending["ids"]:
                    st.session_state.pop(chat_key, None)
                st.session_state.pop(confirm_key, None)
                st.rerun()
        with cc2:
            if st.button("Cancelar", key=f"prj_an_del_no_{project_id}", width="stretch"):
                st.session_state.pop(confirm_key, None)
                st.rerun()

    st.divider()
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    col_t, col_clear = st.columns([5, 1], vertical_alignment="bottom")
    with col_t:
        st.subheader("Chat com a IA sobre o Projeto")
    with col_clear:
        if st.session_state[chat_key] and st.button(
            "Limpar conversa", key=f"prj_chat_clear_{project_id}", width="stretch"
        ):
            st.session_state[chat_key] = []
            st.rerun()
    st.markdown(
        "Tire dúvidas ou peça detalhamentos específicos sobre o relatório geral gerado acima."
    )

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            
    if prompt := st.chat_input("Pergunte algo sobre a análise geral...", key=f"prj_chat_input_{project_id}"):
        with st.chat_message("user"):
            st.write(prompt)
        st.session_state[chat_key].append({"role": "user", "content": prompt})
        
        if not ai_provider_id:
            st.error("Configure uma chave de API no .env para habilitar o chat.")
        else:
            with st.chat_message("assistant"):
                with st.spinner("Pensando..."):
                    try:
                        report_context = latest_analysis.get("analysis_text", "")
                        sys_msg = (
                            "Você é um consultor analítico especialista em prosódia e comportamento humano. "
                            "O usuário deseja fazer perguntas sobre a Análise Geral do Projeto consolidada abaixo. "
                            "Responda de forma concisa, objetiva e baseada nas informações do relatório.\n\n"
                            f"--- RELATÓRIO DO PROJETO ---\n{report_context}\n-----------------------------"
                        )

                        if ai_provider_id == PROVIDER_OPENAI:
                            chat_user_prompt = ""
                            for h in st.session_state[chat_key][:-1]:
                                role_name = "Usuário" if h["role"] == "user" else "Assistente"
                                chat_user_prompt += f"{role_name}: {h['content']}\n\n"
                            chat_user_prompt += f"Usuário: {prompt}"
                            
                            chat_vs_id = get_prosodia_vector_store_id() if use_kb else None
                            
                            result = ai_create_analysis(
                                system_prompt=sys_msg,
                                user_prompt=chat_user_prompt,
                                model=ai_model,
                                vector_store_id=chat_vs_id,
                                kb_filter=build_kb_filter(project_id),
                                temperature=0.7,
                                max_tokens=1500,
                            )
                            answer = result.get("text", "")
                            
                            citations = result.get("citations", [])
                            if citations:
                                answer += "\n\n**Referências da Base de Conhecimento:**"
                                for cit in citations:
                                    filename = cit.get("filename") or "Documento"
                                    quote = cit.get("quote")
                                    if quote:
                                        answer += f"\n- *{filename}*: \"{quote}\""
                                    else:
                                        answer += f"\n- *{filename}*"
                        else:
                            messages = [
                                {"role": h["role"], "content": h["content"]}
                                for h in st.session_state[chat_key]
                            ]
                            answer = chat_completion(
                                ai_provider_id,
                                ai_model,
                                sys_msg,
                                messages,
                                temperature=0.7,
                                max_tokens=1500,
                            )
                            
                        st.write(answer)
                        st.session_state[chat_key].append({"role": "assistant", "content": answer})
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao obter resposta da IA: {e}")
else:
    st.info("Nenhuma analise geral disponivel. Clique em Gerar Analise Geral.")

btn_label = "Regenerar Analise Geral" if latest_analysis else "Gerar Analise Geral"
if st.button(btn_label, type="primary"):
    if not ai_provider_id:
        st.error(
            "Configure uma chave de API no .env (OPENAI_API_KEY, GROQ_API_KEY "
            "ou ANTHROPIC_API_KEY) e reinicie o app."
        )
        st.stop()

    vs_id = get_prosodia_vector_store_id() if use_kb else None

    with st.spinner("Gerando analise geral..."):
        try:
            result = {"text": "", "citations": []}
            
            # Calcular os inputs da análise consolidada de projeto
            acoustic_stats_text = _calculate_acoustic_summary_text(all_sinc)
            
            # Adicionar perguntas com maior ativação prosódica
            q_activations = _calculate_questions_activation(audios, all_sinc)
            if q_activations:
                q_lines = [
                    "\n### Perguntas com Maior Ativação Prosódica (Acumulado dos Áudios):",
                    "| Pergunta | Respostas Cobertas | Arousal Médio | Pitch Var Média | Volume Var Média | Resposta Destaque |",
                    "|---|---|---|---|---|---|",
                ]
                for qa in q_activations:
                    q_lines.append(
                        f"| {qa['question']} | {qa['count']} | {qa['avg_arousal']:.2f} | {qa['avg_f0_var']:.2f} | {qa['avg_ld_var']:.2f} | {qa['example']} |"
                    )
                acoustic_stats_text += "\n" + "\n".join(q_lines)

            # As quatro categorias de emoção existem no CSV desde sempre e nunca
            # chegavam ao prompt, que por sua vez as pedia.
            acoustic_stats_text += (
                "\n\n### Distribuição de Emoções por Áudio\n\n"
                + emotion_distribution_text(all_sinc, "session_id", "Áudio")
            )

            top_words_text = _calculate_top_words_text(all_tr, top_n=30)
            
            top_moments = _extract_high_activation_moments(all_sinc, top_n=15)
            high_activation_text = _format_high_activation_text(top_moments)
            
            individual_analyses_text = _load_individual_analyses(audios)

            sentimento_texto = texto_sentimento_resumo(fonte_sentimento, "session_id", "Áudio")
            divergencias = (
                divergencias_texto(project_divergences, incluir_audio=True)
                if tem_sentimento_texto(all_sinc)
                else ""
            )
            secao_sentimento = secoes_sentimento(sentimento_texto, divergencias)

            user_prompt = build_project_user_prompt(
                project_context=proj_ctx,
                acoustic_stats_text=acoustic_stats_text,
                top_words_text=top_words_text,
                high_activation_text=high_activation_text,
                individual_analyses_text=individual_analyses_text,
                sentimento_texto=sentimento_texto,
                divergencias=divergencias,
            )

            tipo_projeto = project.get("tipo_projeto")
            prj_sys_prompt = get_prosodia_project_system_prompt(tipo_projeto)
            if analysis_mode == "Rapida (1 chamada)":
                result = _run_ai(
                    system_prompt=prj_sys_prompt,
                    user_prompt=user_prompt,
                    temperature=0.5,
                    max_tokens=3500,
                    vector_store_id=vs_id,
                    kb_filter=build_kb_filter(project_id),
                )
            else:
                stat_result = _run_ai(
                    system_prompt=get_prosodia_project_system_prompt(tipo_projeto, "estatistica"),
                    user_prompt=user_prompt,
                    temperature=0.3,
                    max_tokens=2200,
                )
                strat_user = (
                    f"Analise estatistica previa:\n{stat_result['text']}\n\n"
                    f"Dados consolidados do projeto:\n{acoustic_stats_text}\n\n"
                    f"Ranking de palavras:\n{top_words_text}\n\n"
                    + secao_sentimento
                )
                strat_result = _run_ai(
                    system_prompt=get_prosodia_project_system_prompt(tipo_projeto, "estrategica"),
                    user_prompt=strat_user,
                    temperature=0.5,
                    max_tokens=2200,
                    vector_store_id=vs_id,
                    kb_filter=build_kb_filter(project_id),
                )
                result = {
                    "text": (
                        "## Analise Estatistica\n\n"
                        + stat_result["text"]
                        + "\n\n---\n\n## Analise Estrategica\n\n"
                        + strat_result["text"]
                    ),
                    "citations": strat_result.get("citations", []),
                    "search": strat_result.get("search", {}),
                }

            used_model = ai_model
            save_project_analysis(project_id, used_model, result.get("text", ""), result.get("citations", []))
            st.session_state[f"pr_kb_search_project_{project_id}"] = result.get(
                "search", {}
            )

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
            # Devolver a analise para a base so faz sentido para quem esta
            # usando a base: antes isto rodava ate com ela desligada.
            if use_kb:
                kb_ok, kb_msg = _append_result_to_kb(
                    kb_doc_name, kb_doc, project_id
                )
            else:
                kb_ok, kb_msg = False, "base de conhecimento desligada nesta analise"

            st.success("Analise geral salva!")
            if kb_ok:
                st.caption(f"Base de conhecimento atualizada com: {kb_msg}")
            else:
                st.warning(f"Analise geral salva, mas nao foi possivel enviar para a base: {kb_msg}")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao gerar analise geral: {e}")
