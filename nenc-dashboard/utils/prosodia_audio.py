"""
NencBoost — peças da página do áudio (`modules/prosodia/audio.py`, tela 7a).

A página junta o que eram a Timeline e a Análise individual. Aqui ficam:

- os auxiliares que vieram de `audio_analise.py` sem mudar a lógica
  (localizar a resposta a uma pergunta na transcrição, Markdown da análise e
  da qualidade);
- funções puras que preparam os dados do player, das faixas de sinais, da
  transcrição e da lista de momentos;
- os dois componentes HTML: o player, que fica preso ao topo da página, e as
  faixas com a transcrição. Cada um vive num iframe próprio
  (`components.html`); os dois conversam por um BroadcastChannel, já que
  têm a mesma origem do app. O player é o dono do relógio: toca o áudio (ou
  um relógio virtual, quando não há áudio da API), avisa a posição e recebe
  os pedidos de salto vindos das faixas e da transcrição.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from utils import ui
from utils.prosodia_signals import EMOCOES, formatar_tempo

# ---------------------------------------------------------------------------
# Cores (design 7a)
# ---------------------------------------------------------------------------

ACCENT = "#9184d9"
ACCENT_600 = "#796cbf"
LAVANDA = "#d2cefd"
ATIVACAO = "#b5abfc"
NEUTRO = "#595d6c"
NEGATIVO = "#c9708b"
TEXTO_NEGATIVO = "#d9a0b2"
SUCESSO = "#7bc0a8"
ALERTA = "#d9a55c"

# Emoção na voz, de baixo para cima na barra empilhada.
CORES_EMOCAO = (
    ("emocao_neutral", "neutro", NEUTRO),
    ("emocao_happy", "alegria", ACCENT),
    ("emocao_sad", "tristeza", LAVANDA),
    ("emocao_angry", "raiva", NEGATIVO),
)

# "Mais sinais": as faixas que a timeline antiga mostrava.
MAIS_SINAIS = (
    ("f0_media", "Pitch (F0)", "Hz"),
    ("loudness_media", "Volume", "intensidade"),
    ("speaking_rate", "Taxa de fala", "sílabas/s"),
    ("intonation_score", "Entonação", "variação"),
)

# ---------------------------------------------------------------------------
# Texto e localização de perguntas (de audio_analise.py, sem mudança)
# ---------------------------------------------------------------------------

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
    return {t for t in norm.split() if len(t) >= 3 and t not in _STOPWORDS_PT}


def _extract_keyword_tokens(evidence_keywords: str) -> set[str]:
    """Extrai tokens de evidência no formato 'Termos encontrados: ...'."""
    text = str(evidence_keywords or "")
    match = re.search(r"Termos\s+encontrados\s*:\s*(.*?)(?:\(|$)", text, flags=re.IGNORECASE)
    if not match:
        return _tokenize_pt(text)
    parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
    return _tokenize_pt(" ".join(parts))


def _timestamp_to_seconds(ts: str) -> float | None:
    """Converte timestamp HH:MM:SS(.ms) ou MM:SS(.ms) em segundos."""
    val = str(ts or "").strip()
    if not val:
        return None
    try:
        if re.match(r"^\d+(?:\.\d+)?$", val):
            return float(val)
        nums = [float(p) for p in val.split(":")]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
    except Exception:
        return None
    return None


def find_question_moment(
    transcricao_df: pd.DataFrame,
    question: str,
    evidence_ai: str,
    evidence_keywords: str,
) -> dict | None:
    """Localiza o melhor turno da transcrição para a pergunta.

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


def slugify(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text or ""))
    return safe.strip("_")[:80] or "audio"


def build_analysis_markdown(sid: str, project_name: str, model: str, created_at: str,
                            text: str, citations: list) -> str:
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
            lines.append(f"{i}. {cit.get('filename', 'Documento')}")
            if cit.get("quote", ""):
                lines.append(f"   - Trecho: {cit.get('quote')}")
    return "\n".join(lines)


def build_quality_markdown(sid: str, project_name: str, created_at: str, overall_status: str,
                           checks: list, coverage: list) -> str:
    n_pass = sum(1 for c in checks if c.get("status") == "pass")
    n_warn = sum(1 for c in checks if c.get("status") == "warn")
    n_fail = sum(1 for c in checks if c.get("status") == "fail")
    n_cov_total = len(coverage)
    n_kw_found = sum(1 for c in coverage if c.get("covered_keywords") is True)
    n_ai_found = sum(1 for c in coverage if c.get("covered_ai") is True)
    payload = {"overall_status": overall_status, "checks": checks, "coverage": coverage}
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


# ---------------------------------------------------------------------------
# Cabeçalho e cartões
# ---------------------------------------------------------------------------

def tempo_curto(segundos) -> str:
    """2:38 (ou 1:02:38 acima de uma hora)."""
    try:
        total = int(round(float(segundos)))
    except (TypeError, ValueError):
        return ""
    horas, resto = divmod(total, 3600)
    return "{}:{:02d}:{:02d}".format(horas, resto // 60, resto % 60) if horas else "{}:{:02d}".format(
        resto // 60, resto % 60)


def numero(valor: Optional[float], casas: int = 2) -> str:
    """+0,34 com sinal e vírgula; vazio quando não há valor."""
    if valor is None or pd.isna(valor):
        return ""
    return "{:+.{c}f}".format(float(valor), c=casas).replace(".", ",").replace("-", "−")


def navegacao(ids: Sequence[int], atual: int) -> dict:
    """Posição do áudio na lista da tabela de Áudios, para as setas ‹ ›."""
    lista = list(ids or [])
    if atual not in lista:
        return {"posicao": None, "total": len(lista), "anterior": None, "proximo": None}
    i = lista.index(atual)
    return {
        "posicao": i + 1,
        "total": len(lista),
        "anterior": lista[i - 1] if i > 0 else None,
        "proximo": lista[i + 1] if i + 1 < len(lista) else None,
    }


def resumo_curto(texto: str, frases: int = 3, limite: int = 420) -> str:
    """As primeiras frases do primeiro parágrafo de texto corrido da análise."""
    for bloco in re.split(r"\n\s*\n", str(texto or "")):
        linha = bloco.strip()
        if not linha or linha.startswith(("#", "|", "---", "```")):
            continue
        linha = re.sub(r"^[-*>\d.)\s]+", "", linha)
        linha = re.sub(r"[*_`]+", "", linha)
        linha = re.sub(r"\s+", " ", linha).strip()
        if len(linha) < 40:
            continue
        partes = re.split(r"(?<=[.!?])\s+", linha)
        resumo = " ".join(partes[:frases]).strip()
        return resumo if len(resumo) <= limite else resumo[:limite].rsplit(" ", 1)[0] + "…"
    return ""


def cobertura(coverage: Iterable[dict]) -> dict:
    """Cobertas pela IA (ou pelas palavras-chave, quando a IA não avaliou) e as que faltaram."""
    itens = list(coverage or [])
    cobertas, faltou = 0, []
    for item in itens:
        ia = item.get("covered_ai")
        coberta = ia if ia is not None else item.get("covered_keywords")
        if coberta:
            cobertas += 1
        else:
            faltou.append(str(item.get("question", "")).strip())
    return {"cobertas": cobertas, "total": len(itens), "faltou": [f for f in faltou if f]}


# ---------------------------------------------------------------------------
# Momentos deste áudio
# ---------------------------------------------------------------------------

def _indice_no_tempo(indice_trechos: pd.DataFrame, segundos: Optional[float]) -> Optional[float]:
    if indice_trechos is None or indice_trechos.empty or segundos is None:
        return None
    inicio = pd.to_numeric(indice_trechos["start_s"], errors="coerce")
    antes = indice_trechos[inicio <= float(segundos) + 1e-6]
    if antes.empty:
        return None
    return float(antes.loc[pd.to_numeric(antes["start_s"], errors="coerce").idxmax(), "indice"])


def _float(valor) -> Optional[float]:
    try:
        numero_ = float(valor)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(numero_) else numero_


def momentos(divergencias_df: pd.DataFrame, ativacoes: Optional[List[dict]],
             indice_trechos: Optional[pd.DataFrame] = None) -> List[dict]:
    """Divergências e momentos de maior ativação numa lista só, por tempo."""
    lista: List[dict] = []
    if divergencias_df is not None and not divergencias_df.empty:
        for _, linha in divergencias_df.iterrows():
            inicio = _float(linha.get("start_s"))
            lista.append({
                "tipo": "divergencia",
                "seconds": inicio,
                "tempo": tempo_curto(inicio) if inicio is not None else str(linha.get("Timestamp", "")),
                "timestamp": str(linha.get("Timestamp", "")),
                "speaker": str(linha.get("SpeakerName", "") or ""),
                "fala": str(linha.get("Text", "") or "").strip(),
                "numeros": "texto {} · voz z {}".format(
                    numero(_float(linha.get("sentimento_texto"))), numero(_float(linha.get("z_valencia")), 1)),
                "leitura": str(linha.get("tipo", "") or ""),
            })
    for momento in ativacoes or []:
        inicio = _float(momento.get("seconds"))
        if inicio is None:
            inicio = _float(momento.get("start_s"))
        indice = _indice_no_tempo(indice_trechos, inicio)
        numeros = "ativação {:.2f}".format(float(momento.get("dim_arousal") or 0.0)).replace(".", ",")
        if indice is not None:
            numeros += " · índice {}".format(numero(indice))
        lista.append({
            "tipo": "ativacao",
            "seconds": inicio,
            "tempo": tempo_curto(inicio) if inicio is not None else str(momento.get("Timestamp", "")),
            "timestamp": str(momento.get("Timestamp", "")),
            "speaker": str(momento.get("SpeakerName", "") or ""),
            "fala": str(momento.get("Text", "") or "").strip(),
            "numeros": numeros,
            "leitura": str(momento.get("topic") or ""),
        })
    return sorted(lista, key=lambda m: (m["seconds"] is None, m["seconds"] or 0.0))


# ---------------------------------------------------------------------------
# Dados do player, das faixas e da transcrição
# ---------------------------------------------------------------------------

def _coluna(df: pd.DataFrame, nome: str) -> pd.Series:
    return pd.to_numeric(df[nome], errors="coerce") if nome in df.columns else pd.Series(dtype=float)


def segmentos_fala(sinc_df: pd.DataFrame, vad_df: pd.DataFrame) -> List[List[float]]:
    """Trechos de fala do VAD, para a trilha do player."""
    if sinc_df is not None and {"start_s", "end_s"}.issubset(sinc_df.columns):
        inicio, fim = _coluna(sinc_df, "start_s"), _coluna(sinc_df, "end_s")
    elif vad_df is not None and {"start", "end"}.issubset(vad_df.columns):
        inicio, fim = _coluna(vad_df, "start"), _coluna(vad_df, "end")
    else:
        return []
    pares = sorted((float(a), float(b)) for a, b in zip(inicio, fim) if pd.notna(a) and pd.notna(b) and b > a)
    return [[round(a, 2), round(b, 2)] for a, b in pares]


def faixas_divergencia(sinc_df: pd.DataFrame, divergencias_df: pd.DataFrame) -> List[List[float]]:
    """Início e fim de cada segmento do Whisper com divergência voz × texto."""
    if divergencias_df is None or divergencias_df.empty or sinc_df is None or "segmento_idx" not in sinc_df.columns:
        return []
    segmento = _coluna(sinc_df, "segmento_idx")
    faixas = []
    for indice in pd.to_numeric(divergencias_df["segmento_idx"], errors="coerce").dropna().unique():
        bloco = sinc_df[segmento == indice]
        inicio, fim = _coluna(bloco, "start_s").min(), _coluna(bloco, "end_s").max()
        if pd.notna(inicio) and pd.notna(fim) and fim > inicio:
            faixas.append([round(float(inicio), 2), round(float(fim), 2)])
    return sorted(faixas)


def serie_indice(indice_trechos: pd.DataFrame, sinc_df: pd.DataFrame) -> List[List[float]]:
    """[início, fim, índice] de cada trecho com as duas leituras."""
    if indice_trechos is None or indice_trechos.empty:
        return []
    fins = {}
    if sinc_df is not None and {"segmento_idx", "end_s"}.issubset(sinc_df.columns):
        fins = _coluna(sinc_df, "end_s").groupby(_coluna(sinc_df, "segmento_idx")).max().to_dict()
    serie = []
    for _, linha in indice_trechos.iterrows():
        inicio = _float(linha.get("start_s"))
        if inicio is None:
            continue
        fim = _float(fins.get(linha.get("segmento_idx"))) or inicio + 1.0
        serie.append([round(inicio, 2), round(max(fim, inicio + 0.2), 2), round(float(linha["indice"]), 3)])
    return sorted(serie)


def janelas_emocao(sinc_df: pd.DataFrame) -> List[List[float]]:
    """[início, fim, neutro, alegria, tristeza, raiva] por janela do VAD, em fatias que somam 1."""
    if sinc_df is None or sinc_df.empty or not {"start_s", "end_s"}.issubset(sinc_df.columns):
        return []
    colunas = [col for col, _, _ in CORES_EMOCAO]
    if not any(col in sinc_df.columns for col in colunas):
        return []
    valores = pd.DataFrame({col: _coluna(sinc_df, col) if col in sinc_df.columns else 0.0 for col in colunas})
    valores = valores.clip(lower=0).fillna(0.0)
    soma = valores.sum(axis=1)
    janelas = []
    for i, (inicio, fim) in enumerate(zip(_coluna(sinc_df, "start_s"), _coluna(sinc_df, "end_s"))):
        if pd.isna(inicio) or pd.isna(fim) or fim <= inicio or soma.iloc[i] <= 0:
            continue
        fatias = (valores.iloc[i] / soma.iloc[i]).round(3).tolist()
        janelas.append([round(float(inicio), 2), round(float(fim), 2)] + fatias)
    return sorted(janelas)


def serie_linha(sinc_df: pd.DataFrame, coluna: str) -> List[List[float]]:
    """[tempo, valor] de uma coluna do Sincronizado, no meio de cada janela."""
    if sinc_df is None or coluna not in sinc_df.columns or "start_s" not in sinc_df.columns:
        return []
    inicio = _coluna(sinc_df, "start_s")
    fim = _coluna(sinc_df, "end_s") if "end_s" in sinc_df.columns else inicio
    valor = _coluna(sinc_df, coluna)
    pontos = [
        [round(float((a + (b if pd.notna(b) else a)) / 2), 2), round(float(v), 4)]
        for a, b, v in zip(inicio, fim, valor) if pd.notna(a) and pd.notna(v)
    ]
    return sorted(pontos)


def trechos_transcricao(tr_df: pd.DataFrame, divergencias_df: pd.DataFrame,
                        ativacoes: Optional[List[dict]]) -> List[dict]:
    """Falas da transcrição por tempo, com os selos de divergência e alta ativação."""
    if tr_df is None or tr_df.empty or "seconds" not in tr_df.columns:
        return []
    tr = tr_df.copy()
    tr["_s"] = pd.to_numeric(tr["seconds"], errors="coerce")
    tr = tr[tr["_s"].notna()].sort_values("_s").reset_index(drop=True)
    inicios_div = [float(s) for s in _coluna(divergencias_df, "start_s").dropna()] if divergencias_df is not None else []
    inicios_ativ = [float(m["seconds"]) for m in (ativacoes or []) if _float(m.get("seconds")) is not None]
    itens = []
    for i, linha in tr.iterrows():
        inicio = float(linha["_s"])
        fim = _float(linha.get("end_s"))
        if fim is None or fim <= inicio:
            fim = float(tr.loc[i + 1, "_s"]) if i + 1 < len(tr) else inicio + max(len(str(linha.get("Text", "")).split()) * 0.4, 4.0)
        nota = _float(linha.get("sentimento_texto"))
        itens.append({
            "s": round(inicio, 2),
            "e": round(fim, 2),
            "tempo": tempo_curto(inicio),
            "locutor": str(linha.get("SpeakerName", "") or ""),
            "texto": str(linha.get("Text", "") or "").strip(),
            "nota": nota,
            "nota_txt": numero(nota),
            "divergencia": any(inicio - 0.05 <= s < fim for s in inicios_div),
            "ativacao": any(inicio - 0.05 <= s < fim for s in inicios_ativ),
        })
    return itens


def duracao(segmentos: List[List[float]], trechos: List[dict], duracao_banco: Optional[float]) -> float:
    candidatos = [float(duracao_banco or 0.0)]
    candidatos += [b for _, b in segmentos[-1:]] if segmentos else []
    candidatos += [t["e"] for t in trechos[-1:]] if trechos else []
    return round(max(candidatos + [1.0]), 2)


# ---------------------------------------------------------------------------
# Áudio da API no diretório estático (de audio_timeline.py, sem mudança)
# ---------------------------------------------------------------------------

# Downloads em andamento, por caminho de destino. Vive no processo e não em
# st.session_state porque quem limpa a marcação é a thread de download, que
# roda sem ScriptRunContext e não pode tocar no estado da sessão.
_AUDIO_DOWNLOADS: set[str] = set()
_AUDIO_DOWNLOADS_LOCK = threading.Lock()
# Idade máxima de um .wav no diretório static servido publicamente.
_STATIC_AUDIO_TTL_SECONDS = 2 * 60 * 60


def _claim_audio_download(destino: str) -> bool:
    with _AUDIO_DOWNLOADS_LOCK:
        if destino in _AUDIO_DOWNLOADS:
            return False
        _AUDIO_DOWNLOADS.add(destino)
        return True


def _release_audio_download(destino: str) -> None:
    with _AUDIO_DOWNLOADS_LOCK:
        _AUDIO_DOWNLOADS.discard(destino)


def _purge_stale_static_audio(static_dir: str) -> None:
    """Remove áudios antigos do diretório público, incluindo nomes previsíveis.

    O diretório static é servido sem autenticação, então cada arquivo deixado
    para trás é uma gravação de entrevista exposta por tempo indeterminado.
    """
    agora = time.time()
    for nome in os.listdir(static_dir):
        if not nome.startswith("audio_") or not nome.endswith((".wav", ".wav.part")):
            continue
        caminho = os.path.join(static_dir, nome)
        try:
            legado = nome[len("audio_"):-len(".wav")].isdigit() if nome.endswith(".wav") else False
            if legado or (agora - os.path.getmtime(caminho)) > _STATIC_AUDIO_TTL_SECONDS:
                os.remove(caminho)
        except OSError:
            pass


def preparar_audio_estatico(audio_id: int, audio_api_id: int, session_state, logger) -> str:
    """Nome do .wav no diretório static, com o download disparado em segundo plano.

    O nome é um token aleatório preso à sessão: o diretório static é servido
    sem autenticação e um nome derivado do id seria trivial de enumerar.
    """
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    os.makedirs(static_dir, exist_ok=True)
    _purge_stale_static_audio(static_dir)

    name_key = f"audio_static_name_{audio_id}"
    if name_key not in session_state:
        session_state[name_key] = "audio_{}.wav".format(secrets.token_urlsafe(24))
    nome = session_state[name_key]
    destino = os.path.join(static_dir, nome)

    if not os.path.exists(destino) and _claim_audio_download(destino):
        from utils.whatsapp_api_client import authorize_audio_file_download

        # A posse do áudio depende do login da sessão: é conferida aqui, e a
        # thread recebe o download já autorizado.
        try:
            baixar_audio = authorize_audio_file_download(audio_api_id, kind="wav")
        except BaseException:
            _release_audio_download(destino)
            raise

        def download_bg(api_id=audio_api_id, baixar=baixar_audio, destino=destino):
            # Baixa para um temporário e renomeia: o player nunca busca um
            # .wav escrito pela metade.
            parcial = destino + ".part"
            try:
                audio_bytes = baixar()
                with open(parcial, "wb") as arquivo:
                    arquivo.write(audio_bytes)
                os.replace(parcial, destino)
            except Exception:
                logger.exception("Falha ao baixar o audio %s da API.", api_id)
                try:
                    os.remove(parcial)
                except OSError:
                    pass
            finally:
                _release_audio_download(destino)

        threading.Thread(target=download_bg, daemon=True).start()
    return nome


# ---------------------------------------------------------------------------
# Componentes HTML
# ---------------------------------------------------------------------------

PLAYER_HEIGHT = 74
_LANE = 64
_LANE_GAP = 10


def altura_faixas(mais_sinais: bool) -> int:
    faixas = 3 + (len(MAIS_SINAIS) if mais_sinais else 0)
    return 70 + faixas * (_LANE + _LANE_GAP) + 34 + 28


_BASE_CSS = """
__THEME_VARS__
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--nenc-bg); color: var(--nenc-text);
  font-family: "Source Sans 3", Inter, system-ui, -apple-system, sans-serif; font-size: 13px; }
.num { font-variant-numeric: tabular-nums; }
"""

_PLAYER_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
__BASE_CSS__
body { overflow: hidden; }
.player { display: grid; grid-template-columns: auto auto 1fr auto; align-items: center; gap: 14px;
  padding: 12px 14px; background: #1c1e2c; border: 1px solid var(--nenc-border); border-radius: 8px; height: 62px; }
#play { width: 34px; height: 34px; border-radius: 50%; border: none; background: #9184d9; color: #161826;
  display: flex; align-items: center; justify-content: center; cursor: pointer; padding: 0; }
#play:disabled { background: var(--nenc-border); color: var(--nenc-muted); cursor: default; }
#play svg { width: 14px; height: 14px; }
#clock { font-size: 12px; color: var(--nenc-muted); min-width: 92px; }
#track { position: relative; height: 26px; cursor: pointer; }
#track canvas { width: 100%; height: 26px; display: block; }
.side { display: flex; align-items: center; gap: 12px; font-size: 11px; color: var(--nenc-muted); white-space: nowrap; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #d2cefd; margin-right: 4px; vertical-align: 0; }
.tri { display: inline-block; width: 0; height: 0; border-left: 4.5px solid transparent; border-right: 4.5px solid transparent;
  border-bottom: 8px solid #9184d9; margin-right: 4px; }
select { background: transparent; color: var(--nenc-text); border: 1px solid var(--nenc-border); border-radius: 4px;
  font-size: 11px; padding: 2px 4px; }
#msg { font-size: 11px; color: var(--nenc-muted); }
</style></head><body>
<div class="player">
  <button id="play" disabled title="Tocar">__ICON_PLAY__</button>
  <span id="clock" class="num">0:00 / 0:00</span>
  <div id="track"><canvas id="trackc" height="26"></canvas></div>
  <div class="side"><span id="msg"></span><span><span class="dot"></span>divergência</span>
    <span><span class="tri"></span>alta ativação</span>
    <select id="speed" title="Velocidade"><option value="0.75">0,75×</option><option value="1" selected>1×</option>
      <option value="1.25">1,25×</option><option value="1.5">1,5×</option><option value="2">2×</option></select></div>
</div>
<audio id="audio" preload="auto"></audio>
<script>
const D = __DATA__;
const ICON_PLAY = __ICON_PLAY_JS__, ICON_PAUSE = __ICON_PAUSE_JS__;
const canal = new BroadcastChannel(D.canal);
const audio = document.getElementById('audio');
const play = document.getElementById('play');
const clock = document.getElementById('clock');
const canvas = document.getElementById('trackc');
const msg = document.getElementById('msg');
let dur = D.duracao, t = 0, pronto = false, virtualPlaying = false, ultimo = 0;
const chave = 'nencboost-audio-pos-' + D.audio_id;

function fmt(s) { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(r).padStart(2, '0'); }

function desenhar() {
  const w = canvas.clientWidth; if (!w) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = w * ratio; canvas.height = 26 * ratio;
  const c = canvas.getContext('2d'); c.setTransform(ratio, 0, 0, ratio, 0, 0); c.clearRect(0, 0, w, 26);
  const x = s => Math.max(0, Math.min(w, s / dur * w));
  c.fillStyle = 'rgba(233,233,237,.06)'; c.fillRect(0, 11, w, 4);
  for (const [a, b] of D.segmentos) {
    const xa = x(a), xb = Math.max(x(b), xa + 1);
    if (t >= b) { c.fillStyle = '#796cbf'; c.fillRect(xa, 9, xb - xa, 8); }
    else if (t <= a) { c.fillStyle = '#3f424d'; c.fillRect(xa, 9, xb - xa, 8); }
    else { c.fillStyle = '#796cbf'; c.fillRect(xa, 9, x(t) - xa, 8); c.fillStyle = '#3f424d'; c.fillRect(x(t), 9, xb - x(t), 8); }
  }
  c.fillStyle = '#d2cefd';
  for (const s of D.marcas_div) { c.beginPath(); c.arc(x(s), 4, 4, 0, Math.PI * 2); c.fill(); }
  c.fillStyle = '#9184d9';
  for (const s of D.marcas_ativ) { c.beginPath(); c.moveTo(x(s), 18); c.lineTo(x(s) - 4.5, 26); c.lineTo(x(s) + 4.5, 26); c.fill(); }
  c.fillStyle = '#d2cefd'; c.fillRect(x(t) - 0.75, 0, 1.5, 26);
}

function anunciar(force) {
  clock.textContent = fmt(t) + ' / ' + fmt(dur);
  desenhar();
  const agora = performance.now();
  if (force || agora - ultimo > 90) { ultimo = agora; canal.postMessage({tipo: 'tempo', t: t, d: dur}); }
  try { localStorage.setItem(chave, String(t)); } catch (e) {}
}

function tocando() { return D.tem_audio ? !audio.paused : virtualPlaying; }
function icone() { play.innerHTML = tocando() ? ICON_PAUSE : ICON_PLAY; play.title = tocando() ? 'Pausar' : 'Tocar'; }

function saltar(s, tocar) {
  t = Math.max(0, Math.min(dur, s));
  if (D.tem_audio && pronto) { audio.currentTime = t; if (tocar) audio.play().catch(() => {}); }
  anunciar(true); icone();
}

canvas.addEventListener('click', e => { const r = canvas.getBoundingClientRect(); saltar((e.clientX - r.left) / r.width * dur, true); });
play.addEventListener('click', () => {
  if (!D.tem_audio) { return; }
  if (audio.paused) audio.play().catch(() => {}); else audio.pause();
});
document.getElementById('speed').addEventListener('change', e => { audio.playbackRate = parseFloat(e.target.value); });
audio.addEventListener('play', icone); audio.addEventListener('pause', icone);
audio.addEventListener('timeupdate', () => { t = audio.currentTime; anunciar(false); });
function quadro() { if (D.tem_audio && !audio.paused) { t = audio.currentTime; anunciar(false); } requestAnimationFrame(quadro); }
requestAnimationFrame(quadro);

canal.onmessage = ev => {
  const m = ev.data || {};
  if (m.tipo === 'saltar') saltar(m.t, m.tocar);
  if (m.tipo === 'ola') anunciar(true);
};

// Posição inicial: o foco ("Ouvir"), senão onde o player parou.
let inicio = D.foco;
if (inicio === null) { try { const v = parseFloat(localStorage.getItem(chave)); if (!isNaN(v)) inicio = v; } catch (e) {} }
t = Math.max(0, Math.min(dur, inicio || 0));
anunciar(true); icone();
window.addEventListener('resize', desenhar);

if (D.tem_audio) {
  msg.textContent = 'carregando áudio…';
  let tentativas = 0;
  function carregar() {
    const teste = new Audio(); teste.src = D.url;
    teste.addEventListener('canplaythrough', () => {
      audio.src = D.url;
      audio.addEventListener('loadedmetadata', () => {
        if (isFinite(audio.duration) && audio.duration > 0) dur = audio.duration;
        pronto = true; play.disabled = false; msg.textContent = '';
        audio.currentTime = t; anunciar(true);
        if (D.foco !== null) audio.play().catch(() => {});
      }, {once: true});
    }, {once: true});
    teste.addEventListener('error', () => {
      if (tentativas++ < 60) setTimeout(carregar, 2000);
      else msg.textContent = 'não foi possível carregar o áudio';
    }, {once: true});
  }
  carregar();
} else {
  msg.textContent = D.sem_audio || 'sem gravação';
}
</script></body></html>"""

_FAIXAS_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
__BASE_CSS__
body { overflow: hidden; }
.cols { display: grid; grid-template-columns: 1.45fr 1fr; gap: 16px; height: 100vh; }
.card { border: 1px solid var(--nenc-border); border-radius: 6px; padding: 14px 16px; min-height: 0;
  display: flex; flex-direction: column; }
.head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.head b { font-size: 14px; font-weight: 600; white-space: nowrap; }
.head span { font-size: 11px; color: var(--nenc-faint); }
.head .right { margin-left: auto; white-space: nowrap; }
.lanes { position: relative; }
.lane { display: grid; grid-template-columns: 96px 1fr; gap: 8px; height: __LANE__px; margin-bottom: __GAP__px; }
.lab { display: flex; flex-direction: column; justify-content: center; gap: 2px; }
.lab b { font-size: 12px; font-weight: 600; }
.lab span { font-size: 10px; color: var(--nenc-faint); line-height: 1.25; }
.lane canvas { width: 100%; height: __LANE__px; display: block; cursor: pointer; }
.axis { margin-left: 104px; position: relative; height: 16px; font-size: 10px; color: var(--nenc-faint); }
.axis span { position: absolute; transform: translateX(-50%); }
#needle { position: absolute; top: 0; width: 1.5px; background: #d2cefd; pointer-events: none; }
#lista { overflow-y: auto; flex: 1; min-height: 0; padding-right: 4px; }
.item { padding: 8px 10px; border-radius: 6px; cursor: pointer; margin-bottom: 2px; }
.item:hover { background: rgba(233,233,237,.04); }
.item.atual { background: rgba(145,132,217,.1); box-shadow: inset 2px 0 0 #9184d9; }
.meta { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--nenc-muted); margin-bottom: 3px; }
.chip { font-size: 10px; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--nenc-accent-800); color: var(--nenc-accent-300); }
.chip.div { border-color: rgba(210,206,253,.35); color: #d2cefd; background: rgba(210,206,253,.08); }
.nota { margin-left: auto; }
.fala { font-size: 13px; line-height: 1.5; color: var(--nenc-text); }
.vazio { font-size: 12px; color: var(--nenc-faint); }
::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-thumb { background: var(--nenc-border); border-radius: 3px; }
</style></head><body>
<div class="cols">
  <div class="card">
    <div class="head"><b>Sinais ao longo da fala</b><span>as faixas seguem o player; a faixa clara marca divergência voz × texto</span></div>
    <div class="lanes" id="lanes"><div id="needle"></div></div>
    <div class="axis" id="axis"></div>
  </div>
  <div class="card">
    <div class="head"><b>Transcrição</b><span>acompanha o player; clique num trecho para ouvir dali</span>
      <span class="right num" id="ntrechos"></span></div>
    <div id="lista"></div>
  </div>
</div>
<script>
const D = __DATA__;
const canal = new BroadcastChannel(D.canal);
let t = D.foco || 0, dur = D.duracao;
const lanesEl = document.getElementById('lanes'), needle = document.getElementById('needle');
const LABEL = 104, H = __LANE__;

function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

const faixas = [
  {id: 'indice', titulo: 'Índice combinado', sub: 'texto + voz, −1 a +1', tipo: 'indice'},
  {id: 'emocao', titulo: 'Emoção na voz', sub: 'neutro, alegria, tristeza, raiva', tipo: 'emocao'},
  {id: 'ativacao', titulo: 'Ativação', sub: 'arousal, 0 a 1', tipo: 'ativacao'},
].concat(D.mais.map(m => ({id: m.coluna, titulo: m.titulo, sub: m.unidade, tipo: 'linha', pontos: m.pontos})));

for (const f of faixas) {
  const lane = document.createElement('div'); lane.className = 'lane';
  lane.innerHTML = '<div class="lab"><b>' + esc(f.titulo) + '</b><span>' + esc(f.sub) + '</span></div><canvas></canvas>';
  lanesEl.appendChild(lane);
  f.canvas = lane.querySelector('canvas');
  f.canvas.addEventListener('click', e => {
    const r = f.canvas.getBoundingClientRect();
    canal.postMessage({tipo: 'saltar', t: (e.clientX - r.left) / r.width * dur, tocar: true});
  });
}

function preparar(cv) {
  const w = cv.clientWidth, ratio = window.devicePixelRatio || 1;
  cv.width = w * ratio; cv.height = H * ratio;
  const c = cv.getContext('2d'); c.setTransform(ratio, 0, 0, ratio, 0, 0); c.clearRect(0, 0, w, H);
  return [c, w];
}

function fundo(c, w) {
  c.fillStyle = 'rgba(233,233,237,.03)'; c.fillRect(0, 0, w, H);
  c.fillStyle = 'rgba(210,206,253,.10)';
  for (const [a, b] of D.faixas_div) c.fillRect(a / dur * w, 0, Math.max((b - a) / dur * w, 2), H);
}

function desenharFaixa(f) {
  const [c, w] = preparar(f.canvas); if (!w) return;
  fundo(c, w);
  const x = s => s / dur * w;
  if (f.tipo === 'indice') {
    c.strokeStyle = 'rgba(233,233,237,.25)'; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(0, H / 2); c.lineTo(w, H / 2); c.stroke(); c.setLineDash([]);
    const y = v => H / 2 - v * (H / 2 - 4);
    c.strokeStyle = '#9184d9'; c.lineWidth = 1.6; c.beginPath();
    D.indice.forEach(([a, b, v], i) => { if (i === 0) c.moveTo(x(a), y(v)); else c.lineTo(x(a), y(v)); c.lineTo(x(b), y(v)); });
    c.stroke();
    if (!D.indice.length) vazio(c, w, 'sem índice: falta sentimento do texto ou valência');
  } else if (f.tipo === 'emocao') {
    const cores = ['#595d6c', '#9184d9', '#d2cefd', '#c9708b'];
    for (const [a, b, ...fatias] of D.emocao) {
      let base = H; const xa = x(a), larg = Math.max(x(b) - xa - 1, 1);
      fatias.forEach((p, i) => { const h = p * (H - 2); c.fillStyle = cores[i]; c.fillRect(xa, base - h, larg, h); base -= h; });
    }
    if (!D.emocao.length) vazio(c, w, 'sem categorias de emoção no Sincronizado');
  } else if (f.tipo === 'ativacao') {
    const y = v => H - 2 - Math.max(0, Math.min(1, v)) * (H - 6);
    if (D.ativacao.length) {
      c.beginPath(); c.moveTo(x(D.ativacao[0][0]), H);
      D.ativacao.forEach(([s, v]) => c.lineTo(x(s), y(v)));
      c.lineTo(x(D.ativacao[D.ativacao.length - 1][0]), H); c.closePath();
      c.fillStyle = 'rgba(145,132,217,.16)'; c.fill();
      c.beginPath(); D.ativacao.forEach(([s, v], i) => i ? c.lineTo(x(s), y(v)) : c.moveTo(x(s), y(v)));
      c.strokeStyle = '#b5abfc'; c.lineWidth = 1.4; c.stroke();
    } else vazio(c, w, 'sem arousal no Sincronizado');
  } else {
    const pts = f.pontos || [];
    if (!pts.length) { vazio(c, w, 'sem este sinal no Sincronizado'); return; }
    const vs = pts.map(p => p[1]); const lo = Math.min(...vs), hi = Math.max(...vs), amp = (hi - lo) || 1;
    const y = v => H - 4 - (v - lo) / amp * (H - 8);
    c.beginPath(); pts.forEach(([s, v], i) => i ? c.lineTo(x(s), y(v)) : c.moveTo(x(s), y(v)));
    c.strokeStyle = '#9184d9'; c.lineWidth = 1.2; c.stroke();
  }
}

function vazio(c, w, texto) { c.fillStyle = '#75798c'; c.font = '11px system-ui, sans-serif'; c.fillText(texto, 8, H / 2 + 4); }

function eixo() {
  const ax = document.getElementById('axis'); ax.innerHTML = '';
  const w = lanesEl.clientWidth - LABEL; if (w <= 0) return;
  const passos = [5, 10, 15, 30, 60, 120, 300, 600]; let p = passos.find(s => dur / s <= 6) || 900;
  const fmt = s => Math.floor(s / 60) + ':' + String(Math.round(s % 60)).padStart(2, '0');
  for (let s = 0; s <= dur + 0.01; s += p) { const sp = document.createElement('span'); sp.textContent = fmt(s); sp.style.left = (s / dur * w) + 'px'; ax.appendChild(sp); }
}

function agulha() {
  const w = lanesEl.clientWidth - LABEL;
  needle.style.left = (LABEL + Math.max(0, Math.min(1, t / dur)) * w) + 'px';
  needle.style.height = (lanesEl.scrollHeight - __GAP__) + 'px';
}

function tudo() { faixas.forEach(desenharFaixa); eixo(); agulha(); }

// Transcrição
const lista = document.getElementById('lista');
document.getElementById('ntrechos').textContent = D.trechos.length + ' trechos';
if (!D.trechos.length) lista.innerHTML = '<div class="vazio">Sem transcrição para este áudio.</div>';
const itens = D.trechos.map(tr => {
  const el = document.createElement('div'); el.className = 'item';
  const cor = tr.nota === null ? 'var(--nenc-faint)' : (tr.nota <= -0.2 ? '#d9a0b2' : (tr.nota >= 0.2 ? '#b5abfc' : 'var(--nenc-muted)'));
  el.innerHTML = '<div class="meta"><span class="num">' + esc(tr.tempo) + '</span><span>' + esc(tr.locutor) + '</span>'
    + (tr.divergencia ? '<span class="chip div">divergência</span>' : '')
    + (tr.ativacao ? '<span class="chip">alta ativação</span>' : '')
    + (tr.nota_txt ? '<span class="nota num" style="color:' + cor + '">texto ' + esc(tr.nota_txt) + '</span>' : '')
    + '</div><div class="fala">' + esc(tr.texto) + '</div>';
  el.addEventListener('click', () => canal.postMessage({tipo: 'saltar', t: tr.s, tocar: true}));
  lista.appendChild(el); return el;
});
let atual = -1;
function marcar() {
  let i = D.trechos.findIndex(tr => t >= tr.s && t < tr.e);
  if (i === atual) return;
  if (atual >= 0) itens[atual].classList.remove('atual');
  atual = i;
  if (i >= 0) {
    itens[i].classList.add('atual');
    const el = itens[i], topo = el.offsetTop - lista.offsetTop;
    if (topo < lista.scrollTop || topo + el.offsetHeight > lista.scrollTop + lista.clientHeight)
      lista.scrollTo({top: Math.max(0, topo - lista.clientHeight / 3), behavior: 'smooth'});
  }
}

canal.onmessage = ev => {
  const m = ev.data || {};
  if (m.tipo === 'tempo') { t = m.t; if (m.d && Math.abs(m.d - dur) > 0.5) { dur = m.d; tudo(); } agulha(); marcar(); }
};
window.addEventListener('resize', tudo);
tudo(); marcar();
canal.postMessage({tipo: 'ola'});
</script></body></html>"""


def _icone_svg(nome: str) -> str:
    from utils.icons import icon

    return icon(nome, 14, "#161826")


def player_html(dados: dict) -> str:
    """Player fixo: botão, tempo, trilha com a fala e as marcas, legenda e velocidade."""
    base = _BASE_CSS.replace("__THEME_VARS__", ui.css_variables())
    return (
        _PLAYER_HTML.replace("__BASE_CSS__", base)
        .replace("__ICON_PLAY__", _icone_svg("play"))
        .replace("__ICON_PLAY_JS__", ui.json_para_script(_icone_svg("play")))
        .replace("__ICON_PAUSE_JS__", ui.json_para_script(_icone_svg("pause")))
        .replace("__DATA__", ui.json_para_script(dados))
    )


def faixas_html(dados: dict) -> str:
    """Faixas de sinais e transcrição, presas ao relógio do player."""
    base = _BASE_CSS.replace("__THEME_VARS__", ui.css_variables())
    return (
        _FAIXAS_HTML.replace("__BASE_CSS__", base)
        .replace("__LANE__", str(_LANE))
        .replace("__GAP__", str(_LANE_GAP))
        .replace("__DATA__", ui.json_para_script(dados))
    )
