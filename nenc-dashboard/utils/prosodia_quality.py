"""
Prosodia Quality — Verificações automáticas de qualidade de entrevistas.

Checks objetivos (sem IA):
  - Duração total de fala
  - Contagem de palavras
  - Inteligibilidade (marcadores [inaudible]/[?])
  - Ratio de silêncio
  - Dominância de speaker
  - Nº de segmentos VAD
  - Taxa de fala (WPM)
  - Anomalias acústicas (f0, loudness, emoção) — apenas se sincronizado disponível

Cobertura de perguntas:
  - check_question_coverage_keywords: busca por tokens no texto (sempre disponível)
  - check_question_coverage_ai: análise semântica via IA (requer client OpenAI/Groq)
"""

import re
import json
from typing import Dict, List, Optional, Tuple
import pandas as pd


def _parse_json_response(raw: str) -> list:
    """
    Tenta extrair uma lista JSON de uma string que pode conter:
    - Bloco de código markdown (```json ... ```)
    - Texto antes/depois do JSON
    - Trailing commas, comentários //, JSON truncado
    """
    text = raw.strip()

    # 1. Remover bloco markdown ```json ... ``` ou ``` ... ```
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    # Limpeza comum: trailing commas e comentários de linha
    def _clean(s: str) -> str:
        s = re.sub(r"//[^\n]*", "", s)          # remove // comments
        s = re.sub(r",\s*([}\]])", r"\1", s)    # remove trailing commas
        return s

    # 2. Tentar parse direto
    try:
        result = json.loads(_clean(text))
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 3. Extrair o primeiro array [...] encontrado no texto
    match = re.search(r"\[", text)
    if match:
        start = match.start()
        candidate = text[start:]

        # 3a. Tentar com o texto completo desde '[
        try:
            result = json.loads(_clean(candidate))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # 3b. JSON truncado: coletar objetos completos manualmente
        objects = []
        depth = 0
        obj_start = None
        i = 0
        in_str = False
        escape = False
        for i, ch in enumerate(candidate):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not escape:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                if depth == 0 and obj_start is None:
                    obj_start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        obj = json.loads(_clean(candidate[obj_start:i+1]))
                        objects.append(obj)
                    except json.JSONDecodeError:
                        pass
                    obj_start = None

        if objects:
            return objects

    # 4. Nenhuma estratégia funcionou
    raise ValueError(f"Não foi possível extrair JSON válido da resposta. Início: {raw[:300]!r}")

# ---------------------------------------------------------------------------
# Limiares (editáveis)
# ---------------------------------------------------------------------------

THRESHOLDS = {
    "duration_fail_s": 60,
    "duration_warn_s": 120,
    "words_fail": 50,
    "words_warn": 100,
    "unintelligible_fail_pct": 0.30,
    "unintelligible_warn_pct": 0.20,
    "silence_ratio_warn": 0.75,
    "speaker_dominance_warn_pct": 0.90,
    "min_vad_segments_warn": 5,
    "wpm_low_warn": 80,
    "wpm_high_warn": 250,
    "f0_zero_ratio_warn": 0.80,
    "emotion_neutral_warn": 0.95,
    "loudness_low_warn": -40.0,  # dB típico; ajustar conforme pipeline
}

_UNINTELLIGIBLE_RE = re.compile(
    r"\[\s*(?:inaudível|inaudible|inaud\.?|inaudi|\?+|xxx+|unclear)\s*\]",
    flags=re.IGNORECASE,
)

STATUS_PRIORITY = {"fail": 2, "warn": 1, "pass": 0}


def _check(id: str, label: str, status: str, detail: str) -> Dict:
    return {"id": id, "label": label, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# Checks objetivos
# ---------------------------------------------------------------------------

def check_duration(vad_df: pd.DataFrame) -> Dict:
    """Verifica se a duração total de fala é suficiente."""
    if vad_df.empty or "duration" not in vad_df.columns:
        return _check("duration", "Duração de fala", "warn", "Dados VAD não disponíveis.")

    total = vad_df["duration"].sum()
    if total < THRESHOLDS["duration_fail_s"]:
        return _check(
            "duration", "Duração de fala", "fail",
            f"Total de fala: {total:.1f}s (mínimo esperado: {THRESHOLDS['duration_fail_s']}s)."
        )
    if total < THRESHOLDS["duration_warn_s"]:
        return _check(
            "duration", "Duração de fala", "warn",
            f"Total de fala: {total:.1f}s (recomendado: ≥ {THRESHOLDS['duration_warn_s']}s)."
        )
    return _check("duration", "Duração de fala", "pass", f"Total de fala: {total:.1f}s.")


def check_word_count(transcricao_df: pd.DataFrame) -> Dict:
    """Verifica se há palavras suficientes na transcrição."""
    if transcricao_df.empty or "word_count" not in transcricao_df.columns:
        return _check("word_count", "Contagem de palavras", "warn", "Dados de transcrição não disponíveis.")

    total = int(transcricao_df["word_count"].sum())
    if total < THRESHOLDS["words_fail"]:
        return _check(
            "word_count", "Contagem de palavras", "fail",
            f"Total de palavras: {total} (mínimo esperado: {THRESHOLDS['words_fail']})."
        )
    if total < THRESHOLDS["words_warn"]:
        return _check(
            "word_count", "Contagem de palavras", "warn",
            f"Total de palavras: {total} (recomendado: ≥ {THRESHOLDS['words_warn']})."
        )
    return _check("word_count", "Contagem de palavras", "pass", f"Total de palavras: {total}.")


def check_intelligibility(transcricao_df: pd.DataFrame) -> Dict:
    """Verifica a proporção de marcadores de texto ininteligível."""
    if transcricao_df.empty or "Text" not in transcricao_df.columns:
        return _check("intelligibility", "Inteligibilidade", "warn", "Dados de transcrição não disponíveis.")

    texts = transcricao_df["Text"].fillna("").astype(str)
    total_msgs = max(len(texts), 1)
    unintelligible = texts.apply(lambda t: bool(_UNINTELLIGIBLE_RE.search(t))).sum()
    ratio = unintelligible / total_msgs

    if ratio >= THRESHOLDS["unintelligible_fail_pct"]:
        return _check(
            "intelligibility", "Inteligibilidade", "fail",
            f"{unintelligible} de {total_msgs} turnos com texto ininteligível ({ratio:.0%})."
        )
    if ratio >= THRESHOLDS["unintelligible_warn_pct"]:
        return _check(
            "intelligibility", "Inteligibilidade", "warn",
            f"{unintelligible} de {total_msgs} turnos com texto ininteligível ({ratio:.0%})."
        )
    return _check(
        "intelligibility", "Inteligibilidade", "pass",
        f"Apenas {unintelligible} turno(s) com marcadores de ininteligibilidade ({ratio:.0%})."
    )


def check_silence_ratio(vad_df: pd.DataFrame) -> Dict:
    """Verifica se há silêncio excessivo em relação ao total da entrevista."""
    if vad_df.empty or "start" not in vad_df.columns:
        return _check("silence_ratio", "Ratio de silêncio", "warn", "Dados VAD não disponíveis.")

    total_duration = vad_df["end"].max() - vad_df["start"].min() if "end" in vad_df.columns else 0
    speech_duration = vad_df["duration"].sum() if "duration" in vad_df.columns else 0

    if total_duration <= 0:
        return _check("silence_ratio", "Ratio de silêncio", "warn", "Não foi possível calcular a duração total.")

    silence_ratio = 1.0 - (speech_duration / total_duration)
    if silence_ratio >= THRESHOLDS["silence_ratio_warn"]:
        return _check(
            "silence_ratio", "Ratio de silêncio", "warn",
            f"Silêncio: {silence_ratio:.0%} do total (duração total: {total_duration:.1f}s, "
            f"fala: {speech_duration:.1f}s)."
        )
    return _check(
        "silence_ratio", "Ratio de silêncio", "pass",
        f"Silêncio: {silence_ratio:.0%} do total — dentro do esperado."
    )


def check_speaker_balance(transcricao_df: pd.DataFrame) -> Dict:
    """Verifica se um único locutor domina a totalidade da conversa."""
    if transcricao_df.empty or "SpeakerName" not in transcricao_df.columns:
        return _check("speaker_balance", "Equilíbrio entre locutores", "warn", "Dados de transcrição não disponíveis.")

    if "word_count" not in transcricao_df.columns:
        transcricao_df = transcricao_df.copy()
        transcricao_df["word_count"] = (
            transcricao_df.get("Text", pd.Series(dtype=str)).fillna("").apply(lambda t: len(str(t).split()))
        )

    total_words = transcricao_df["word_count"].sum()
    if total_words == 0:
        return _check("speaker_balance", "Equilíbrio entre locutores", "warn", "Nenhuma palavra encontrada.")

    by_speaker = transcricao_df.groupby("SpeakerName")["word_count"].sum()
    max_ratio = by_speaker.max() / total_words
    dominant = by_speaker.idxmax()

    if max_ratio >= THRESHOLDS["speaker_dominance_warn_pct"]:
        return _check(
            "speaker_balance", "Equilíbrio entre locutores", "warn",
            f"'{dominant}' fez {max_ratio:.0%} das palavras — possível gravação mono ou entrevista desequilibrada."
        )
    return _check(
        "speaker_balance", "Equilíbrio entre locutores", "pass",
        f"Locutor mais ativo: '{dominant}' com {max_ratio:.0%} das palavras."
    )


def check_segment_count(vad_df: pd.DataFrame) -> Dict:
    """Verifica se há segmentos VAD suficientes."""
    if vad_df.empty:
        return _check("segment_count", "Nº de segmentos VAD", "warn", "Dados VAD não disponíveis.")

    n = len(vad_df)
    if n < THRESHOLDS["min_vad_segments_warn"]:
        return _check(
            "segment_count", "Nº de segmentos VAD", "warn",
            f"Apenas {n} segmento(s) VAD detectado(s) (esperado: ≥ {THRESHOLDS['min_vad_segments_warn']})."
        )
    return _check("segment_count", "Nº de segmentos VAD", "pass", f"{n} segmentos VAD detectados.")


def check_speaking_rate(transcricao_df: pd.DataFrame, vad_df: pd.DataFrame) -> Dict:
    """Verifica se a taxa de fala (WPM) está dentro de uma faixa normal."""
    if transcricao_df.empty or vad_df.empty:
        return _check("speaking_rate", "Taxa de fala (WPM)", "warn", "Dados insuficientes para calcular.")

    total_words = transcricao_df["word_count"].sum() if "word_count" in transcricao_df.columns else 0
    total_speech_s = vad_df["duration"].sum() if "duration" in vad_df.columns else 0

    if total_speech_s <= 0:
        return _check("speaking_rate", "Taxa de fala (WPM)", "warn", "Duração de fala zero — impossível calcular WPM.")

    wpm = (total_words / total_speech_s) * 60.0

    if wpm < THRESHOLDS["wpm_low_warn"] or wpm > THRESHOLDS["wpm_high_warn"]:
        return _check(
            "speaking_rate", "Taxa de fala (WPM)", "warn",
            f"Taxa de fala: {wpm:.0f} WPM — fora da faixa esperada "
            f"({THRESHOLDS['wpm_low_warn']}–{THRESHOLDS['wpm_high_warn']} WPM)."
        )
    return _check("speaking_rate", "Taxa de fala (WPM)", "pass", f"Taxa de fala: {wpm:.0f} WPM.")


def check_acoustic_anomalies(sinc_df: pd.DataFrame) -> List[Dict]:
    """
    Verifica anomalias nas features acústicas (apenas se dados sincronizados
    com colunas acústicas estiverem disponíveis).
    Retorna lista de checks (pode ser vazia se dados não disponíveis).
    """
    results = []

    # f0 anomalias
    if "f0_media" in sinc_df.columns:
        total = max(len(sinc_df), 1)
        zero_ratio = (sinc_df["f0_media"].fillna(0) == 0).sum() / total
        if zero_ratio >= THRESHOLDS["f0_zero_ratio_warn"]:
            results.append(_check(
                "f0_anomaly", "Anomalia F0 (pitch)",  "warn",
                f"{zero_ratio:.0%} dos segmentos com F0 = 0 — possível problema de microfone ou silêncio dominante."
            ))
        else:
            results.append(_check(
                "f0_anomaly", "Anomalia F0 (pitch)", "pass",
                f"F0 presente em {1-zero_ratio:.0%} dos segmentos."
            ))

    # Emoção neutral dominante
    if "emocao_neutral" in sinc_df.columns:
        mean_neutral = sinc_df["emocao_neutral"].mean()
        if mean_neutral >= THRESHOLDS["emotion_neutral_warn"]:
            results.append(_check(
                "emotion_variety", "Variedade emocional", "warn",
                f"Emoção 'neutral' média: {mean_neutral:.0%} — baixa variação prosódica detectada."
            ))
        else:
            results.append(_check(
                "emotion_variety", "Variedade emocional", "pass",
                f"Emoção 'neutral' média: {mean_neutral:.0%} — variação prosódica adequada."
            ))

    # Loudness baixa
    if "loudness_media" in sinc_df.columns:
        mean_loudness = sinc_df["loudness_media"].mean()
        if mean_loudness < THRESHOLDS["loudness_low_warn"]:
            results.append(_check(
                "loudness", "Nível de volume (loudness)", "warn",
                f"Loudness média: {mean_loudness:.1f} dB — volume muito baixo, possível problema de gravação."
            ))
        else:
            results.append(_check(
                "loudness", "Nível de volume (loudness)", "pass",
                f"Loudness média: {mean_loudness:.1f} dB — dentro do esperado."
            ))

    return results


# ---------------------------------------------------------------------------
# Cobertura de perguntas — Keywords
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set:
    """Tokeniza texto em palavras em minúsculas sem pontuação (≥ 3 chars)."""
    return {
        w.lower()
        for w in re.sub(r"[^\w\s]", " ", text).split()
        if len(w) >= 3
    }


def check_question_coverage_keywords(
    transcricao_df: pd.DataFrame,
    questions: List[str],
) -> List[Dict]:
    """
    Para cada pergunta, verifica se os termos principais aparecem na transcrição.
    Não requer API.
    """
    if not questions:
        return []

    full_text = " ".join(
        transcricao_df["Text"].fillna("").astype(str).tolist()
    ) if not transcricao_df.empty and "Text" in transcricao_df.columns else ""
    transcript_tokens = _tokenize(full_text)

    results = []
    for q in questions:
        q_tokens = _tokenize(q)
        if not q_tokens:
            results.append({
                "question": q,
                "covered_keywords": False,
                "covered_ai": None,
                "confidence": 0.0,
                "evidence": "Pergunta vazia — não verificada.",
            })
            continue

        matched = q_tokens & transcript_tokens
        ratio = len(matched) / len(q_tokens)

        results.append({
            "question": q,
            "covered_keywords": ratio >= 0.5,
            "covered_ai": None,
            "confidence": round(ratio, 2),
            "evidence": (
                f"Termos encontrados: {', '.join(sorted(matched))} "
                f"({len(matched)}/{len(q_tokens)} tokens da pergunta)."
                if matched else "Nenhum termo da pergunta encontrado na transcrição."
            ),
        })
    return results


# ---------------------------------------------------------------------------
# Cobertura de perguntas — IA
# ---------------------------------------------------------------------------

COVERAGE_SYSTEM_PROMPT = (
    "Você é um especialista em análise de entrevistas qualitativas. "
    "Dado o texto de uma entrevista e uma lista de perguntas de pesquisa, "
    "determine se cada pergunta foi abordada durante a conversa. "
    "Responda APENAS com um array JSON válido, sem texto adicional, "
    "sem blocos de código markdown, sem comentários. "
    "Formato exato (uma entrada por pergunta, na mesma ordem): "
    '[{"question": "...", "covered": true, "confidence": 0.9, "evidence": "trecho relevante"}]'
)


def check_question_coverage_ai(
    transcript_text: str,
    questions: List[str],
    client,
    model: str = "gpt-4.1-mini",
) -> List[Dict]:
    """
    Usa IA para verificar semanticamente se cada pergunta foi abordada.
    Requer client OpenAI ou Groq.
    Retorna lista no mesmo formato de check_question_coverage_keywords,
    com campo covered_ai preenchido.
    """
    if not questions or not transcript_text.strip():
        return []

    questions_block = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    # Limitar transcrição para deixar espaço suficiente para a resposta JSON
    # Cada pergunta precisa de ~150 tokens na resposta; deixar margem folgada
    max_transcript_words = max(400, 2000 - len(questions) * 80)
    words = transcript_text.split()
    if len(words) > max_transcript_words:
        transcript_text = " ".join(words[:max_transcript_words]) + "\n[transcrição truncada]"

    user_msg = (
        f"Perguntas de pesquisa:\n{questions_block}\n\n"
        f"Transcrição da entrevista:\n{transcript_text}"
    )

    try:
        # Suporte a OpenAI (client.responses.create) e Groq (client.chat.completions.create)
        # Tokens necessários: ~150 por pergunta + margem de segurança
        needed_tokens = max(1500, len(questions) * 200 + 500)

        if hasattr(client, "responses"):
            resp = client.responses.create(
                model=model,
                instructions=COVERAGE_SYSTEM_PROMPT,
                input=user_msg,
                temperature=0.2,
                max_output_tokens=needed_tokens,
            )
            raw_json = resp.output_text
        else:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": COVERAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=needed_tokens,
            )
            raw_json = resp.choices[0].message.content

        ai_results = _parse_json_response(raw_json)

        # Mapear resultados da IA por índice de pergunta
        coverage = []
        for i, q in enumerate(questions):
            ai_item = ai_results[i] if i < len(ai_results) else {}
            coverage.append({
                "question": q,
                "covered_keywords": None,  # será mesclado pelo chamador
                "covered_ai": bool(ai_item.get("covered", False)),
                "confidence": float(ai_item.get("confidence", 0.0)),
                "evidence": str(ai_item.get("evidence", "")),
            })
        return coverage

    except Exception as e:
        # Se IA falhar, retornar lista com erro mas não quebrar o fluxo
        return [
            {
                "question": q,
                "covered_keywords": None,
                "covered_ai": None,
                "confidence": 0.0,
                "evidence": f"Erro na análise de IA: {e}",
            }
            for q in questions
        ]


def merge_coverage(
    kw_results: List[Dict],
    ai_results: List[Dict],
) -> List[Dict]:
    """Mescla resultados de keywords e IA na mesma lista."""
    ai_by_question = {r["question"]: r for r in ai_results}
    merged = []
    for kw in kw_results:
        ai = ai_by_question.get(kw["question"], {})
        merged.append({
            "question": kw["question"],
            "covered_keywords": kw.get("covered_keywords"),
            "covered_ai": ai.get("covered_ai"),
            "confidence": ai.get("confidence", kw.get("confidence", 0.0)),
            "evidence": ai.get("evidence") or kw.get("evidence", ""),
        })
    return merged


# ---------------------------------------------------------------------------
# Runner principal
# ---------------------------------------------------------------------------

def run_quality_checks(
    vad_df: pd.DataFrame,
    transcricao_df: pd.DataFrame,
    sinc_df: Optional[pd.DataFrame] = None,
) -> List[Dict]:
    """
    Executa todos os checks objetivos e retorna lista de resultados.
    """
    checks = [
        check_duration(vad_df),
        check_word_count(transcricao_df),
        check_intelligibility(transcricao_df),
        check_silence_ratio(vad_df),
        check_speaker_balance(transcricao_df),
        check_segment_count(vad_df),
        check_speaking_rate(transcricao_df, vad_df),
    ]

    if sinc_df is not None and not sinc_df.empty:
        checks.extend(check_acoustic_anomalies(sinc_df))

    return checks


def compute_overall_status(checks: List[Dict]) -> str:
    """
    Retorna o status geral com base na prioridade: fail > warn > pass.
    """
    if not checks:
        return "pass"
    priority = max(STATUS_PRIORITY.get(c["status"], 0) for c in checks)
    return {2: "fail", 1: "warn", 0: "pass"}.get(priority, "pass")


def status_badge(status: str) -> str:
    """Retorna emoji de badge para o status."""
    return {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "⏳")
