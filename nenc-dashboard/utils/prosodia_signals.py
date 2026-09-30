"""Sinais do NencBoost formatados para a IA.

O CSV Sincronizado traz mais do que os prompts recebiam: dominância e as
quatro categorias de emoção ficavam só nos gráficos. Como os prompts já
pediam distribuição de emoções, o modelo respondia que ela não existia.
Estas funções são puras e servem tanto a análise do áudio quanto a do
projeto, para os dois lados enviarem os mesmos sinais.
"""

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
