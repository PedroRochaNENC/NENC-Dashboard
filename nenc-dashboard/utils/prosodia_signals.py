"""Sinais do NencBoost formatados para a IA.

O CSV Sincronizado traz mais do que os prompts recebiam: dominância e as
quatro categorias de emoção ficavam só nos gráficos. Como os prompts já
pediam distribuição de emoções, o modelo respondia que ela não existia.
Estas funções são puras e servem tanto a análise do áudio quanto a do
projeto, para os dois lados enviarem os mesmos sinais.
"""

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

# Rótulos em português, na ordem em que aparecem no relatório.
EMOCOES = [
    ("emocao_happy", "Alegria"),
    ("emocao_neutral", "Neutro"),
    ("emocao_sad", "Tristeza"),
    ("emocao_angry", "Raiva"),
]

# O trio VAD. Dominância entra aqui: é o sinal de segurança/assertividade e
# sem ele não dá para separar uma crítica firme de um desabafo hesitante.
DIMENSOES = [
    ("dim_arousal", "Ativação"),
    ("dim_valence", "Valência"),
    ("dim_dominance", "Dominância"),
]

ACUSTICAS = [
    ("f0_media", "Pitch médio (Hz)"),
    ("f0_variacao", "Variação de pitch"),
    ("loudness_media", "Volume médio"),
    ("loudness_variacao", "Variação de volume"),
    ("speaking_rate", "Ritmo de fala"),
]

_AVISO = (
    "_As categorias de emoção são saídas probabilísticas do classificador, "
    "não diagnóstico do estado interno do respondente._"
)


def _colunas_presentes(df: pd.DataFrame, pares) -> List[tuple]:
    return [(c, rotulo) for c, rotulo in pares if c in df.columns]


def _numerico(df: pd.DataFrame, colunas: List[str]) -> pd.DataFrame:
    work = df.copy()
    for coluna in colunas:
        work[coluna] = pd.to_numeric(work[coluna], errors="coerce")
    return work


def emotion_distribution_text(
    sinc_df: pd.DataFrame,
    group_col: str = "session_id",
    rotulo_grupo: str = "Áudio",
) -> str:
    """Distribuição das categorias de emoção por grupo.

    Para cada segmento vale a categoria de maior probabilidade; a tabela traz
    a fatia de segmentos de cada uma. Segmento sem nenhuma das quatro colunas
    preenchidas fica de fora da contagem.
    """
    if sinc_df.empty:
        return "Nenhum dado de emoção disponível."

    disponiveis = _colunas_presentes(sinc_df, EMOCOES)
    if not disponiveis:
        return "Nenhuma categoria de emoção disponível nos dados."

    colunas = [c for c, _ in disponiveis]
    work = _numerico(sinc_df, colunas)
    work = work.dropna(subset=colunas, how="all")
    if work.empty:
        return "As colunas de emoção existem, mas estão vazias em todos os segmentos."

    work["_dominante"] = work[colunas].idxmax(axis=1)
    if group_col not in work.columns:
        work[group_col] = "geral"

    rotulos = dict(disponiveis)
    linhas = [
        f"| {rotulo_grupo} | Segmentos | "
        + " | ".join(rotulos[c] for c in colunas)
        + " | Predominante |",
        "|---|---|" + "---|" * (len(colunas) + 1),
    ]
    for grupo, bloco in work.groupby(group_col):
        total = len(bloco)
        fatias = []
        for coluna in colunas:
            fatia = (bloco["_dominante"] == coluna).sum() / total * 100
            fatias.append(f"{fatia:.0f}%")
        predominante = rotulos[bloco["_dominante"].value_counts().idxmax()]
        linhas.append(
            f"| {grupo} | {total} | " + " | ".join(fatias) + f" | {predominante} |"
        )

    return "\n".join(linhas) + "\n\n" + _AVISO


def speaker_acoustics_text(
    sinc_df: pd.DataFrame,
    group_col: str = "SpeakerName",
    rotulo_grupo: str = "Locutor",
) -> str:
    """Médias acústicas e dimensionais por locutor.

    A análise do áudio individual só recebia contagem de segmentos e de
    palavras; sem esta tabela, o prompt estatístico pedia médias de F0 e
    loudness que nunca chegavam.
    """
    if sinc_df.empty:
        return "Nenhuma métrica acústica disponível."

    disponiveis = _colunas_presentes(sinc_df, ACUSTICAS + DIMENSOES)
    if not disponiveis:
        return "Nenhuma métrica acústica compatível nos dados."

    colunas = [c for c, _ in disponiveis]
    work = _numerico(sinc_df, colunas)
    if group_col not in work.columns:
        work[group_col] = "Respondente"
    work[group_col] = work[group_col].fillna("Desconhecido")

    agregado = work.groupby(group_col)[colunas].mean().reset_index()
    contagem = work.groupby(group_col).size()

    rotulos = dict(disponiveis)
    linhas = [
        f"| {rotulo_grupo} | Segmentos | " + " | ".join(rotulos[c] for c in colunas) + " |",
        "|---|---|" + "---|" * len(colunas),
    ]
    for _, linha in agregado.iterrows():
        valores = " | ".join(
            f"{linha[c]:.3f}" if pd.notna(linha[c]) else "-" for c in colunas
        )
        linhas.append(f"| {linha[group_col]} | {contagem[linha[group_col]]} | {valores} |")

    return "\n".join(linhas)


def signals_block(
    sinc_df: pd.DataFrame,
    group_col: str = "SpeakerName",
    rotulo_grupo: str = "Locutor",
    emocao_group_col: Optional[str] = None,
) -> str:
    """Bloco pronto com as duas tabelas, para colar no prompt."""
    partes = [
        f"### Perfil Acústico e Dimensional por {rotulo_grupo}",
        "",
        speaker_acoustics_text(sinc_df, group_col, rotulo_grupo),
        "",
        "### Distribuição de Emoções",
        "",
        emotion_distribution_text(
            sinc_df,
            emocao_group_col or group_col,
            rotulo_grupo,
        ),
    ]
    return "\n".join(partes)


# ---------------------------------------------------------------------------
# Sentimento do texto transcrito
#
# A API dá a cada segmento do Whisper uma nota de -1 a +1 (sentimento_texto),
# um rótulo e uma justificativa curta. Na transcrição a nota vem uma vez por
# segmento; no Sincronizado, repetida em cada linha do VAD que cai nele.
# ---------------------------------------------------------------------------

# Divergência voz × texto: texto nítido (|nota| >= 0,5) dito com valência vocal
# a pelo menos 1 desvio-padrão do habitual do locutor, no sentido oposto.
LIMIAR_SENTIMENTO_TEXTO = 0.5
LIMIAR_Z_VALENCIA = 1.0
# Abaixo disto não há como saber o que é "habitual" para o locutor: sem z.
MIN_LINHAS_POR_LOCUTOR = 8

ROTULOS_SENTIMENTO = [
    ("positivo", "Positivo"),
    ("neutro", "Neutro"),
    ("negativo", "Negativo"),
]

AVISO_SENTIMENTO = (
    "_O sentimento do texto é inferência automática de um modelo de linguagem "
    "sobre o que foi dito, não verdade sobre o que o respondente sente._"
)

AVISO_DIVERGENCIA = (
    "_Divergência é candidata a leitura qualitativa (ironia, cortesia "
    "protocolar, insatisfação normalizada), não prova._"
)

_AVISO_LOCUTOR_CORTADO = (
    "_Áudio longo analisado em trechos: os rótulos de locutor recomeçam a cada "
    "trecho, então o sentimento vai pelo áudio inteiro, não por locutor._"
)


def _texto(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    return str(valor).strip()


def formatar_tempo(segundos) -> str:
    try:
        total = float(segundos)
    except (TypeError, ValueError):
        return ""
    if pd.isna(total):
        return ""
    return "{:02d}:{:02d}:{:02d}".format(int(total // 3600), int(total % 3600 // 60), int(total % 60))


def _peso_duracao(df: pd.DataFrame) -> pd.Series:
    """Duração de cada linha, para as médias ponderadas; 1 quando não há tempo."""
    if {"start_s", "end_s"}.issubset(df.columns):
        duracao = pd.to_numeric(df["end_s"], errors="coerce") - pd.to_numeric(df["start_s"], errors="coerce")
    elif "duracao_s" in df.columns:
        duracao = pd.to_numeric(df["duracao_s"], errors="coerce")
    else:
        return pd.Series(1.0, index=df.index)
    return duracao.where(duracao > 0).fillna(0.01).clip(lower=0.01)


def tem_sentimento_texto(df: pd.DataFrame) -> bool:
    return (
        df is not None
        and not df.empty
        and "sentimento_texto" in df.columns
        and pd.to_numeric(df["sentimento_texto"], errors="coerce").notna().any()
    )


def _com_sentimento(tr_df: pd.DataFrame) -> pd.DataFrame:
    work = tr_df.copy()
    work["sentimento_texto"] = pd.to_numeric(work["sentimento_texto"], errors="coerce")
    work = work[work["sentimento_texto"].notna()].copy()
    if "sentimento_rotulo" not in work.columns:
        work["sentimento_rotulo"] = None
    faltando = work["sentimento_rotulo"].isna()
    work.loc[faltando, "sentimento_rotulo"] = work.loc[faltando, "sentimento_texto"].map(
        lambda s: "positivo" if s >= 0.2 else ("negativo" if s <= -0.2 else "neutro")
    )
    work["_peso"] = _peso_duracao(work)
    return work


def sentimento_por_grupo(tr_df: pd.DataFrame, group_col: Optional[str] = "SpeakerName") -> pd.DataFrame:
    """Média ponderada pela duração e fatia do tempo de fala por rótulo, por grupo.

    Colunas: grupo, trechos, media, positivo, neutro, negativo (fatias em 0..1).
    `group_col=None` agrega tudo num grupo só.
    """
    colunas = ["grupo", "trechos", "media", "positivo", "neutro", "negativo"]
    if not tem_sentimento_texto(tr_df):
        return pd.DataFrame(columns=colunas)
    work = _com_sentimento(tr_df)
    if group_col is None or group_col not in work.columns:
        work["_grupo"] = "Todos"
    else:
        work["_grupo"] = work[group_col].fillna("Desconhecido").astype(str)

    linhas = []
    for grupo, bloco in work.groupby("_grupo", sort=True):
        peso_total = bloco["_peso"].sum()
        linha = {
            "grupo": grupo,
            "trechos": len(bloco),
            "media": float((bloco["sentimento_texto"] * bloco["_peso"]).sum() / peso_total),
        }
        for rotulo, _ in ROTULOS_SENTIMENTO:
            linha[rotulo] = float(bloco.loc[bloco["sentimento_rotulo"] == rotulo, "_peso"].sum() / peso_total)
        linhas.append(linha)
    return pd.DataFrame(linhas, columns=colunas)


def _linha_trecho(linha: pd.Series, incluir_grupo: Optional[str]) -> str:
    partes = []
    if incluir_grupo and incluir_grupo in linha.index:
        partes.append("[{}]".format(_texto(linha[incluir_grupo])))
    tempo = formatar_tempo(linha.get("start_s")) or _texto(linha.get("Timestamp"))
    if tempo:
        partes.append("[{}]".format(tempo))
    locutor = _texto(linha.get("SpeakerName")) or "?"
    fala = _texto(linha.get("Text")).replace("\n", " ")
    if len(fala) > 220:
        fala = fala[:220] + "…"
    texto = "{} {} ({:+.2f}): \"{}\"".format(" ".join(partes), locutor, linha["sentimento_texto"], fala)
    justificativa = _texto(linha.get("sentimento_justificativa"))
    if justificativa:
        texto += " — " + justificativa
    return "- " + texto.strip()


def texto_sentimento_resumo(
    tr_df: pd.DataFrame,
    group_col: Optional[str] = "SpeakerName",
    rotulo_grupo: str = "Locutor",
    por_trecho: int = 3,
) -> str:
    """Sentimento do texto por grupo, com os trechos mais positivos e negativos.

    Devolve "" quando não há nota nenhuma: aí a seção não vai para o prompt.
    """
    if not tem_sentimento_texto(tr_df):
        return ""
    tabela = sentimento_por_grupo(tr_df, group_col)
    linhas = [
        "| {} | Trechos com nota | Média (ponderada pela duração) | Positivo | Neutro | Negativo |".format(rotulo_grupo),
        "|---|---|---|---|---|---|",
    ]
    for _, linha in tabela.iterrows():
        linhas.append("| {} | {} | {:+.2f} | {:.0f}% | {:.0f}% | {:.0f}% |".format(
            linha["grupo"], linha["trechos"], linha["media"],
            linha["positivo"] * 100, linha["neutro"] * 100, linha["negativo"] * 100,
        ))
    linhas.append("")
    linhas.append("_Fatias em proporção do tempo de fala com nota; neutro é |nota| < 0,2._")

    work = _com_sentimento(tr_df)
    incluir_grupo = group_col if group_col not in (None, "SpeakerName") else None
    positivos = work[work["sentimento_texto"] > 0].sort_values("sentimento_texto", ascending=False)
    negativos = work[work["sentimento_texto"] < 0].sort_values("sentimento_texto")
    for titulo, bloco in (("Trechos mais positivos", positivos), ("Trechos mais negativos", negativos)):
        if bloco.empty:
            continue
        linhas.extend(["", "**{}**".format(titulo)])
        linhas.extend(_linha_trecho(l, incluir_grupo) for _, l in bloco.head(por_trecho).iterrows())

    linhas.extend(["", AVISO_SENTIMENTO])
    return "\n".join(linhas)


_COLUNAS_DIVERGENCIA = [
    "session_id", "segmento_idx", "SpeakerName", "start_s", "Timestamp", "Text",
    "sentimento_texto", "sentimento_rotulo", "sentimento_justificativa",
    "valencia_voz", "z_valencia", "tipo", "forca",
]


def detectar_divergencias(sinc_df: pd.DataFrame) -> pd.DataFrame:
    """Trechos em que o texto e a voz apontam em sentidos opostos.

    Recebe o Sincronizado normalizado (uma linha por segmento do VAD, com
    SpeakerName, session_id e o sentimento do segmento do Whisper). A valência
    vocal vira z em relação ao próprio locutor no áudio — a DevAIce devolve
    valências comprimidas e puxadas para o negativo, então comparar com zero
    faria quase toda fala positiva parecer divergente. Em áudio cortado em
    trechos (audio_cortado), os rótulos de locutor não se sustentam e a
    referência é o áudio inteiro. Grupos com menos de MIN_LINHAS_POR_LOCUTOR
    linhas ficam sem z.

    O z de cada segmento do Whisper é a média, ponderada pela duração, das
    linhas do VAD que caem nele. Divergente: |nota do texto| >= 0,5 com z <= -1
    para texto positivo, ou z >= +1 para texto negativo. Ordena pela força
    (|nota| + |z|).
    """
    obrigatorias = {"dim_valence", "sentimento_texto", "segmento_idx"}
    if sinc_df is None or sinc_df.empty or not obrigatorias.issubset(sinc_df.columns):
        return pd.DataFrame(columns=_COLUNAS_DIVERGENCIA)

    work = sinc_df.copy()
    if "session_id" not in work.columns:
        work["session_id"] = "audio"
    if "SpeakerName" not in work.columns:
        work["SpeakerName"] = None
    work["dim_valence"] = pd.to_numeric(work["dim_valence"], errors="coerce")
    work["sentimento_texto"] = pd.to_numeric(work["sentimento_texto"], errors="coerce")
    work["segmento_idx"] = pd.to_numeric(work["segmento_idx"], errors="coerce")
    work["_peso"] = _peso_duracao(work)
    cortado = (
        work["audio_cortado"].astype(str).str.strip().str.lower().isin({"true", "1"})
        if "audio_cortado" in work.columns
        else pd.Series(False, index=work.index)
    )
    locutor = work["SpeakerName"].map(_texto)

    # Referência do z: (áudio, locutor), ou só o áudio quando ele foi cortado.
    # Linha sem locutor (pausa sem fala casada) não entra no z por locutor.
    work["_grupo_z"] = work["session_id"].astype(str) + "\x1f" + locutor.where(~cortado, "*")
    work.loc[~cortado & (locutor == ""), "_grupo_z"] = None
    validas = work["dim_valence"].notna() & work["_grupo_z"].notna()
    grupos = work[validas].groupby("_grupo_z")["dim_valence"]
    media = grupos.transform("mean")
    desvio = grupos.transform("std")
    tamanho = grupos.transform("size")
    z = (work.loc[validas, "dim_valence"] - media) / desvio
    z = z.where((tamanho >= MIN_LINHAS_POR_LOCUTOR) & (desvio > 0))
    work["_z"] = z.reindex(work.index)

    trechos = work[work["segmento_idx"].notna() & work["sentimento_texto"].notna() & work["_z"].notna()]
    if trechos.empty:
        return pd.DataFrame(columns=_COLUNAS_DIVERGENCIA)

    linhas = []
    for (sessao, indice), bloco in trechos.groupby(["session_id", "segmento_idx"], sort=False):
        peso = bloco["_peso"]
        score = float(bloco["sentimento_texto"].iloc[0])
        z_medio = float((bloco["_z"] * peso).sum() / peso.sum())
        if abs(score) < LIMIAR_SENTIMENTO_TEXTO:
            continue
        if score > 0 and z_medio <= -LIMIAR_Z_VALENCIA:
            tipo = "texto positivo × voz negativa"
        elif score < 0 and z_medio >= LIMIAR_Z_VALENCIA:
            tipo = "texto negativo × voz positiva"
        else:
            continue
        primeira = bloco.sort_values("start_s").iloc[0] if "start_s" in bloco.columns else bloco.iloc[0]
        linhas.append({
            "session_id": sessao,
            "segmento_idx": int(indice),
            "SpeakerName": _texto(primeira.get("SpeakerName")),
            "start_s": _numero_ou_none(primeira.get("start_s")),
            "Timestamp": _texto(primeira.get("Timestamp")),
            "Text": _texto(primeira.get("Text")),
            "sentimento_texto": score,
            "sentimento_rotulo": _texto(primeira.get("sentimento_rotulo")),
            "sentimento_justificativa": _texto(primeira.get("sentimento_justificativa")),
            "valencia_voz": float((bloco["dim_valence"] * peso).sum() / peso.sum()),
            "z_valencia": z_medio,
            "tipo": tipo,
            "forca": abs(score) + abs(z_medio),
        })

    saida = pd.DataFrame(linhas, columns=_COLUNAS_DIVERGENCIA)
    return saida.sort_values("forca", ascending=False).reset_index(drop=True)


def _numero_ou_none(valor):
    numero = pd.to_numeric(valor, errors="coerce")
    return None if pd.isna(numero) else float(numero)


def divergencias_texto(div_df: pd.DataFrame, incluir_audio: bool = False, limite: int = 15) -> str:
    """Tabela das divergências voz × texto para o prompt."""
    if div_df is None or div_df.empty:
        return (
            "Nenhuma divergência voz × texto acima dos limiares (|nota do texto| >= "
            "{:.1f} com a valência vocal a {:.0f} desvio-padrão ou mais do habitual do "
            "locutor, no sentido oposto).".format(LIMIAR_SENTIMENTO_TEXTO, LIMIAR_Z_VALENCIA)
        )
    cabecalho = ["Áudio"] if incluir_audio else []
    cabecalho += ["Tempo", "Locutor", "Fala", "Texto (nota)", "Voz (valência, z)", "Leitura"]
    linhas = [
        "| " + " | ".join(cabecalho) + " |",
        "|" + "---|" * len(cabecalho),
    ]
    for _, linha in div_df.head(limite).iterrows():
        fala = _texto(linha["Text"]).replace("|", "/").replace("\n", " ")
        if len(fala) > 200:
            fala = fala[:200] + "…"
        justificativa = _texto(linha["sentimento_justificativa"]).replace("|", "/")
        celulas = [_texto(linha["session_id"])] if incluir_audio else []
        celulas += [
            formatar_tempo(linha["start_s"]) or _texto(linha["Timestamp"]),
            _texto(linha["SpeakerName"]) or "?",
            "\"{}\"".format(fala),
            "{:+.2f}{}".format(linha["sentimento_texto"], " — " + justificativa if justificativa else ""),
            "{:+.2f} (z {:+.1f})".format(linha["valencia_voz"], linha["z_valencia"]),
            linha["tipo"],
        ]
        linhas.append("| " + " | ".join(celulas) + " |")
    extras = len(div_df) - limite
    if extras > 0:
        linhas.append("")
        linhas.append("_Mais {} divergência(s) de menor força fora da tabela._".format(extras))
    linhas.extend([
        "",
        "_z: desvios-padrão da valência vocal em relação ao habitual do próprio locutor "
        "no áudio (ao áudio inteiro, quando ele foi analisado em trechos)._",
        AVISO_DIVERGENCIA,
    ])
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Índice combinado de sentimento (texto + voz)
#
# Uma nota de -1 a +1 por trecho, metade do texto e metade da voz. A valência
# da DevAIce não serve crua (comprimida e puxada para o negativo), e o z por
# locutor da divergência zera a média de cada locutor por construção — somada,
# a voz sumiria do índice de qualquer áudio. A régua aqui é o projeto: a voz é
# o z da valência em relação a todas as linhas do VAD dos áudios do projeto,
# dividido por 2 e limitado a ±1 (dois desvios equivalem ao extremo do texto).
# ---------------------------------------------------------------------------

PESO_TEXTO_INDICE = 0.5
PESO_VOZ_INDICE = 0.5
# z da valência que equivale a ±1 na escala do texto.
Z_EXTREMO_VOZ = 2.0

AVISO_INDICE = (
    "_O índice combinado soma duas inferências automáticas — o modelo de "
    "linguagem sobre o que foi dito e o classificador sobre a voz — e só faz "
    "sentido comparado dentro do projeto, não como medida absoluta._"
)


@dataclass(frozen=True)
class ReferenciaVoz:
    """Média e desvio da valência vocal que servem de régua ao índice."""

    media: float
    desvio: float
    linhas: int
    audios: int


def referencia_valencia(sinc_df: Optional[pd.DataFrame]) -> Optional[ReferenciaVoz]:
    """Régua da voz a partir do Sincronizado de todos os áudios do projeto.

    None quando não há linhas suficientes ou a valência não varia: sem régua
    não há como dizer se a voz está acima ou abaixo do habitual.
    """
    if sinc_df is None or sinc_df.empty or "dim_valence" not in sinc_df.columns:
        return None
    valencia = pd.to_numeric(sinc_df["dim_valence"], errors="coerce")
    validas = valencia.notna()
    if validas.sum() < MIN_LINHAS_POR_LOCUTOR:
        return None
    desvio = float(valencia[validas].std())
    if not desvio > 0:
        return None
    audios = sinc_df.loc[validas, "session_id"].nunique() if "session_id" in sinc_df.columns else 1
    return ReferenciaVoz(float(valencia[validas].mean()), desvio, int(validas.sum()), int(audios))


_COLUNAS_INDICE_TRECHO = [
    "session_id", "segmento_idx", "SpeakerName", "start_s", "Timestamp", "Text",
    "texto", "voz", "indice", "_peso",
]


def indice_combinado_por_trecho(sinc_df: Optional[pd.DataFrame], referencia: Optional[ReferenciaVoz]) -> pd.DataFrame:
    """Índice de cada segmento do Whisper que tem nota do texto e valência.

    A voz do segmento é a média, ponderada pela duração, das linhas do VAD que
    caem nele. Trecho sem uma das duas leituras fica de fora: o índice não
    completa a metade que falta com a outra.
    """
    obrigatorias = {"dim_valence", "sentimento_texto", "segmento_idx"}
    if (
        referencia is None
        or sinc_df is None
        or sinc_df.empty
        or not obrigatorias.issubset(sinc_df.columns)
    ):
        return pd.DataFrame(columns=_COLUNAS_INDICE_TRECHO)

    work = sinc_df.copy()
    if "session_id" not in work.columns:
        work["session_id"] = "audio"
    if "SpeakerName" not in work.columns:
        work["SpeakerName"] = None
    work["dim_valence"] = pd.to_numeric(work["dim_valence"], errors="coerce")
    work["sentimento_texto"] = pd.to_numeric(work["sentimento_texto"], errors="coerce")
    work["segmento_idx"] = pd.to_numeric(work["segmento_idx"], errors="coerce")
    work["_peso"] = _peso_duracao(work)
    work["_voz"] = ((work["dim_valence"] - referencia.media) / referencia.desvio / Z_EXTREMO_VOZ).clip(-1.0, 1.0)

    trechos = work[work["segmento_idx"].notna() & work["sentimento_texto"].notna() & work["_voz"].notna()]
    if trechos.empty:
        return pd.DataFrame(columns=_COLUNAS_INDICE_TRECHO)

    linhas = []
    for (sessao, indice), bloco in trechos.groupby(["session_id", "segmento_idx"], sort=False):
        peso = bloco["_peso"]
        texto = float(bloco["sentimento_texto"].iloc[0])
        voz = float((bloco["_voz"] * peso).sum() / peso.sum())
        primeira = bloco.sort_values("start_s").iloc[0] if "start_s" in bloco.columns else bloco.iloc[0]
        linhas.append({
            "session_id": sessao,
            "segmento_idx": int(indice),
            "SpeakerName": _texto(primeira.get("SpeakerName")),
            "start_s": _numero_ou_none(primeira.get("start_s")),
            "Timestamp": _texto(primeira.get("Timestamp")),
            "Text": _texto(primeira.get("Text")),
            "texto": texto,
            "voz": voz,
            "indice": PESO_TEXTO_INDICE * texto + PESO_VOZ_INDICE * voz,
            "_peso": float(peso.sum()),
        })
    return pd.DataFrame(linhas, columns=_COLUNAS_INDICE_TRECHO)


_COLUNAS_INDICE_GRUPO = ["grupo", "trechos", "texto", "voz", "indice", "positivo", "neutro", "negativo"]


def indice_combinado_por_grupo(
    sinc_df: Optional[pd.DataFrame],
    referencia: Optional[ReferenciaVoz],
    group_col: Optional[str] = "SpeakerName",
) -> pd.DataFrame:
    """Índice combinado por grupo, ponderado pela duração dos trechos.

    Colunas: grupo, trechos, texto e voz (as duas metades, já na escala -1..+1),
    indice, e as fatias do tempo com índice positivo, neutro e negativo (mesmo
    corte de ±0,2 do sentimento do texto). `group_col=None` agrega tudo.
    """
    trechos = indice_combinado_por_trecho(sinc_df, referencia)
    if trechos.empty:
        return pd.DataFrame(columns=_COLUNAS_INDICE_GRUPO)
    if group_col is None or group_col not in trechos.columns:
        trechos["_grupo"] = "Todos"
    else:
        trechos["_grupo"] = trechos[group_col].map(_texto).replace("", "Desconhecido")

    linhas = []
    for grupo, bloco in trechos.groupby("_grupo", sort=True):
        peso = bloco["_peso"]
        total = peso.sum()
        linhas.append({
            "grupo": grupo,
            "trechos": len(bloco),
            "texto": float((bloco["texto"] * peso).sum() / total),
            "voz": float((bloco["voz"] * peso).sum() / total),
            "indice": float((bloco["indice"] * peso).sum() / total),
            "positivo": float(peso[bloco["indice"] >= 0.2].sum() / total),
            "neutro": float(peso[bloco["indice"].abs() < 0.2].sum() / total),
            "negativo": float(peso[bloco["indice"] <= -0.2].sum() / total),
        })
    return pd.DataFrame(linhas, columns=_COLUNAS_INDICE_GRUPO)


def indice_combinado_texto(
    sinc_df: Optional[pd.DataFrame],
    referencia: Optional[ReferenciaVoz],
    group_col: Optional[str] = "SpeakerName",
    rotulo_grupo: str = "Locutor",
) -> str:
    """Tabela do índice combinado para o prompt; "" quando não há como calcular."""
    tabela = indice_combinado_por_grupo(sinc_df, referencia, group_col)
    if tabela.empty:
        return ""
    linhas = [
        "### Índice Combinado de Sentimento (texto + voz)",
        "",
        "| {} | Trechos | Texto | Voz | Índice | Positivo | Neutro | Negativo |".format(rotulo_grupo),
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, linha in tabela.iterrows():
        linhas.append("| {} | {} | {:+.2f} | {:+.2f} | {:+.2f} | {:.0f}% | {:.0f}% | {:.0f}% |".format(
            linha["grupo"], linha["trechos"], linha["texto"], linha["voz"], linha["indice"],
            linha["positivo"] * 100, linha["neutro"] * 100, linha["negativo"] * 100,
        ))
    linhas.extend([
        "",
        "_Índice = metade da nota do texto + metade da voz, de -1 a +1, ponderado pela "
        "duração. Voz = desvios-padrão da valência vocal em relação a {} áudio(s) do "
        "projeto, divididos por {:.0f} e limitados a ±1. Fatias: |índice| < 0,2 é neutro._".format(
            referencia.audios, Z_EXTREMO_VOZ
        ),
        AVISO_INDICE,
    ])
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Momentos de maior ativação prosódica
# ---------------------------------------------------------------------------

def selecionar_momentos_ativacao(sinc_df: pd.DataFrame, top_n: int = 10, min_altos: int = 3) -> pd.DataFrame:
    """Linhas do Sincronizado com a maior combinação de arousal e variação de voz.

    Parte das linhas com arousal > 0,4 (ou do percentil 85, se forem menos de
    `min_altos`) e ordena pela soma dos ranks de arousal, variação de pitch e
    de volume. Várias linhas do VAD que caem no mesmo segmento do Whisper
    contam uma vez: fica a de maior pontuação.
    """
    if sinc_df is None or sinc_df.empty:
        return pd.DataFrame()

    work = sinc_df.copy()
    if "dim_arousal" in work.columns:
        work["dim_arousal"] = pd.to_numeric(work["dim_arousal"], errors="coerce").fillna(0.0)
        high_ar = work[work["dim_arousal"] > 0.4]
        if len(high_ar) < min_altos:
            q = work["dim_arousal"].quantile(0.85)
            high_ar = work[work["dim_arousal"] >= q]
        work = high_ar.copy()

    if work.empty:
        return pd.DataFrame()

    rank_f0 = work["f0_variacao"].rank(pct=True) if "f0_variacao" in work.columns else 0.0
    rank_ld = work["loudness_variacao"].rank(pct=True) if "loudness_variacao" in work.columns else 0.0
    rank_ar = work["dim_arousal"].rank(pct=True) if "dim_arousal" in work.columns else 0.0

    work["activation_score"] = rank_f0 + rank_ld + rank_ar
    work = work.sort_values(by="activation_score", ascending=False)

    if "segmento_idx" in work.columns:
        indice = pd.to_numeric(work["segmento_idx"], errors="coerce")
        sessao = work["session_id"].astype(str) if "session_id" in work.columns else ""
        chave = sessao + "\x1f" + indice.astype(str)
        work = work[indice.isna() | ~chave.duplicated(keep="first")]

    # Índice posicional: a tela monta as colunas a partir de listas e de
    # colunas deste frame, e com o índice original o pandas as desalinhava.
    return work.head(top_n).reset_index(drop=True)


def _float(valor, padrao: float = 0.0) -> float:
    numero = _numero_ou_none(valor)
    return padrao if numero is None else numero


def momentos_alta_ativacao(sinc_df: pd.DataFrame, top_n: int = 10) -> List[dict]:
    """Momentos de maior ativação de um áudio, no formato gravado no banco.

    `seconds` e o Tempo são o início do segmento do VAD (start_s): é ali que a
    ativação foi medida e é para ali que a Timeline salta.
    """
    from utils.prosodia_loader import extract_topic_from_text

    momentos = []
    for _, row in selecionar_momentos_ativacao(sinc_df, top_n=top_n).iterrows():
        inicio = _numero_ou_none(row.get("start_s"))
        if inicio is None:
            inicio = _float(row.get("seconds"))
        texto = _texto(row.get("Text"))
        momento = {
            "session_id": _texto(row.get("session_id")),
            "SpeakerName": _texto(row.get("SpeakerName")),
            "Timestamp": formatar_tempo(inicio) or _texto(row.get("Timestamp")),
            "Text": texto,
            "seconds": inicio,
            "start_s": inicio,
            "dim_arousal": _float(row.get("dim_arousal")),
            "f0_variacao": _float(row.get("f0_variacao")),
            "loudness_variacao": _float(row.get("loudness_variacao")),
            "dim_valence": _numero_ou_none(row.get("dim_valence")),
            "dim_dominance": _numero_ou_none(row.get("dim_dominance")),
            "topic": extract_topic_from_text(texto),
        }
        indice = _numero_ou_none(row.get("segmento_idx"))
        if indice is not None:
            momento["segmento_idx"] = int(indice)
        nota = _numero_ou_none(row.get("sentimento_texto"))
        if nota is not None:
            momento["sentimento_texto"] = nota
        momentos.append(momento)
    return momentos


def formatar_momentos_ativacao(momentos: Optional[List[dict]]) -> str:
    """Tabela dos momentos de maior ativação de um áudio, para o prompt."""
    if not momentos:
        return ""
    from utils.prosodia_loader import extract_topic_from_text

    def _valor(momento, chave):
        numero = _numero_ou_none(momento.get(chave))
        return "-" if numero is None else "{:.2f}".format(numero)

    linhas = [
        "Momentos de Maior Ativação Prosódica no Áudio:",
        "| Tópico | Locutor | Tempo | Fala | Arousal | Valência | Dominância | Variação Pitch | Variação Volume |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for m in momentos:
        texto = _texto(m.get("Text")).replace("|", "/").replace("\n", " ")
        linhas.append(
            "| {} | {} | {} | \"{}\" | {} | {} | {} | {} | {} |".format(
                m.get("topic") or extract_topic_from_text(texto),
                _texto(m.get("SpeakerName")) or "Desconhecido",
                _texto(m.get("Timestamp")),
                texto,
                _valor(m, "dim_arousal"),
                _valor(m, "dim_valence"),
                _valor(m, "dim_dominance"),
                _valor(m, "f0_variacao"),
                _valor(m, "loudness_variacao"),
            )
        )
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Evidências de um áudio para a IA
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenciasAudio:
    """O que a análise de um áudio manda para a IA, em três blocos.

    `tabelas` vai em <dados_prosodicos>; `sentimento_texto` e `divergencias`,
    nas seções próprias do prompt, que somem quando vêm vazias.
    """

    tabelas: str
    sentimento_texto: str = ""
    divergencias: str = ""


def _audio_cortado(sinc_df: pd.DataFrame) -> bool:
    return (
        sinc_df is not None
        and "audio_cortado" in sinc_df.columns
        and sinc_df["audio_cortado"].astype(str).str.strip().str.lower().isin({"true", "1"}).any()
    )


def montar_evidencias_audio(
    vad_df: pd.DataFrame,
    tr_df: pd.DataFrame,
    sinc_df: Optional[pd.DataFrame],
    momentos: Optional[List[dict]] = None,
    referencia_voz: Optional[ReferenciaVoz] = None,
) -> EvidenciasAudio:
    """Único construtor das evidências da análise individual.

    `sinc_df` é o Sincronizado normalizado (normalizar_sincronizado). Todos os
    caminhos — análise na página, sincronização, importação, upload e
    reprocessamento — passam por aqui para mandar à IA os mesmos sinais.

    `referencia_voz` é a régua do projeto (referencia_valencia); com ela o
    índice combinado entra junto do sentimento do texto. Sem ela o índice fica
    de fora: medido só contra o próprio áudio, a média dele sairia zero.
    """
    sinc_df = sinc_df if sinc_df is not None else pd.DataFrame()
    linhas = []
    if not vad_df.empty and "duration" in vad_df.columns:
        total_s = vad_df["duration"].sum()
        linhas.append(f"VAD: {len(vad_df)} segmentos, {total_s:.1f}s de fala total.")
    if not tr_df.empty and "SpeakerName" in tr_df.columns:
        palavras = (
            tr_df["word_count"] if "word_count" in tr_df.columns
            else tr_df["Text"].fillna("").astype(str).str.split().str.len()
        )
        by_spk = (
            tr_df.assign(_palavras=palavras)
            .groupby("SpeakerName")
            .agg(msgs=("Text", "count"), words=("_palavras", "sum"))
            .reset_index()
        )
        linhas.append("Participação por locutor:\n" + by_spk.to_string(index=False))

    # Sem este bloco a análise individual recebia só contagem de segmentos e de
    # palavras, enquanto o prompt estatístico pedia médias de F0, loudness e
    # distribuição de emoções.
    if not sinc_df.empty:
        linhas.append(signals_block(sinc_df))

    tabela_momentos = formatar_momentos_ativacao(momentos)
    if tabela_momentos:
        linhas.append(tabela_momentos)

    fonte_sentimento = tr_df if tem_sentimento_texto(tr_df) else sinc_df
    sentimento = ""
    divergencias = ""
    if tem_sentimento_texto(fonte_sentimento):
        cortado = _audio_cortado(sinc_df)
        if cortado:
            sentimento = texto_sentimento_resumo(fonte_sentimento, None, "Áudio")
            sentimento += "\n\n" + _AVISO_LOCUTOR_CORTADO
        else:
            sentimento = texto_sentimento_resumo(fonte_sentimento)
        if tem_sentimento_texto(sinc_df):
            divergencias = divergencias_texto(detectar_divergencias(sinc_df))
            indice = (
                indice_combinado_texto(sinc_df, referencia_voz, None, "Áudio")
                if cortado
                else indice_combinado_texto(sinc_df, referencia_voz)
            )
            if indice:
                sentimento += "\n\n" + indice

    return EvidenciasAudio("\n\n".join(linhas), sentimento, divergencias)
