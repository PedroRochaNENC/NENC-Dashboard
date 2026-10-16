"""
Agregação da Análise Geral v2 do NencBoost (fase 3 de docs/SPEC_ANALISE_GERAL.md).

Todo número do relatório sai daqui, calculado em código: quantos respondentes
trataram de cada subtema e a faixa de cada um, a leitura da voz por tema, a
Matriz de Destaques, o Termômetro, a comparação com a análise anterior, os
trechos de evidência que vão para a IA, os aliases "Áudio NN" e o apêndice
técnico. A IA recebe as tabelas prontas e escreve só o texto.

Entram a classificação de cada áudio (prosodia_temas), já conferida contra a
chave de agora, e os dataframes que a Análise Geral monta do projeto inteiro:
o Sincronizado normalizado e a transcrição. Nada aqui lê o banco.

A fase 4 fecha o relatório: a mensagem da IA com as tabelas prontas, a
separação do texto dela nas seções, a montagem na ordem fixa e a verificação
das referências (escrever_relatorio faz as quatro, com a IA injetada).
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Collection, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

from utils import prosodia_prompts as prompts
from utils import prosodia_signals as sinais
from utils.prosodia_project_types import (
    ENTREVISTA_QUALITATIVA,
    PESQUISA_OPINIAO,
    normalize_project_type,
)
from utils.prosodia_temas import (
    TAXONOMIA_PROVISORIA,
    TAXONOMIA_STATUS_LABELS,
    TAXONOMIA_VALIDADA,
    Linha,
    RespostaInvalida,
    Taxonomia,
    chave_classificacao,
    chave_rotulo,
    juntar_polaridades,
)

# ---------------------------------------------------------------------------
# Regras da spec (§7 e §14). Os limiares de voz são iniciais e serão
# calibrados na validação com a Smart Fit.
# ---------------------------------------------------------------------------

MENCAO_ISOLADA = "Menção isolada"
SINAL = "Sinal"
PADRAO = "Padrão"

# Respondentes no subtema: 1–2 é menção isolada; 3–4, ou 5 ou mais com menos
# de 10% de N, é sinal; 5 ou mais e pelo menos 10% de N, padrão.
_MIN_SINAL = 3
_MIN_PADRAO = 5
_PERCENTUAL_PADRAO = 10

# Abaixo disto o relatório sai como leitura exploratória.
MIN_RESPONDENTES = 20

# Voz por tema: valência é a metade de voz do índice (±1), ativação é z. A
# leitura pede 5 respondentes com voz no tema: na validação com a Smart Fit, a
# média dos trechos mudava de direção ao tirar uma só pessoa que falou muito.
LIMIAR_VALENCIA = 0.15
LIMIAR_ATIVACAO = 0.3
MIN_RESPONDENTES_VOZ = 5

# O mesmo corte de ±0,2 do sentimento do texto no resto do NencBoost.
LIMIAR_TEXTO = 0.2

MIN_AUDIOS_MATRIZ = 10
_MAX_LINHAS_MATRIZ = 15
# Tema que entra em "Temas mais citados no grupo": de pelo menos 2 áudios.
_MIN_AUDIOS_TEMA_NO_GRUPO = 2
_MAX_TEMAS_NO_GRUPO = 3

ESCALA_TERMOMETRO = 5

# Δ: com menos respondentes novos a coluna não aparece, e com menos de 20 a
# diferença é variação observada, não tendência.
MIN_NOVOS_DELTA = 5
MIN_NOVOS_TENDENCIA = 20

MAX_TRECHOS_POR_TEMA = 6
_MAX_EMERGENTES = 10
_MAX_FALA = 220
# Linha.inicio_s vem arredondado a duas casas; o VAD, não.
_TOLERANCIA_TEMPO = 0.01

POLARIDADES = ("positiva", "negativa", "mista", "neutra")
_ABREVIACOES = {
    "positiva": ("pos", "pos"),
    "negativa": ("neg", "neg"),
    "mista": ("mista", "mistas"),
    "neutra": ("neutra", "neutras"),
}

POSITIVO = "positivo"
NEGATIVO = "negativo"
MISTO_OU_NEUTRO = "misto ou neutro"

ACIMA = "acima do habitual"
NO_HABITUAL = "no habitual"
ABAIXO = "abaixo do habitual"

# Voz no habitual só "atenua" com ativação alta ou baixa. Com ativação média é
# o estado da maioria dos temas e não diz nada sobre o texto: na validação com
# a Smart Fit, a IA leu esse "atenua" como satisfação protocolar mesmo com o
# prompt pedindo o contrário.
_QUALIFICADORES = {
    POSITIVO: {ACIMA: "reforça", NO_HABITUAL: "atenua", ABAIXO: "contradiz"},
    NEGATIVO: {ACIMA: "contradiz", NO_HABITUAL: "atenua", ABAIXO: "reforça"},
}
_NIVEIS_QUE_ATENUAM = ("alta", "baixa")

# Texto das análises sem snapshot que foram geradas com filtro (analise_geral.py).
_MARCA_RECORTE = "Recorte analisado"


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------

def _decimal(valor: float, casas: int = 2) -> str:
    """+0,82 e -0,64: sinal sempre, vírgula decimal; zero sai sem sinal."""
    if round(valor, casas) == 0:
        return "{:.{}f}".format(0.0, casas).replace(".", ",")
    return "{:+.{}f}".format(valor, casas).replace(".", ",")


def _enxuto(valor: float) -> str:
    """Duas casas sem zeros à direita: -0,9 e +1 em vez de -0,90 e +1,00."""
    if round(valor, 2) == 0:
        return "0"
    return "{:+.2f}".format(valor).rstrip("0").rstrip(".").replace(".", ",")


def _percentual(parte: int, total: int) -> int:
    return math.floor(100 * parte / total + 0.5) if total else 0


def _plural(n: int, singular: str, plural: str) -> str:
    return "{} {}".format(n, singular if n == 1 else plural)


def _tempo(segundos: Optional[float]) -> str:
    """mm:ss, ou h:mm:ss passada a primeira hora; vazio sem tempo."""
    if segundos is None:
        return ""
    horas, resto = divmod(int(segundos), 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return "{:d}:{:02d}:{:02d}".format(horas, minutos, segs)
    return "{:02d}:{:02d}".format(minutos, segs)


def _celula(texto) -> str:
    """Texto numa célula de tabela markdown: uma linha, sem barra vertical."""
    return " ".join(str(texto or "").replace("|", "/").split())


def _fala(texto: str) -> str:
    fala = _celula(texto)
    return fala if len(fala) <= _MAX_FALA else fala[:_MAX_FALA].rstrip() + "…"


def _tabela(cabecalho: Sequence[str], linhas: Iterable[Sequence[str]]) -> str:
    saida = ["| " + " | ".join(cabecalho) + " |", "|" + "---|" * len(cabecalho)]
    saida.extend("| " + " | ".join(_celula(c) for c in linha) + " |" for linha in linhas)
    return "\n".join(saida)


def _numero(valor) -> Optional[float]:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(numero) or math.isinf(numero) else numero


def _inteiro(valor) -> Optional[int]:
    numero = _numero(valor)
    return int(numero) if numero is not None and numero.is_integer() else None


def _lista(valor) -> List[dict]:
    return [item for item in valor if isinstance(item, dict)] if isinstance(valor, list) else []


def _polaridade(valor) -> str:
    return valor if valor in POLARIDADES else "neutra"


def ler_data(valor) -> Optional[datetime]:
    """Data do banco (created_at) como datetime sem fuso, ou None."""
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=None)
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto).replace(tzinfo=None)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Áudios, respondentes e aliases
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioDoProjeto:
    """Um áudio do projeto, com o que a agregação usa dele.

    `linhas` é a transcrição numerada (linhas_da_transcricao). `classificacao`
    é o JSON gravado em audio_temas, só quando ainda vale
    (classificacoes_validas); None deixa o áudio fora da contagem de temas.
    `criado_em` é audios.created_at, do mesmo relógio que
    project_analyses.created_at: é com ele que o Δ separa novos de
    anteriores. `qr` e `chegada` só aparecem na legenda dos aliases.
    `conteudo` identifica o arquivo (assinatura_do_conteudo): áudios do
    recorte com o mesmo conteúdo são o mesmo arquivo enviado de novo e contam
    uma vez.
    """

    audio_id: int
    session_id: str
    linhas: Tuple[Linha, ...] = ()
    classificacao: Optional[dict] = None
    criado_em: Optional[datetime] = None
    qr: str = ""
    chegada: str = ""
    conteudo: Optional[str] = None


def assinatura_do_conteudo(prosodia_json: Optional[bytes]) -> Optional[str]:
    """O que identifica o arquivo de áudio: o hash do prosodia_json, ou None.

    O prosodia_json é a saída da API de prosódia para o arquivo (VAD, emoções,
    prosódia e ASR de cada segmento), e o mesmo arquivo enviado de novo dá o
    mesmo JSON, byte a byte. A transcrição e o Sincronizado não servem: o
    Whisper os refaz a cada envio, e eles saem um pouco diferentes. JSON sem
    nenhum segmento de VAD fica sem assinatura, porque dois áudios mudos
    dariam o mesmo.
    """
    if not prosodia_json:
        return None
    try:
        dados = json.loads(prosodia_json)
    except (TypeError, ValueError):
        return None
    resultado = dados.get("result", dados) if isinstance(dados, dict) else None
    if not isinstance(resultado, dict) or not resultado.get("vad"):
        return None
    return hashlib.sha256(prosodia_json).hexdigest()


def _repetidos(audios: Iterable[AudioDoProjeto]) -> Dict[int, int]:
    """Cópia → o áudio que conta por ela, entre os áudios com o mesmo conteúdo.

    Conta o primeiro do grupo com classificação válida, ou o primeiro, se
    nenhum tem: uma classificação por arquivo, para a variação da IA entre
    cópias do mesmo depoimento não somar menções.
    """
    grupos: Dict[str, List[AudioDoProjeto]] = {}
    for audio in sorted(audios, key=lambda a: a.audio_id):
        if audio.conteudo:
            grupos.setdefault(audio.conteudo, []).append(audio)
    repetidos: Dict[int, int] = {}
    for grupo in grupos.values():
        fica = next((a for a in grupo if a.classificacao is not None), grupo[0])
        repetidos.update((a.audio_id, fica.audio_id) for a in grupo if a is not fica)
    return repetidos


def chave_respondente(session_id: str, tipo_projeto: Optional[str]) -> str:
    """Quem conta como um respondente (§14 da spec).

    Na Pesquisa de Opinião, os áudios do WhatsApp do mesmo número
    (wa_<número>_<id>) são de uma pessoa só. Na Entrevista, o número que envia
    é o do entrevistador, então cada áudio é um respondente, como nas demais
    origens. A chave carrega o telefone e nunca vai para o relatório.
    """
    sessao = str(session_id or "")
    if normalize_project_type(tipo_projeto) == PESQUISA_OPINIAO:
        partes = sessao.split("_")
        if (
            len(partes) >= 3
            and partes[0] == "wa"
            and partes[1] != "upload"
            and sum(c.isdigit() for c in partes[1]) >= 8
        ):
            return "wa_" + partes[1]
    return sessao


def aliases(audios: Iterable[AudioDoProjeto]) -> Dict[int, str]:
    """"Áudio NN" de cada áudio do projeto, na ordem em que entrou no banco.

    O corpo do relatório cita o alias, e não o session_id, que traz o
    telefone. A numeração é do projeto inteiro, então o mesmo áudio tem o
    mesmo alias com ou sem filtro.
    """
    ids = sorted({audio.audio_id for audio in audios})
    largura = max(2, len(str(len(ids))))
    return {audio_id: "Áudio {:0{}d}".format(numero, largura) for numero, audio_id in enumerate(ids, 1)}


def classificacoes_validas(
    linhas_por_audio: Dict[int, Sequence[Linha]],
    salvas: Dict[int, dict],
    taxonomia: Taxonomia,
    tipo_projeto: Optional[str],
) -> Dict[int, dict]:
    """A classificação gravada de cada áudio, quando ainda vale.

    `salvas` é o que get_audio_temas_for_project devolve. Vale a
    classificação cuja chave bate com a da taxonomia, do tipo e da transcrição
    de agora: a de outra versão da taxonomia contaria subtemas que não
    existem mais.
    """
    validas = {}
    for audio_id, linhas in linhas_por_audio.items():
        salva = salvas.get(audio_id)
        if salva and salva.get("chave") == chave_classificacao(taxonomia, tipo_projeto, linhas):
            validas[audio_id] = salva.get("temas") or {}
    return validas


def _devolver_emergentes(classificacao: dict, subtemas: Dict[Tuple[str, str], str]) -> dict:
    """A classificação com o emergente que é um subtema contado nele.

    A IA às vezes cria um emergente e dá como categoria o rótulo inteiro de um
    subtema, como "Produto > Instalações". A menção conta nesse subtema, com a
    polaridade, a evidência e os trechos do emergente; se o áudio já citava o
    subtema, as polaridades se juntam e os trechos se somam. A regra vale na
    leitura, e as classificações gravadas não voltam à IA. `subtemas` vai da
    chave (categoria, nome) ao rótulo da taxonomia.
    """
    temas = [dict(tema) for tema in _lista(classificacao.get("temas"))]
    por_rotulo = {tema.get("subtema"): tema for tema in temas}
    emergentes = []
    for emergente in _lista(classificacao.get("emergentes")):
        categoria, _, nome = str(emergente.get("categoria") or "").partition(">")
        rotulo = subtemas.get((chave_rotulo(categoria), chave_rotulo(nome)))
        if rotulo is None:
            emergentes.append(emergente)
            continue
        polaridade = _polaridade(emergente.get("polaridade"))
        tema = por_rotulo.get(rotulo)
        if tema is None:
            por_rotulo[rotulo] = {
                "subtema": rotulo,
                "polaridade": polaridade,
                "evidencia": emergente.get("evidencia") or "",
                "trechos": _lista(emergente.get("trechos")),
            }
            temas.append(por_rotulo[rotulo])
            continue
        citadas = {_inteiro(trecho.get("linha")) for trecho in _lista(tema.get("trechos"))}
        tema["polaridade"] = juntar_polaridades(_polaridade(tema.get("polaridade")), polaridade)
        tema["evidencia"] = tema.get("evidencia") or emergente.get("evidencia") or ""
        tema["trechos"] = _lista(tema.get("trechos")) + [
            trecho for trecho in _lista(emergente.get("trechos")) if _inteiro(trecho.get("linha")) not in citadas
        ]
    return dict(classificacao, temas=temas, emergentes=emergentes)


def _nomes_curtos(taxonomia: Taxonomia) -> Dict[str, str]:
    """Só o subtema, como na Matriz; o rótulo inteiro quando o nome se repete."""
    contagem = Counter(chave_rotulo(s.nome) for s in taxonomia.subtemas)
    return {
        s.rotulo: s.nome if contagem[chave_rotulo(s.nome)] == 1 else s.rotulo
        for s in taxonomia.subtemas
    }


# ---------------------------------------------------------------------------
# Faixas e polaridades
# ---------------------------------------------------------------------------

def faixa(respondentes: int, total: int) -> Optional[str]:
    """Faixa do subtema pelo número de respondentes que trataram dele."""
    if respondentes <= 0:
        return None
    if respondentes < _MIN_SINAL:
        return MENCAO_ISOLADA
    if respondentes >= _MIN_PADRAO and 100 * respondentes >= _PERCENTUAL_PADRAO * total:
        return PADRAO
    return SINAL


def predominante(polaridades: Dict[str, int]) -> str:
    """A polaridade com mais respondentes no tema: positivo, negativo ou misto.

    Empate no topo, ou mista e neutra no topo, é "misto ou neutro".
    """
    ordem = sorted(((n, p) for p, n in polaridades.items() if n > 0), reverse=True)
    if not ordem or (len(ordem) > 1 and ordem[0][0] == ordem[1][0]):
        return MISTO_OU_NEUTRO
    return {"positiva": POSITIVO, "negativa": NEGATIVO}.get(ordem[0][1], MISTO_OU_NEUTRO)


def texto_polaridades(polaridades: Dict[str, int]) -> str:
    """"17 neg · 2 mistas": da polaridade com mais respondentes à com menos."""
    itens = sorted(
        ((n, POLARIDADES.index(p), p) for p, n in polaridades.items() if n > 0 and p in POLARIDADES),
        key=lambda item: (-item[0], item[1]),
    )
    return " · ".join("{} {}".format(n, _ABREVIACOES[p][0 if n == 1 else 1]) for n, _, p in itens)


# ---------------------------------------------------------------------------
# Voz
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reguas:
    """Régua da voz do projeto inteiro, com ou sem filtro."""

    valencia: Optional[sinais.ReferenciaVoz] = None
    ativacao: Optional[sinais.ReferenciaVoz] = None


def _referencia(sinc: Optional[pd.DataFrame], coluna: str) -> Optional[sinais.ReferenciaVoz]:
    """A conta de referencia_valencia, para outra coluna do VAD."""
    if sinc is None or sinc.empty or coluna not in sinc.columns:
        return None
    valores = pd.to_numeric(sinc[coluna], errors="coerce")
    validas = valores.notna()
    if validas.sum() < sinais.MIN_LINHAS_POR_LOCUTOR:
        return None
    desvio = float(valores[validas].std())
    if not desvio > 0:
        return None
    audios = sinc.loc[validas, "session_id"].nunique() if "session_id" in sinc.columns else 1
    return sinais.ReferenciaVoz(float(valores[validas].mean()), desvio, int(validas.sum()), int(audios))


def reguas_do_projeto(sinc_projeto: Optional[pd.DataFrame]) -> Reguas:
    """A valência usa a régua do índice combinado (referencia_valencia), e a
    ativação, a mesma conta sobre dim_arousal: média e desvio de todas as
    linhas do VAD do projeto."""
    return Reguas(sinais.referencia_valencia(sinc_projeto), _referencia(sinc_projeto, "dim_arousal"))


@dataclass(frozen=True)
class LeituraTrecho:
    """Voz e nota do texto de um trecho classificado.

    `voz` é a metade de voz do índice combinado (z da valência em relação ao
    projeto, ÷ 2, limitado a ±1) e `ativacao`, o z de dim_arousal em relação
    ao projeto, os dois ponderados pela duração das linhas do VAD do trecho;
    `peso` é essa duração. None quando o trecho não tem dado de voz.
    """

    voz: Optional[float] = None
    ativacao: Optional[float] = None
    peso: float = 0.0
    nota_texto: Optional[float] = None


@dataclass(frozen=True)
class LeituraVoz:
    """A voz de um tema, como entra na tabela de taxonomia.

    `respondentes` são os que têm voz em algum trecho do tema, e `trechos`,
    os trechos com voz de todos eles. `instavel` marca a média que passou de
    um limiar, mas voltaria para dentro sem algum respondente: a direção ou a
    ativação ficou no habitual por isso.
    """

    respondentes: int
    valencia: Optional[float] = None
    ativacao: Optional[float] = None
    direcao: str = ""
    nivel: str = ""
    qualificador: str = ""
    trechos: int = 0
    instavel: bool = False

    @property
    def avaliavel(self) -> bool:
        return self.valencia is not None

    def texto(self) -> str:
        if not self.avaliavel:
            return "não avaliável ({} com voz)".format(
                _plural(self.respondentes, "respondente", "respondentes")
            )
        leitura = self.direcao + (", ativação " + self.nivel if self.nivel else "")
        return leitura + (" → " + self.qualificador if self.qualificador else "")


def _media_ponderada(pares: Iterable[Tuple[Optional[float], float]]) -> Optional[float]:
    pares = [(valor, peso) for valor, peso in pares if valor is not None]
    if not pares:
        return None
    total = sum(peso for _, peso in pares)
    if total <= 0:
        return sum(valor for valor, _ in pares) / len(pares)
    return sum(valor * peso for valor, peso in pares) / total


def _media_por_respondente(medias: Iterable[Optional[float]]) -> Optional[float]:
    validas = [media for media in medias if media is not None]
    return sum(validas) / len(validas) if validas else None


def _lado(valor: float, limiar: float) -> int:
    # Arredondar antes de comparar: média de 0,15 não pode cair abaixo do
    # limiar por resto de ponto flutuante.
    valor = round(valor, 6)
    return 1 if valor >= limiar else -1 if valor <= -limiar else 0


def _lado_estavel(medias: Sequence[float], limiar: float) -> Tuple[int, bool]:
    """(lado do limiar, instável): 1 acima, -1 abaixo, 0 dentro.

    O lado só vale se a média sem cada um dos respondentes fica do mesmo
    lado. Senão a leitura é decidida por uma pessoa, ou por milésimos em
    volta do limiar, e fica dentro, marcada como instável.
    """
    lado = _lado(sum(medias) / len(medias), limiar)
    if not lado:
        return 0, False
    total = sum(medias)
    for media in medias:
        if len(medias) < 2 or _lado((total - media) / (len(medias) - 1), limiar) != lado:
            return 0, True
    return lado, False


def ler_voz(leituras: Iterable[Tuple[str, LeituraTrecho]], polaridade_predominante: str) -> LeituraVoz:
    """Leitura da voz de um tema, com o mesmo peso para cada respondente.

    `leituras` são pares (respondente, leitura do trecho). Cada respondente
    entra com a média dos trechos dele que têm voz, ponderada pela duração, e
    a leitura é a média entre respondentes, como no Termômetro: quem fala
    muito sobre o tema não decide a leitura sozinho. Valência a partir de
    +0,15 é "acima do habitual" e até -0,15, "abaixo"; ativação a partir de
    +0,3 desvio é "alta" e até -0,3, "baixa". Passar do limiar só vale se a
    média sem cada respondente também passa; senão fica no habitual (ou
    média). O qualificador compara a direção da voz com a polaridade
    predominante do texto no tema: voz no habitual só "atenua" com ativação
    alta ou baixa, e tema misto ou neutro leva só a direção. Com menos de 5
    respondentes com voz, não avaliável.
    """
    por_respondente: Dict[str, List[LeituraTrecho]] = {}
    for respondente, leitura in leituras:
        if leitura.voz is not None:
            por_respondente.setdefault(respondente, []).append(leitura)
    trechos = sum(len(lista) for lista in por_respondente.values())
    if len(por_respondente) < MIN_RESPONDENTES_VOZ:
        return LeituraVoz(len(por_respondente), trechos=trechos)
    valencias = [
        media
        for media in (_media_ponderada((l.voz, l.peso) for l in lista) for lista in por_respondente.values())
        if media is not None
    ]
    ativacoes = [
        media
        for media in (_media_ponderada((l.ativacao, l.peso) for l in lista) for lista in por_respondente.values())
        if media is not None
    ]
    valencia = _media_por_respondente(valencias)
    ativacao = _media_por_respondente(ativacoes)
    lado, instavel = _lado_estavel(valencias, LIMIAR_VALENCIA)
    direcao = {1: ACIMA, -1: ABAIXO}.get(lado, NO_HABITUAL)
    nivel = ""
    if ativacoes:
        lado_ativacao, ativacao_instavel = _lado_estavel(ativacoes, LIMIAR_ATIVACAO)
        nivel = {1: "alta", -1: "baixa"}.get(lado_ativacao, "média")
        instavel = instavel or ativacao_instavel
    qualificador = _QUALIFICADORES.get(polaridade_predominante, {}).get(direcao, "")
    if direcao == NO_HABITUAL and nivel not in _NIVEIS_QUE_ATENUAM:
        qualificador = ""
    return LeituraVoz(len(por_respondente), valencia, ativacao, direcao, nivel, qualificador, trechos, instavel)


def _por_sessao(df: Optional[pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    if df is None or df.empty or "session_id" not in df.columns:
        return {}
    return {str(sessao): bloco for sessao, bloco in df.groupby(df["session_id"].astype(str), sort=False)}


def _vad_do_audio(bloco: pd.DataFrame) -> pd.DataFrame:
    """As colunas do Sincronizado de um áudio que a leitura dos trechos usa."""

    def numerica(nome: str) -> pd.Series:
        if nome not in bloco.columns:
            return pd.Series(float("nan"), index=bloco.index, dtype=float)
        return pd.to_numeric(bloco[nome], errors="coerce")

    inicio = numerica("start_s")
    inicio = inicio.where(inicio.notna(), numerica("seconds"))
    return pd.DataFrame(
        {
            "inicio": inicio,
            "segmento": numerica("segmento_idx"),
            "valencia": numerica("dim_valence"),
            "ativacao": numerica("dim_arousal"),
            "nota": numerica("sentimento_texto"),
            # O mesmo peso do índice combinado: a duração de cada linha do VAD.
            "peso": sinais._peso_duracao(bloco),
        },
        index=bloco.index,
    )


def _ler(selecao: Optional[pd.DataFrame], reguas: Reguas, nota: Optional[float]) -> LeituraTrecho:
    if selecao is None or selecao.empty:
        return LeituraTrecho(nota_texto=nota)
    if nota is None:
        notas = selecao["nota"].dropna()
        nota = float(notas.iloc[0]) if not notas.empty else None
    voz, peso = None, 0.0
    com_valencia = selecao[selecao["valencia"].notna()]
    if reguas.valencia is not None and not com_valencia.empty:
        regua = reguas.valencia
        z = (com_valencia["valencia"] - regua.media) / regua.desvio / sinais.Z_EXTREMO_VOZ
        peso = float(com_valencia["peso"].sum())
        voz = float((z.clip(-1.0, 1.0) * com_valencia["peso"]).sum() / peso)
    ativacao = None
    com_ativacao = selecao[selecao["ativacao"].notna()]
    if reguas.ativacao is not None and not com_ativacao.empty:
        regua = reguas.ativacao
        z = (com_ativacao["ativacao"] - regua.media) / regua.desvio
        ativacao = float((z * com_ativacao["peso"]).sum() / com_ativacao["peso"].sum())
    return LeituraTrecho(voz, ativacao, peso, nota)


def _ler_trechos(
    vad: Optional[pd.DataFrame],
    linhas: Sequence[Linha],
    trechos: Iterable[dict],
    reguas: Reguas,
    notas_por_segmento: Dict[int, float],
) -> Dict[int, LeituraTrecho]:
    """Leitura de cada trecho citado na classificação de um áudio, pela linha.

    O trecho é casado com as linhas do VAD pelo segmento do Whisper. Arquivo
    sem segmento_idx (os antigos e os de upload manual) casa pelo tempo: as
    linhas do VAD que começam entre o início da fala e o início da fala
    seguinte. Sem tempo, o trecho fica sem voz: entra na contagem, não na
    leitura da voz.
    """
    inicio_por_numero = {linha.numero: linha.inicio_s for linha in linhas}
    tem_segmento = vad is not None and not vad.empty and bool(vad["segmento"].notna().any())
    leituras: Dict[int, LeituraTrecho] = {}
    for trecho in trechos:
        numero = _inteiro(trecho.get("linha"))
        if numero is None or numero in leituras:
            continue
        segmento = _inteiro(trecho.get("segmento_idx"))
        selecao = None
        if vad is not None and not vad.empty:
            if segmento is not None and tem_segmento:
                selecao = vad[vad["segmento"] == segmento]
            else:
                inicio = _numero(trecho.get("inicio_s"))
                fim = _numero(trecho.get("fim_s"))
                if fim is None:
                    fim = inicio_por_numero.get(numero + 1)
                if inicio is not None:
                    mascara = vad["inicio"] >= inicio - _TOLERANCIA_TEMPO
                    if fim is not None:
                        mascara &= vad["inicio"] < fim - _TOLERANCIA_TEMPO
                    selecao = vad[mascara]
        nota = notas_por_segmento.get(segmento) if segmento is not None else None
        leituras[numero] = _ler(selecao, reguas, nota)
    return leituras


def _notas_por_segmento(transcricao: Optional[pd.DataFrame]) -> Dict[str, Dict[int, float]]:
    """Nota do texto de cada segmento do Whisper, por sessão, da transcrição."""
    obrigatorias = {"session_id", "segmento_idx", "sentimento_texto"}
    if transcricao is None or transcricao.empty or not obrigatorias.issubset(transcricao.columns):
        return {}
    segmento = pd.to_numeric(transcricao["segmento_idx"], errors="coerce")
    nota = pd.to_numeric(transcricao["sentimento_texto"], errors="coerce")
    validas = segmento.notna() & nota.notna()
    notas: Dict[str, Dict[int, float]] = {}
    for sessao, indice, valor in zip(
        transcricao.loc[validas, "session_id"].astype(str), segmento[validas], nota[validas]
    ):
        notas.setdefault(sessao, {}).setdefault(int(indice), float(valor))
    return notas


def _divergencias(sinc: Optional[pd.DataFrame], sessoes: Set[str]) -> Set[Tuple[str, int]]:
    """(sessão, segmento) dos trechos na tabela de divergências voz × texto."""
    if sinc is None or sinc.empty or "session_id" not in sinc.columns:
        return set()
    tabela = sinais.detectar_divergencias(sinc[sinc["session_id"].astype(str).isin(sessoes)])
    return {(str(s), int(i)) for s, i in zip(tabela["session_id"], tabela["segmento_idx"])}


# ---------------------------------------------------------------------------
# Contagem por respondente
# ---------------------------------------------------------------------------

@dataclass
class _Mencoes:
    """O que um respondente disse, juntando os áudios dele."""

    temas: Dict[str, str] = field(default_factory=dict)
    # chave do rótulo -> (rótulo, categoria, polaridade)
    emergentes: Dict[str, Tuple[str, str, str]] = field(default_factory=dict)


def _mencoes_por_respondente(
    audios: Iterable[AudioDoProjeto], tipo: str, rotulos: Set[str]
) -> Dict[str, _Mencoes]:
    """Subtemas e emergentes de cada respondente; várias menções contam uma vez.

    Respondente sem tema nenhum também entra: ele falou, então está no N.
    """
    mencoes: Dict[str, _Mencoes] = {}
    for audio in audios:
        respondente = mencoes.setdefault(chave_respondente(audio.session_id, tipo), _Mencoes())
        classificacao = audio.classificacao or {}
        for tema in _lista(classificacao.get("temas")):
            rotulo = tema.get("subtema")
            if rotulo not in rotulos:
                continue
            polaridade = _polaridade(tema.get("polaridade"))
            anterior = respondente.temas.get(rotulo)
            respondente.temas[rotulo] = juntar_polaridades(anterior, polaridade) if anterior else polaridade
        for emergente in _lista(classificacao.get("emergentes")):
            rotulo = " ".join(str(emergente.get("rotulo") or "").split())
            chave = chave_rotulo(rotulo)
            if not chave:
                continue
            categoria = " ".join(str(emergente.get("categoria") or "").split())
            polaridade = _polaridade(emergente.get("polaridade"))
            if chave in respondente.emergentes:
                antigo = respondente.emergentes[chave]
                respondente.emergentes[chave] = (
                    antigo[0], antigo[1] or categoria, juntar_polaridades(antigo[2], polaridade)
                )
            else:
                respondente.emergentes[chave] = (rotulo, categoria, polaridade)
    return mencoes


@dataclass
class LinhaTema:
    """Uma linha da tabela de taxonomia."""

    subtema: str
    nome: str
    respondentes: int
    faixa: str
    polaridades: Dict[str, int]
    voz: LeituraVoz
    anteriores: Optional[int] = None
    novos: Optional[int] = None

    @property
    def predominante(self) -> str:
        return predominante(self.polaridades)


@dataclass
class Emergente:
    rotulo: str
    categoria: str
    respondentes: int
    polaridades: Dict[str, int]


def _emergentes(mencoes: Dict[str, _Mencoes]) -> List[Emergente]:
    """Emergentes por respondente; a grafia e a categoria são as da primeira vez."""
    grafia: Dict[str, str] = {}
    categoria: Dict[str, str] = {}
    contagem: Dict[str, Counter] = {}
    for respondente in mencoes.values():
        for chave, (rotulo, cat, polaridade) in respondente.emergentes.items():
            grafia.setdefault(chave, rotulo)
            if cat:
                categoria.setdefault(chave, cat)
            contagem.setdefault(chave, Counter())[polaridade] += 1
    lista = [
        Emergente(grafia[chave], categoria.get(chave, ""), sum(c.values()), dict(c))
        for chave, c in contagem.items()
    ]
    return sorted(lista, key=lambda e: (-e.respondentes, chave_rotulo(e.rotulo)))


# ---------------------------------------------------------------------------
# Trechos de evidência para a IA
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Trecho:
    """Uma fala classificada num subtema, citada pelo alias do áudio."""

    audio_id: int
    alias: str
    linha: int
    inicio_s: Optional[float]
    fala: str
    polaridade: str
    nota_texto: Optional[float] = None
    divergente: bool = False


def _forca(trecho: Trecho) -> float:
    return abs(trecho.nota_texto) if trecho.nota_texto is not None else -1.0


def _escolher_trechos(ocorrencias: Iterable[Tuple[str, Trecho, LeituraTrecho]]) -> List[Trecho]:
    """Até 6 trechos do tema, um por respondente, os de nota do texto mais forte."""
    melhor: Dict[str, Trecho] = {}
    for respondente, trecho, _ in ocorrencias:
        atual = melhor.get(respondente)
        if atual is None or _forca(trecho) > _forca(atual):
            melhor[respondente] = trecho
    escolhidos = sorted(melhor.values(), key=lambda t: (-_forca(t), t.alias, t.linha))
    return escolhidos[:MAX_TRECHOS_POR_TEMA]


def _ocorrencias(
    contados: Sequence[AudioDoProjeto],
    tipo: str,
    nomes: Dict[int, str],
    rotulos: Set[str],
    sinc_projeto: Optional[pd.DataFrame],
    transcricao_projeto: Optional[pd.DataFrame],
    reguas: Reguas,
) -> Dict[str, List[Tuple[str, Trecho, LeituraTrecho]]]:
    """Cada trecho classificado, por subtema: respondente, fala e leitura de voz."""
    vad_por_sessao = _por_sessao(sinc_projeto)
    notas_por_sessao = _notas_por_segmento(transcricao_projeto)
    divergentes = _divergencias(sinc_projeto, {str(a.session_id) for a in contados})
    ocorrencias: Dict[str, List[Tuple[str, Trecho, LeituraTrecho]]] = {}
    for audio in contados:
        sessao = str(audio.session_id)
        temas = [t for t in _lista((audio.classificacao or {}).get("temas")) if t.get("subtema") in rotulos]
        bloco = vad_por_sessao.get(sessao)
        leituras = _ler_trechos(
            _vad_do_audio(bloco) if bloco is not None else None,
            audio.linhas,
            (trecho for tema in temas for trecho in _lista(tema.get("trechos"))),
            reguas,
            notas_por_sessao.get(sessao, {}),
        )
        por_numero = {linha.numero: linha for linha in audio.linhas}
        respondente = chave_respondente(audio.session_id, tipo)
        for tema in temas:
            polaridade = _polaridade(tema.get("polaridade"))
            for trecho in _lista(tema.get("trechos")):
                numero = _inteiro(trecho.get("linha"))
                linha = por_numero.get(numero)
                if linha is None:
                    continue
                leitura = leituras.get(numero, LeituraTrecho())
                segmento = _inteiro(trecho.get("segmento_idx"))
                ocorrencias.setdefault(tema["subtema"], []).append((
                    respondente,
                    Trecho(
                        audio.audio_id,
                        nomes[audio.audio_id],
                        numero,
                        linha.inicio_s,
                        linha.texto,
                        polaridade,
                        leitura.nota_texto,
                        segmento is not None and (sessao, segmento) in divergentes,
                    ),
                    leitura,
                ))
    return ocorrencias


# ---------------------------------------------------------------------------
# Termômetro
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Termometro:
    respondentes: int = 0
    media: Optional[float] = None
    nota: Optional[int] = None
    positivos: int = 0
    neutros: int = 0
    negativos: int = 0

    @property
    def avaliavel(self) -> bool:
        return self.nota is not None


def calcular_termometro(medias: Iterable[Optional[float]]) -> Termometro:
    """Termômetro a partir da média do texto de cada respondente.

    Cada respondente pesa igual: a média entre eles, com duas casas, × 5,
    arredondada (meio para longe do zero) e limitada a ±5. A conta parte da
    média já com duas casas, para fechar com o que o relatório mostra.
    Respondente com média a partir de +0,2 é positivo, e até -0,2, negativo.
    """
    medias = [m for m in medias if m is not None]
    if not medias:
        return Termometro()
    media = round(sum(medias) / len(medias), 2)
    bruto = round(media * ESCALA_TERMOMETRO, 2)
    nota = int(math.copysign(math.floor(abs(bruto) + 0.5), bruto))
    nota = max(-ESCALA_TERMOMETRO, min(ESCALA_TERMOMETRO, nota))
    positivos = sum(1 for m in medias if m >= LIMIAR_TEXTO)
    negativos = sum(1 for m in medias if m <= -LIMIAR_TEXTO)
    return Termometro(len(medias), media, nota, positivos, len(medias) - positivos - negativos, negativos)


def _medias_do_texto(
    transcricao: Optional[pd.DataFrame],
    sinc: Optional[pd.DataFrame],
    contados: Sequence[AudioDoProjeto],
    tipo: str,
) -> List[float]:
    """Média do texto de cada respondente, ponderada pela duração dos trechos.

    A fonte é a da Análise Geral: a transcrição quando ela tem nota, senão o
    Sincronizado. Na entrevista, entram só as falas do entrevistado que a
    classificação apontou; quando ela não soube apontar, o áudio inteiro.
    """
    fonte = transcricao if sinais.tem_sentimento_texto(transcricao) else sinc
    if not sinais.tem_sentimento_texto(fonte) or "session_id" not in fonte.columns:
        return []
    respondente_da_sessao = {str(a.session_id): chave_respondente(a.session_id, tipo) for a in contados}
    linhas = fonte[fonte["session_id"].astype(str).isin(respondente_da_sessao)].copy()
    if linhas.empty:
        return []
    if tipo == ENTREVISTA_QUALITATIVA and "SpeakerName" in linhas.columns:
        falante = {
            str(a.session_id): chave_rotulo((a.classificacao or {}).get("respondente") or "")
            for a in contados
        }
        alvo = linhas["session_id"].astype(str).map(falante).fillna("")
        locutor = linhas["SpeakerName"].map(lambda s: chave_rotulo(s) if isinstance(s, str) else "")
        linhas = linhas[(alvo == "") | (locutor == alvo)].copy()
    linhas["_respondente"] = linhas["session_id"].astype(str).map(respondente_da_sessao)
    tabela = sinais.sentimento_por_grupo(linhas, "_respondente")
    return [float(m) for m in tabela["media"]]


# ---------------------------------------------------------------------------
# Matriz de Destaques
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LinhaMatriz:
    audio_id: int
    alias: str
    texto: float
    voz: float
    indice: float
    ativacao: Optional[float]
    temas: Tuple[str, ...]


@dataclass(frozen=True)
class GrupoMatriz:
    linhas: Tuple[LinhaMatriz, ...]
    # Áudios do recorte no grupo; a tabela mostra até 15.
    total: int
    temas_mais_citados: Tuple[Tuple[str, int], ...]
    # A voz de todos os áudios do grupo, com os limiares da voz por tema. A IA
    # descreve a voz do grupo por aqui: na validação com a Smart Fit, ela
    # disse "voz raramente acima do habitual" num grupo com 8 de 15 acima.
    voz_media: Optional[float] = None
    voz_acima: int = 0
    voz_abaixo: int = 0


@dataclass(frozen=True)
class Matriz:
    audios_com_indice: int
    corte_indice: Optional[float] = None
    corte_ativacao: Optional[float] = None
    vinculo: Optional[GrupoMatriz] = None
    risco: Optional[GrupoMatriz] = None

    @property
    def avaliavel(self) -> bool:
        return self.audios_com_indice >= MIN_AUDIOS_MATRIZ


def _ativacao_por_audio(sinc: Optional[pd.DataFrame]) -> Dict[str, float]:
    """Média simples de dim_arousal por sessão, a mesma das médias do apêndice."""
    if sinc is None or sinc.empty or not {"session_id", "dim_arousal"}.issubset(sinc.columns):
        return {}
    medias = pd.to_numeric(sinc["dim_arousal"], errors="coerce").groupby(sinc["session_id"].astype(str)).mean()
    return {str(sessao): float(valor) for sessao, valor in medias.dropna().items()}


def _grupo(linhas: List[LinhaMatriz]) -> GrupoMatriz:
    contagem = Counter(tema for linha in linhas for tema in set(linha.temas))
    citados = sorted(
        ((tema, n) for tema, n in contagem.items() if n >= _MIN_AUDIOS_TEMA_NO_GRUPO),
        key=lambda item: (-item[1], chave_rotulo(item[0])),
    )
    vozes = [linha.voz for linha in linhas]
    return GrupoMatriz(
        tuple(linhas[:_MAX_LINHAS_MATRIZ]),
        len(linhas),
        tuple(citados[:_MAX_TEMAS_NO_GRUPO]),
        sum(vozes) / len(vozes) if vozes else None,
        sum(1 for voz in vozes if _lado(voz, LIMIAR_VALENCIA) > 0),
        sum(1 for voz in vozes if _lado(voz, LIMIAR_VALENCIA) < 0),
    )


def montar_matriz(
    indice_projeto: pd.DataFrame,
    ativacao_projeto: Dict[str, float],
    audios_recorte: Sequence[AudioDoProjeto],
    nomes: Dict[int, str],
    temas_por_audio: Dict[int, Tuple[str, ...]],
) -> Matriz:
    """Matriz de Destaques, por áudio.

    `indice_projeto` é o índice combinado de cada áudio do projeto inteiro
    (indice_combinado_por_grupo por session_id) e `ativacao_projeto`, a
    ativação média de cada um: os terços saem do projeto, a mesma régua do
    índice, e a tabela lista só os áudios do recorte. Vínculo forte é índice
    no terço superior e texto acima de +0,2; risco de abandono silencioso é
    texto abaixo de -0,2 e ativação no terço inferior. Com menos de 10 áudios
    com índice no projeto, não avaliável.
    """
    com_indice = indice_projeto.dropna(subset=["indice"]) if not indice_projeto.empty else indice_projeto
    if len(com_indice) < MIN_AUDIOS_MATRIZ:
        return Matriz(len(com_indice))
    corte_indice = float(com_indice["indice"].quantile(2 / 3))
    ativacoes = pd.Series(list(ativacao_projeto.values()), dtype=float)
    corte_ativacao = float(ativacoes.quantile(1 / 3)) if not ativacoes.empty else None
    por_sessao = {str(linha.grupo): linha for linha in com_indice.itertuples(index=False)}

    vinculo: List[LinhaMatriz] = []
    risco: List[LinhaMatriz] = []
    for audio in audios_recorte:
        indice = por_sessao.get(str(audio.session_id))
        if indice is None:
            continue
        linha = LinhaMatriz(
            audio.audio_id,
            nomes[audio.audio_id],
            float(indice.texto),
            float(indice.voz),
            float(indice.indice),
            ativacao_projeto.get(str(audio.session_id)),
            temas_por_audio.get(audio.audio_id, ()),
        )
        if linha.indice >= corte_indice and linha.texto > LIMIAR_TEXTO:
            vinculo.append(linha)
        if (
            linha.texto < -LIMIAR_TEXTO
            and linha.ativacao is not None
            and corte_ativacao is not None
            and linha.ativacao <= corte_ativacao
        ):
            risco.append(linha)
    vinculo.sort(key=lambda l: (-l.indice, l.alias))
    risco.sort(key=lambda l: (l.texto, l.alias))
    return Matriz(len(com_indice), corte_indice, corte_ativacao, _grupo(vinculo), _grupo(risco))


# ---------------------------------------------------------------------------
# Comparação com a análise anterior (Δ)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Recorte:
    """O filtro da Análise Geral, como fica gravado no snapshot.

    QR code e origem ficam vazios quando não filtram (marcar todos é o mesmo
    que não filtrar). O período fica registrado, mas não entra na comparação
    do Δ: é ele que muda de uma onda para a outra.
    """

    qr: Tuple[str, ...] = ()
    origem: Tuple[str, ...] = ()
    periodo: Tuple[str, ...] = ()
    descricao: str = ""

    def mesmo_qr_e_origem(self, outro: "Recorte") -> bool:
        return set(self.qr) == set(outro.qr) and set(self.origem) == set(outro.origem)

    def como_dict(self) -> dict:
        return {
            "qr": sorted(self.qr),
            "origem": sorted(self.origem),
            "periodo": list(self.periodo),
            "descricao": self.descricao,
        }

    @classmethod
    def de_dict(cls, dados) -> "Recorte":
        dados = dados if isinstance(dados, dict) else {}

        def tupla(chave: str) -> Tuple[str, ...]:
            valor = dados.get(chave)
            return tuple(str(v) for v in valor) if isinstance(valor, (list, tuple)) else ()

        return cls(tupla("qr"), tupla("origem"), tupla("periodo"), str(dados.get("descricao") or ""))


@dataclass(frozen=True)
class AnaliseAnterior:
    """A análise com que a coluna "Anteriores → novos" compara."""

    criada_em: datetime
    audios_cobertos: FrozenSet[int]
    # Sem snapshot: a cobertura foi reconstruída pela data de criação dos áudios.
    reconstruida: bool = False
    analise_id: Optional[int] = None

    @property
    def data(self) -> str:
        return self.criada_em.strftime("%d/%m")


@dataclass(frozen=True)
class Delta:
    anterior: AnaliseAnterior
    # Respondentes dos áudios que a anterior cobriu e dos que chegaram depois.
    anteriores: int
    novos: int
    # Quem mandou áudio nos dois momentos conta nos dois grupos.
    em_ambos: int

    @property
    def exibir(self) -> bool:
        return self.novos >= MIN_NOVOS_DELTA

    @property
    def sustenta_tendencia(self) -> bool:
        return self.novos >= MIN_NOVOS_TENDENCIA


def _audios_novos(
    anterior: AnaliseAnterior, ids_recorte: Iterable[int], criados: Dict[int, Optional[datetime]]
) -> Set[int]:
    return {
        audio_id
        for audio_id in ids_recorte
        if audio_id not in anterior.audios_cobertos
        and criados.get(audio_id) is not None
        and criados[audio_id] > anterior.criada_em
    }


def escolher_analise_anterior(
    analises: Sequence[dict],
    recorte: Recorte,
    audios: Sequence[AudioDoProjeto],
    ids_recorte: Collection[int],
) -> Optional[AnaliseAnterior]:
    """A análise com que o Δ compara, ou None.

    `analises` vem de get_project_analyses, da mais recente para a mais
    antiga. Vale a última com o mesmo recorte de QR code e origem. A análise
    com snapshot traz a lista de áudios que cobriu. A sem snapshot (todas as
    anteriores à v2) cobriu os áudios criados antes dela, desde que o texto
    não traga "Recorte analisado"; se trouxer, não há como saber o que ela
    cobriu, e o Δ espera a primeira análise com snapshot.

    Análise que já cobriu todos os áudios do recorte de agora é uma
    regeneração da mesma onda e é pulada: sem isso, regenerar sem áudio novo
    faria a coluna sumir e mudaria a tabela.
    """
    criados = {audio.audio_id: audio.criado_em for audio in audios}
    for analise in analises:
        criada_em = ler_data(analise.get("created_at"))
        if criada_em is None:
            continue
        snapshot = analise.get("temas")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("audios"), list):
            if not recorte.mesmo_qr_e_origem(Recorte.de_dict(snapshot.get("recorte"))):
                continue
            cobertos = frozenset(i for i in (_inteiro(v) for v in snapshot["audios"]) if i is not None)
            candidata = AnaliseAnterior(criada_em, cobertos, False, analise.get("id"))
        else:
            if _MARCA_RECORTE in str(analise.get("analysis_text") or ""):
                return None
            if recorte.qr or recorte.origem:
                continue
            cobertos = frozenset(
                audio_id for audio_id, criado in criados.items() if criado is not None and criado < criada_em
            )
            candidata = AnaliseAnterior(criada_em, cobertos, True, analise.get("id"))
        if _audios_novos(candidata, ids_recorte, criados):
            return candidata
    return None


# ---------------------------------------------------------------------------
# Agregação
# ---------------------------------------------------------------------------

@dataclass
class Agregacao:
    """Tudo o que o código calcula para o relatório, sobre os áudios do recorte."""

    tipo_projeto: str
    aliases: Dict[int, str]
    recorte: Tuple[AudioDoProjeto, ...]
    # Áudios com fala e classificação válida: são eles que a contagem usa.
    contados: Tuple[int, ...]
    sem_fala: Tuple[int, ...]
    sem_classificacao: Tuple[int, ...]
    respondente_incerto: Tuple[int, ...]
    n_respondentes: int
    temas: List[LinhaTema]
    sem_mencao: List[str]
    emergentes: List[Emergente]
    trechos: Dict[str, List[Trecho]]
    termometro: Termometro
    matriz: Matriz
    reguas: Reguas
    delta: Optional[Delta] = None
    # Cópia → o áudio que conta por ela: o mesmo arquivo enviado de novo.
    repetidos: Dict[int, int] = field(default_factory=dict)

    @property
    def exploratoria(self) -> bool:
        return self.n_respondentes < MIN_RESPONDENTES


def agregar(
    audios: Sequence[AudioDoProjeto],
    ids_recorte: Iterable[int],
    taxonomia: Taxonomia,
    tipo_projeto: Optional[str],
    sinc_projeto: Optional[pd.DataFrame] = None,
    transcricao_projeto: Optional[pd.DataFrame] = None,
    anterior: Optional[AnaliseAnterior] = None,
) -> Agregacao:
    """A agregação da Análise Geral sobre os áudios do recorte.

    `audios` são todos os áudios do projeto, e `ids_recorte`, os do filtro: os
    aliases, a régua da voz e os terços da Matriz usam o projeto inteiro, e
    a contagem, só o recorte. Os dataframes são o Sincronizado normalizado e
    a transcrição de todos os áudios do projeto, com session_id. `anterior` é
    a análise que escolher_analise_anterior devolveu. Áudios do recorte com o
    mesmo conteúdo contam uma vez, pelo primeiro (_repetidos): as cópias
    ficam fora da contagem, da voz, do Termômetro e da Matriz.
    """
    tipo = normalize_project_type(tipo_projeto)
    nomes = aliases(audios)
    ids = set(ids_recorte)
    recorte = tuple(sorted((a for a in audios if a.audio_id in ids), key=lambda a: a.audio_id))
    sem_fala = tuple(a.audio_id for a in recorte if not a.linhas)
    repetidos = _repetidos(a for a in recorte if a.linhas)
    sem_classificacao = tuple(
        a.audio_id for a in recorte if a.linhas and a.classificacao is None and a.audio_id not in repetidos
    )
    subtemas = {(chave_rotulo(s.categoria), chave_rotulo(s.nome)): s.rotulo for s in taxonomia.subtemas}
    contados = [
        replace(a, classificacao=_devolver_emergentes(a.classificacao, subtemas))
        for a in recorte
        if a.linhas and a.classificacao is not None and a.audio_id not in repetidos
    ]
    incertos = tuple(a.audio_id for a in contados if a.classificacao.get("respondente_incerto"))

    rotulos = [subtema.rotulo for subtema in taxonomia.subtemas]
    conjunto = set(rotulos)
    curtos = _nomes_curtos(taxonomia)
    mencoes = _mencoes_por_respondente(contados, tipo, conjunto)
    total = len(mencoes)
    reguas = reguas_do_projeto(sinc_projeto)
    ocorrencias = _ocorrencias(contados, tipo, nomes, conjunto, sinc_projeto, transcricao_projeto, reguas)

    temas: List[LinhaTema] = []
    for rotulo in rotulos:
        polaridades = Counter(m.temas[rotulo] for m in mencoes.values() if rotulo in m.temas)
        n = sum(polaridades.values())
        if not n:
            continue
        voz = ler_voz(
            ((respondente, leitura) for respondente, _, leitura in ocorrencias.get(rotulo, [])),
            predominante(polaridades),
        )
        temas.append(LinhaTema(rotulo, curtos[rotulo], n, faixa(n, total), dict(polaridades), voz))
    ordem = {rotulo: i for i, rotulo in enumerate(rotulos)}
    temas.sort(key=lambda t: (-t.respondentes, ordem[t.subtema]))
    citados = {tema.subtema for tema in temas}

    delta = None
    if anterior is not None:
        criados = {a.audio_id: a.criado_em for a in audios}
        novos_ids = _audios_novos(anterior, ids, criados)
        mencoes_ant = _mencoes_por_respondente(
            [a for a in contados if a.audio_id in anterior.audios_cobertos], tipo, conjunto
        )
        mencoes_nov = _mencoes_por_respondente([a for a in contados if a.audio_id in novos_ids], tipo, conjunto)
        for tema in temas:
            tema.anteriores = sum(1 for m in mencoes_ant.values() if tema.subtema in m.temas)
            tema.novos = sum(1 for m in mencoes_nov.values() if tema.subtema in m.temas)
        delta = Delta(anterior, len(mencoes_ant), len(mencoes_nov), len(set(mencoes_ant) & set(mencoes_nov)))

    temas_por_audio = {}
    for audio in contados:
        citados_no_audio = {t.get("subtema") for t in _lista(audio.classificacao.get("temas"))}
        temas_por_audio[audio.audio_id] = tuple(curtos[r] for r in rotulos if r in citados_no_audio)
    matriz = montar_matriz(
        sinais.indice_combinado_por_grupo(sinc_projeto, reguas.valencia, "session_id"),
        _ativacao_por_audio(sinc_projeto),
        tuple(a for a in recorte if a.audio_id not in repetidos),
        nomes,
        temas_por_audio,
    )

    return Agregacao(
        tipo_projeto=tipo,
        aliases=nomes,
        recorte=recorte,
        contados=tuple(a.audio_id for a in contados),
        sem_fala=sem_fala,
        sem_classificacao=sem_classificacao,
        respondente_incerto=incertos,
        n_respondentes=total,
        temas=temas,
        sem_mencao=[rotulo for rotulo in rotulos if rotulo not in citados],
        emergentes=_emergentes(mencoes),
        trechos={
            tema.subtema: _escolher_trechos(ocorrencias.get(tema.subtema, []))
            for tema in temas
            if tema.faixa in (PADRAO, SINAL)
        },
        termometro=calcular_termometro(_medias_do_texto(transcricao_projeto, sinc_projeto, contados, tipo)),
        matriz=matriz,
        reguas=reguas,
        delta=delta,
        repetidos=repetidos,
    )


def snapshot(agregacao: Agregacao, recorte: Recorte) -> dict:
    """O que a análise salva grava em project_analyses.temas_json.

    `audios` são todos os áudios do recorte, contados ou não: a análise
    seguinte trata como novos só os que chegaram depois e ficaram de fora.
    """
    return {
        "versao": 1,
        "recorte": recorte.como_dict(),
        "audios": [audio.audio_id for audio in agregacao.recorte],
        "respondentes": agregacao.n_respondentes,
        "temas": [
            {
                "subtema": tema.subtema,
                "respondentes": tema.respondentes,
                "faixa": tema.faixa,
                "polaridades": tema.polaridades,
            }
            for tema in agregacao.temas
        ],
        "emergentes": [
            {"rotulo": emergente.rotulo, "respondentes": emergente.respondentes}
            for emergente in agregacao.emergentes
        ],
        "termometro": agregacao.termometro.nota,
    }


# ---------------------------------------------------------------------------
# Blocos do relatório escritos pelo código
# ---------------------------------------------------------------------------

def cabecalho_markdown(
    agregacao: Agregacao,
    nome_projeto: str,
    recorte: Recorte,
    audios_no_projeto: int,
    taxonomia_status: Optional[str],
    taxonomia_da_ia: bool = False,
) -> str:
    """Cabeçalho: N, recorte, status da taxonomia e o aviso exploratório."""
    respondentes = _plural(agregacao.n_respondentes, "respondente", "respondentes")
    contagem = respondentes
    delta = agregacao.delta
    if delta is not None and delta.novos:
        contagem += " ({} da análise de {} e {}{})".format(
            delta.anteriores,
            delta.anterior.data,
            _plural(delta.novos, "novo", "novos"),
            ", {} nos dois grupos".format(delta.em_ambos) if delta.em_ambos else "",
        )
    status = TAXONOMIA_STATUS_LABELS.get(taxonomia_status or "", TAXONOMIA_STATUS_LABELS[TAXONOMIA_PROVISORIA])
    if taxonomia_da_ia and taxonomia_status != TAXONOMIA_VALIDADA:
        taxonomia = "taxonomia provisória, gerada pela IA — a validar com a cliente"
    else:
        taxonomia = "taxonomia " + status[0].lower() + status[1:]
    linhas = [" · ".join(["**{}**".format(nome_projeto.strip() or "Projeto"), contagem, taxonomia])]
    if recorte.descricao:
        linhas.append("Recorte: {} · {} de {} áudios do projeto.".format(
            recorte.descricao, len(agregacao.recorte), audios_no_projeto
        ))
    if agregacao.exploratoria:
        linhas.append(
            "**Leitura exploratória:** {}, abaixo de {}. Os números descrevem esta "
            "amostra e não se estendem ao público.".format(respondentes, MIN_RESPONDENTES)
        )
    return "\n\n".join(linhas)


def _fracao(n: int, total: int) -> str:
    return "{}/{}".format(n, total)


def emergentes_markdown(agregacao: Agregacao) -> str:
    """Linha dos temas emergentes, do mais citado ao menos."""
    if not agregacao.emergentes:
        return ""
    total = agregacao.n_respondentes
    itens = [
        "{}{}, {}, {}".format(
            e.rotulo,
            " ({})".format(e.categoria) if e.categoria else "",
            _fracao(e.respondentes, total),
            texto_polaridades(e.polaridades),
        )
        for e in agregacao.emergentes[:_MAX_EMERGENTES]
    ]
    resto = len(agregacao.emergentes) - _MAX_EMERGENTES
    texto = "Temas emergentes (fora da taxonomia): " + " · ".join(itens)
    if resto > 0:
        texto += " · e mais {}".format(_plural(resto, "tema", "temas"))
    return texto + "."


def tabela_temas_markdown(agregacao: Agregacao, incluir_emergentes: bool = True) -> str:
    """Tabela de taxonomia, emergentes, subtemas sem menção e a nota das faixas.

    Na mensagem da IA os emergentes vêm num bloco próprio, depois dos trechos
    (§9 da spec), e a tabela vai sem eles.
    """
    total = agregacao.n_respondentes
    if not total:
        return "Nenhum respondente com fala classificada no recorte."
    delta = agregacao.delta
    coluna_delta = delta is not None and delta.exibir
    blocos = []
    if agregacao.temas:
        cabecalho = ["Categoria > Subtema", "Respondentes", "Faixa", "Texto", "Voz nos trechos"]
        if coluna_delta:
            cabecalho.append("Anteriores → novos")
        linhas = []
        for tema in agregacao.temas:
            celulas = [
                tema.subtema,
                "{} ({}%)".format(_fracao(tema.respondentes, total), _percentual(tema.respondentes, total)),
                tema.faixa,
                texto_polaridades(tema.polaridades),
                "—" if tema.faixa == MENCAO_ISOLADA else tema.voz.texto(),
            ]
            if coluna_delta:
                celulas.append("{} → {}".format(
                    _fracao(tema.anteriores or 0, delta.anteriores), _fracao(tema.novos or 0, delta.novos)
                ))
            linhas.append(celulas)
        blocos.append(_tabela(cabecalho, linhas))
    else:
        blocos.append("Nenhum subtema da taxonomia foi citado no recorte.")
    if agregacao.emergentes and incluir_emergentes:
        blocos.append(emergentes_markdown(agregacao))
    if agregacao.sem_mencao:
        blocos.append("Subtemas da taxonomia sem menção no recorte: {}.".format("; ".join(agregacao.sem_mencao)))

    nota = (
        "_Faixas: 1–2 respondentes, menção isolada; 3–4, sinal; 5 ou mais e pelo menos 10% do "
        "total, padrão. O mesmo respondente pode estar em vários subtemas, então as contagens "
        "não se somam."
    )
    if coluna_delta:
        nota += " \"Anteriores → novos\" compara os respondentes da análise de {} com os que chegaram depois".format(
            delta.anterior.data
        )
        if not delta.sustenta_tendencia:
            nota += "; {} não sustentam tendência".format(_plural(delta.novos, "novo", "novos"))
        nota += "."
    elif delta is not None and delta.novos:
        nota += " Desde a análise de {} {}, menos que os {} que a comparação pede.".format(
            delta.anterior.data,
            "chegou 1 respondente novo" if delta.novos == 1 else "chegaram {} respondentes novos".format(delta.novos),
            MIN_NOVOS_DELTA,
        )
    blocos.append(nota + "_")
    return "\n\n".join(blocos)


def _tabela_grupo(grupo: Optional[GrupoMatriz], cabecalho: Sequence[str], celulas) -> str:
    if grupo is None or not grupo.total:
        return "Nenhum áudio do recorte neste grupo."
    partes = [_tabela(cabecalho, (celulas(linha) for linha in grupo.linhas))]
    if grupo.total > len(grupo.linhas):
        partes.append("Mais {} no grupo, fora da tabela.".format(
            _plural(grupo.total - len(grupo.linhas), "áudio", "áudios")
        ))
    if grupo.temas_mais_citados:
        partes.append("Temas mais citados no grupo: {}.".format(", ".join(
            "{} ({})".format(tema, _fracao(n, grupo.total)) for tema, n in grupo.temas_mais_citados
        )))
    if grupo.voz_media is not None:
        partes.append(
            "Voz no grupo: média {}; {} acima do habitual (a partir de {}) e {} abaixo (até {}).".format(
                _decimal(grupo.voz_media),
                _fracao(grupo.voz_acima, grupo.total),
                _decimal(LIMIAR_VALENCIA),
                _fracao(grupo.voz_abaixo, grupo.total),
                _decimal(-LIMIAR_VALENCIA),
            )
        )
    return "\n\n".join(partes)


VINCULO = "Vínculo forte"
RISCO = "Risco de abandono silencioso"


def _com_audios(grupo: Optional[GrupoMatriz]) -> bool:
    return grupo is not None and grupo.total > 0


def _grupos_da_matriz(agregacao: Agregacao) -> List[Tuple[str, str, bool]]:
    """(grupo, markdown, tem o que ler) de cada grupo da Matriz avaliável.

    O grupo sem áudio do recorte, ou sem dado de ativação no projeto, não
    pede leitura à IA.
    """
    matriz = agregacao.matriz
    grupos = [(
        VINCULO,
        "**{}** — índice combinado no terço superior do projeto (a partir de {}) e texto acima de +0,2"
        "\n\n{}".format(
            VINCULO,
            _decimal(matriz.corte_indice),
            _tabela_grupo(
                matriz.vinculo,
                ["Respondente", "Texto", "Voz", "Índice", "Temas"],
                lambda l: [l.alias, _decimal(l.texto), _decimal(l.voz), _decimal(l.indice), "; ".join(l.temas) or "—"],
            ),
        ),
        _com_audios(matriz.vinculo),
    )]
    if matriz.corte_ativacao is None:
        grupos.append((RISCO, "**{}** — não avaliável: o projeto não tem dado de ativação.".format(RISCO), False))
    else:
        grupos.append((
            RISCO,
            "**{}** — texto abaixo de -0,2 e ativação no terço inferior do projeto (até {})\n\n{}".format(
                RISCO,
                _decimal(matriz.corte_ativacao),
                _tabela_grupo(
                    matriz.risco,
                    ["Respondente", "Texto", "Ativação", "Temas"],
                    lambda l: [l.alias, _decimal(l.texto), _decimal(l.ativacao), "; ".join(l.temas) or "—"],
                ),
            ),
            _com_audios(matriz.risco),
        ))
    return grupos


def matriz_markdown(agregacao: Agregacao) -> str:
    """As duas tabelas da Matriz de Destaques, com a regra de cada grupo."""
    matriz = agregacao.matriz
    if not matriz.avaliavel:
        return "Matriz não avaliável: {} com índice combinado no projeto, menos que os {} necessários.".format(
            _plural(matriz.audios_com_indice, "áudio", "áudios"), MIN_AUDIOS_MATRIZ
        )
    return "\n\n".join(markdown for _, markdown, _ in _grupos_da_matriz(agregacao))


def termometro_markdown(agregacao: Agregacao) -> str:
    """O número do Termômetro e a conta inteira, à mostra."""
    t = agregacao.termometro
    if not t.avaliavel:
        return (
            "**Não avaliável:** nenhum respondente do recorte tem nota do sentimento do texto "
            "(os áudios anteriores ao sentimento do texto não têm)."
        )
    texto = (
        "**{}** (de -5 a +5): sentimento médio do texto, {} × 5 = {}, arredondado. "
        "Respondentes pela média do texto: {}, {} e {}.".format(
            "{:+d}".format(t.nota) if t.nota else "0",
            _decimal(t.media),
            _enxuto(round(t.media * ESCALA_TERMOMETRO, 2)),
            _plural(t.positivos, "positivo", "positivos"),
            _plural(t.neutros, "neutro", "neutros"),
            _plural(t.negativos, "negativo", "negativos"),
        )
    )
    if t.respondentes < agregacao.n_respondentes:
        texto += " {} dos {} respondentes têm nota do texto.".format(t.respondentes, agregacao.n_respondentes)
    return texto + " _Leitura do texto; a voz entra pelos qualificadores dos temas._"


def trechos_markdown(agregacao: Agregacao) -> str:
    """Trechos de evidência por subtema em padrão ou sinal, para a IA."""
    total = agregacao.n_respondentes
    blocos = []
    for tema in agregacao.temas:
        trechos = agregacao.trechos.get(tema.subtema)
        if not trechos:
            continue
        linhas = ["### {} · {}, {}".format(tema.subtema, tema.faixa.lower(), _fracao(tema.respondentes, total))]
        for t in trechos:
            local = t.alias + (", " + _tempo(t.inicio_s) if t.inicio_s is not None else "")
            detalhes = [t.polaridade]
            if t.nota_texto is not None:
                detalhes.append("texto " + _decimal(t.nota_texto))
            if t.divergente:
                detalhes.append("divergência voz × texto")
            linhas.append("- ({}) {}: \"{}\"".format(local, " · ".join(detalhes), _fala(t.fala)))
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos) or "Nenhum subtema em padrão ou sinal."


# ---------------------------------------------------------------------------
# Sumário Executivo das análises individuais
# ---------------------------------------------------------------------------

_TITULO_MARKDOWN = re.compile(r"^\s{0,3}(#{1,6})\s")
_ITEM_NUMERADO_NEGRITO = re.compile(r"^\s*\d+[.)]\s+\*\*")
_LINHA_NEGRITO = re.compile(r"^\s*\*\*[^*]+\*\*")
_REGUA = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_DEPOIS_DO_TITULO = re.compile(r"executivo", re.IGNORECASE)
_MAX_SUMARIO = 1200


def _tipo_de_titulo(linha: str) -> Optional[Tuple[str, int]]:
    markdown = _TITULO_MARKDOWN.match(linha)
    if markdown:
        return ("markdown", len(markdown.group(1)))
    if _ITEM_NUMERADO_NEGRITO.match(linha):
        return ("numerado", 0)
    if _LINHA_NEGRITO.match(linha):
        return ("negrito", 0)
    return None


def _encerra(linha: str, tipo: Tuple[str, int]) -> bool:
    """A linha começa a seção seguinte à do Sumário?"""
    if _REGUA.match(linha):
        return True
    outro = _tipo_de_titulo(linha)
    if outro is None:
        return False
    if outro[0] == "markdown":
        return tipo[0] != "markdown" or outro[1] <= tipo[1]
    if tipo[0] == "numerado":
        return outro[0] == "numerado"
    if tipo[0] == "negrito":
        return True
    return False


def sumario_executivo(texto: Optional[str]) -> Optional[str]:
    """O Sumário Executivo de uma análise individual salva, ou None.

    Vai do título "Sumário Executivo" até a seção seguinte. O título pode
    ser de markdown ("## 1. Sumário Executivo"), item numerado em negrito
    ("1. **Sumário Executivo** — texto") ou linha em negrito; o texto na
    mesma linha do título entra. Análise sem esse título (gerada com o
    prompt antigo) devolve None.
    """
    linhas = str(texto or "").splitlines()
    for i, linha in enumerate(linhas):
        tipo = _tipo_de_titulo(linha)
        if tipo is None or "sumario executivo" not in chave_rotulo(linha):
            continue
        corpo = []
        resto = _DEPOIS_DO_TITULO.split(linha, maxsplit=1)[-1].lstrip("*:—–- \t").strip()
        if resto:
            corpo.append(resto)
        for seguinte in linhas[i + 1:]:
            if _encerra(seguinte, tipo):
                break
            corpo.append(seguinte)
        sumario = "\n".join(corpo).strip()
        if len(sumario) > _MAX_SUMARIO:
            sumario = sumario[:_MAX_SUMARIO].rstrip() + "…"
        return sumario or None
    return None


def contar_sumarios(textos: Iterable[Optional[str]]) -> Tuple[int, int]:
    """(com Sumário Executivo, sem) entre as análises individuais existentes."""
    com = sem = 0
    for texto in textos:
        if not str(texto or "").strip():
            continue
        if sumario_executivo(texto):
            com += 1
        else:
            sem += 1
    return com, sem


# ---------------------------------------------------------------------------
# Apêndice técnico
# ---------------------------------------------------------------------------

# A mesma lista da Análise Geral e do gráfico de palavras do projeto.
_PALAVRAS_VAZIAS = frozenset({
    "a", "o", "as", "os", "de", "do", "da", "dos", "das", "e", "ou", "no", "na",
    "nos", "nas", "em", "para", "por", "com", "sem", "um", "uma", "uns", "umas",
    "que", "qual", "quais", "como", "onde", "quando", "se", "seu", "sua", "seus", "suas",
    "voce", "vocês", "voces", "ele", "ela", "eles", "elas", "isso", "isto", "aquele",
    "mas", "tambem", "mais", "muito", "entao", "aqui", "la", "sim", "nao", "pra", "pro",
    "este", "esta", "estes", "estas", "tudo", "todo", "toda", "todos", "todas", "ser",
    "ter", "ir", "ao", "aos",
    "pelo", "pela", "pelos", "pelas", "num", "numa", "neste", "nesta", "disso", "disto",
    "dele", "dela", "deles", "delas", "mim", "me", "te", "vos", "lhe", "lhes",
    "meu", "minha", "meus", "minhas", "teu", "tua", "teus", "tuas", "nosso", "nossa",
    "nossos", "nossas", "vosso", "vossa", "vossos", "vossas", "qualquer", "quaisquer",
    "algum", "alguma", "alguns", "algumas", "nenhum", "nenhuma", "outro", "outra", "outros",
    "outras", "mesmo", "mesma", "mesmos", "mesmas", "proprio", "propria", "proprios", "proprias",
    "acho", "acha", "achar", "coisa", "coisas", "dai", "tipo", "ne", "ta",
    "porque", "porquê", "pois", "assim", "sobre",
    "gente", "bem", "vou", "vai", "tao",
})


def ranking_palavras(textos: Iterable[str], top_n: int = 30) -> List[Tuple[str, int]]:
    """Palavras mais frequentes, sem acento e sem as palavras vazias."""
    contagem: Counter = Counter()
    for texto in textos:
        normalizado = "".join(
            c for c in unicodedata.normalize("NFD", str(texto or "").lower())
            if unicodedata.category(c) != "Mn"
        )
        contagem.update(p for p in re.findall(r"\b[a-z]{3,}\b", normalizado) if p not in _PALAVRAS_VAZIAS)
    return contagem.most_common(top_n)


_NUMERO_COM_PONTO = re.compile(r"(?<![\w.,])([+-]?\d+)\.(\d+)(?![\w.])")


def _virgula_decimal(texto: str) -> str:
    """Vírgula decimal nas tabelas que vêm de prosodia_signals, que usam ponto.

    Fala entre aspas fica como foi dita.
    """
    linhas = []
    for linha in texto.split("\n"):
        partes = linha.split('"')
        linhas.append('"'.join(
            _NUMERO_COM_PONTO.sub(r"\1,\2", parte) if i % 2 == 0 else parte
            for i, parte in enumerate(partes)
        ))
    return "\n".join(linhas)


def _recorte_com_alias(df: Optional[pd.DataFrame], sessoes: Dict[str, str]) -> pd.DataFrame:
    if df is None or df.empty or "session_id" not in df.columns:
        return pd.DataFrame()
    recorte = df[df["session_id"].astype(str).isin(sessoes)].copy()
    recorte["_alias"] = recorte["session_id"].astype(str).map(sessoes)
    return recorte


_TODOS = "Recorte inteiro"


def _com_linha_do_recorte(df: pd.DataFrame) -> pd.DataFrame:
    """Repete as linhas sob o rótulo "Recorte inteiro", que abre a tabela."""
    return pd.concat([df.assign(_alias=_TODOS), df], ignore_index=True)


def divergencias_markdown(
    agregacao: Agregacao, sinc_projeto: Optional[pd.DataFrame], so_respondente: bool = False
) -> str:
    """Divergências voz × texto dos áudios do recorte, pelo alias.

    Com `so_respondente`, a entrevista fica só com as falas do entrevistado
    que a classificação apontou: é assim que a tabela vai para a IA, que não
    pode tomar fala do entrevistador como evidência. O apêndice mostra todas.
    """
    sessoes = {str(a.session_id): agregacao.aliases[a.audio_id] for a in agregacao.recorte}
    sinc = _recorte_com_alias(sinc_projeto, sessoes)
    if not sinais.tem_sentimento_texto(sinc):
        return "O Sincronizado dos áudios do recorte não traz o sentimento do texto: não há como comparar voz e texto."
    divergencias = sinais.detectar_divergencias(sinc)
    if so_respondente and agregacao.tipo_projeto == ENTREVISTA_QUALITATIVA and not divergencias.empty:
        alvo = {
            str(a.session_id): chave_rotulo((a.classificacao or {}).get("respondente") or "")
            for a in agregacao.recorte
        }
        quem = divergencias["session_id"].astype(str).map(alvo).fillna("")
        locutor = divergencias["SpeakerName"].map(lambda s: chave_rotulo(s) if isinstance(s, str) else "")
        divergencias = divergencias[(quem == "") | (locutor == quem)]
    if not divergencias.empty:
        divergencias = divergencias.assign(session_id=divergencias["session_id"].astype(str).map(sessoes))
    return _virgula_decimal(sinais.divergencias_texto(divergencias, incluir_audio=True))


def legenda_markdown(agregacao: Agregacao) -> str:
    """Alias → sessão. A sessão traz o telefone: vai ao apêndice só se a
    cliente puder receber telefones; senão, fica só na tela."""
    return _tabela(
        ["Áudio", "Sessão", "QR code", "Chegada"],
        ([agregacao.aliases[a.audio_id], a.session_id, a.qr or "—", a.chegada or "—"] for a in agregacao.recorte),
    )


def _secao_voz_por_tema(agregacao: Agregacao) -> str:
    if not agregacao.temas:
        return "Nenhum subtema citado."
    return _tabela(
        ["Subtema", "Respondentes", "Com voz", "Trechos com voz", "Valência (voz)", "Ativação (z)", "Leitura"],
        (
            [
                tema.subtema,
                str(tema.respondentes),
                str(tema.voz.respondentes),
                str(tema.voz.trechos),
                _decimal(tema.voz.valencia) if tema.voz.valencia is not None else "—",
                _decimal(tema.voz.ativacao) if tema.voz.ativacao is not None else "—",
                tema.voz.texto() + (" (instável)" if tema.voz.instavel else ""),
            ]
            for tema in agregacao.temas
        ),
    )


def _secao_momentos(sinc: pd.DataFrame) -> str:
    momentos = sinais.selecionar_momentos_ativacao(sinc, top_n=15, min_altos=5) if not sinc.empty else pd.DataFrame()
    if momentos.empty:
        return "Nenhum momento de alta ativação no recorte."

    def valor(linha, coluna):
        numero = _numero(linha.get(coluna))
        return _decimal(numero) if numero is not None else "—"

    def texto(linha, coluna):
        valor_bruto = linha.get(coluna)
        return valor_bruto if isinstance(valor_bruto, str) else ""

    linhas = []
    for _, linha in momentos.iterrows():
        inicio = _numero(linha.get("start_s"))
        if inicio is None:
            inicio = _numero(linha.get("seconds"))
        linhas.append([
            texto(linha, "_alias"),
            _tempo(inicio) or "—",
            texto(linha, "SpeakerName") or "—",
            "\"{}\"".format(_fala(texto(linha, "Text"))),
            valor(linha, "dim_arousal"),
            valor(linha, "dim_valence"),
            valor(linha, "dim_dominance"),
        ])
    return _tabela(["Áudio", "Tempo", "Locutor", "Fala", "Ativação", "Valência", "Dominância"], linhas)


def _secao_indice(sinc: pd.DataFrame, regua: Optional[sinais.ReferenciaVoz], sessoes: Dict[str, str]) -> str:
    """Índice combinado por áudio, pela régua do projeto e com o alias no lugar da sessão."""
    tabela = sinais.indice_combinado_por_grupo(sinc, regua, "session_id") if not sinc.empty else pd.DataFrame()
    if tabela.empty:
        return "Sem índice combinado: falta nota do texto ou valência vocal nos mesmos trechos."
    tabela = tabela.assign(grupo=tabela["grupo"].astype(str).map(sessoes)).sort_values("grupo")

    def fatia(valor: float) -> str:
        return "{}%".format(math.floor(valor * 100 + 0.5))

    corpo = _tabela(
        ["Áudio", "Trechos", "Texto", "Voz", "Índice", "Positivo", "Neutro", "Negativo"],
        (
            [
                linha.grupo,
                str(linha.trechos),
                _decimal(linha.texto),
                _decimal(linha.voz),
                _decimal(linha.indice),
                fatia(linha.positivo),
                fatia(linha.neutro),
                fatia(linha.negativo),
            ]
            for linha in tabela.itertuples(index=False)
        ),
    )
    return corpo + (
        "\n\n_Índice = metade da nota do texto + metade da voz, de -1 a +1, ponderado pela duração. "
        "Voz = desvios-padrão da valência vocal em relação a {} do projeto, divididos por {:.0f} e "
        "limitados a ±1. Fatias em proporção do tempo; |índice| < 0,2 é neutro._\n\n{}".format(
            _plural(regua.audios, "áudio", "áudios"), sinais.Z_EXTREMO_VOZ, sinais.AVISO_INDICE
        )
    )


def _secao_perguntas(perguntas: Sequence[dict]) -> str:
    """As perguntas do roteiro, sem a fala de destaque que a tela mostra.

    O destaque é a resposta mais ativada à pergunta, e a de "Qual seu nome?"
    é o nome completo do entrevistado: o relatório vai para a cliente.
    """
    return _tabela(
        ["Pergunta", "Respostas cobertas", "Ativação média", "Variação de pitch", "Variação de volume"],
        (
            [
                p.get("question", ""),
                str(p.get("count", "")),
                _decimal(float(p.get("avg_arousal") or 0.0)),
                _decimal(float(p.get("avg_f0_var") or 0.0)),
                _decimal(float(p.get("avg_ld_var") or 0.0)),
            ]
            for p in perguntas
        ),
    )


def _limitacoes(
    agregacao: Agregacao, sumarios: Optional[Tuple[int, int]], sumarios_fora: int = 0
) -> List[str]:
    itens = []
    if agregacao.tipo_projeto == PESQUISA_OPINIAO:
        itens.append(
            "A unidade de contagem é o respondente: áudios do WhatsApp do mesmo número contam como uma "
            "pessoa, que trata do tema se qualquer áudio dela tratar."
        )
    else:
        itens.append(
            "A unidade de contagem é o respondente, e cada entrevista é um respondente. Só as falas do "
            "entrevistado contam como menção."
        )
    fora = []
    if agregacao.sem_fala:
        fora.append(_plural(len(agregacao.sem_fala), "áudio sem fala transcrita", "áudios sem fala transcrita"))
    if agregacao.sem_classificacao:
        fora.append(_plural(
            len(agregacao.sem_classificacao), "áudio sem classificação válida", "áudios sem classificação válida"
        ))
    if fora:
        itens.append("Ficaram fora da contagem de temas: {}.".format(" e ".join(fora)))
    if agregacao.repetidos:
        grupos: Dict[int, List[int]] = {}
        for copia, original in agregacao.repetidos.items():
            grupos.setdefault(original, []).append(copia)
        n = len(agregacao.repetidos)
        itens.append(
            "{} do recorte {} o mesmo arquivo de outro, enviado de novo (a análise de prosódia é idêntica), "
            "e {} fora da contagem. Em cada grupo conta só o primeiro: {}.".format(
                _plural(n, "áudio", "áudios"),
                "é" if n == 1 else "são",
                "ficou" if n == 1 else "ficaram",
                "; ".join(
                    " = ".join(agregacao.aliases[i] for i in [original] + sorted(copias))
                    for original, copias in sorted(grupos.items())
                ),
            )
        )
    if agregacao.respondente_incerto:
        itens.append(
            "Em {} não foi possível identificar o entrevistado, e todas as falas contaram.".format(
                _plural(len(agregacao.respondente_incerto), "entrevista", "entrevistas")
            )
        )
    valencia, ativacao = agregacao.reguas.valencia, agregacao.reguas.ativacao
    if valencia is None:
        itens.append("Voz por tema não avaliável: o projeto não tem valência vocal suficiente para a régua.")
    else:
        itens.append(
            "Voz por tema: média de cada respondente nos trechos do tema, ponderada pela duração, e depois "
            "a média entre os respondentes, com o mesmo peso para cada um. Valência é o z da valência "
            "em relação a {} linhas do VAD de {} do projeto, ÷ 2 e limitado a ±1 (a metade de voz do "
            "índice combinado): a partir de +0,15, acima do habitual; até -0,15, abaixo. Ativação é o z "
            "da ativação em relação às mesmas linhas{}: a partir de +0,3, alta; até -0,3, baixa. Passar do "
            "limiar só vale se a média sem cada um dos respondentes também passa; senão a leitura fica no "
            "habitual (ou média) e aparece como instável. Voz no habitual só atenua o texto com ativação "
            "alta ou baixa. Com menos de {} respondentes com voz no tema, não avaliável. Os limiares são "
            "iniciais.".format(
                valencia.linhas,
                _plural(valencia.audios, "áudio", "áudios"),
                "" if ativacao is not None else " (sem dado de ativação no projeto)",
                MIN_RESPONDENTES_VOZ,
            )
        )
    matriz = agregacao.matriz
    if matriz.avaliavel:
        itens.append(
            "Matriz: terços sobre os {} áudios do projeto com índice combinado, com ou sem filtro; a "
            "tabela lista só os do recorte e mostra até {} por grupo.".format(
                matriz.audios_com_indice, _MAX_LINHAS_MATRIZ
            )
        )
    else:
        itens.append("Matriz não avaliável: menos de {} áudios com índice no projeto.".format(MIN_AUDIOS_MATRIZ))
    termometro = agregacao.termometro
    if termometro.avaliavel:
        itens.append(
            "Termômetro: média de cada respondente, ponderada pela duração dos trechos, e depois a média "
            "entre os {}, com o mesmo peso para cada um. Mede o texto, não a voz.".format(
                _plural(termometro.respondentes, "respondente com nota", "respondentes com nota")
            )
        )
    delta = agregacao.delta
    if delta is None:
        itens.append("Sem análise anterior comparável: a coluna \"Anteriores → novos\" não aparece.")
    else:
        texto = "Comparação com a análise de {}: {} e {}.".format(
            delta.anterior.criada_em.strftime("%d/%m/%Y"),
            _plural(delta.anteriores, "respondente anterior", "respondentes anteriores"),
            _plural(delta.novos, "novo", "novos"),
        )
        if delta.anterior.reconstruida:
            texto += " Ela não guardou a lista de áudios; a lista foi reconstruída pela data de criação deles."
        if not delta.exibir:
            texto += " Com menos de {} novos, a coluna não aparece.".format(MIN_NOVOS_DELTA)
        elif not delta.sustenta_tendencia:
            texto += " Com menos de {} novos, a diferença é variação observada, não tendência.".format(
                MIN_NOVOS_TENDENCIA
            )
        itens.append(texto)
    if sumarios is not None and sumarios[1]:
        itens.append(
            "{} de {} análises individuais não têm Sumário Executivo (geradas com o prompt antigo) e ficaram "
            "fora do que a IA recebeu.".format(sumarios[1], sum(sumarios))
        )
    if sumarios_fora:
        itens.append(
            "{} fora do que a IA recebeu pelo limite de tamanho da mensagem; entraram primeiro os "
            "dos áudios citados nos trechos de evidência.".format(
                _plural(sumarios_fora, "Sumário Executivo ficou", "Sumários Executivos ficaram")
            )
        )
    return itens


def apendice_markdown(
    agregacao: Agregacao,
    sinc_projeto: Optional[pd.DataFrame],
    transcricao_projeto: Optional[pd.DataFrame],
    perguntas_ativacao: Optional[Sequence[dict]] = None,
    sumarios: Optional[Tuple[int, int]] = None,
    incluir_legenda: bool = True,
    sumarios_fora: int = 0,
) -> str:
    """O apêndice técnico, gerado pelo código, só com os áudios do recorte.

    Os áudios aparecem pelo alias. A legenda alias → sessão, que traz o
    telefone, entra só com `incluir_legenda`. `perguntas_ativacao` é a lista
    de perguntas com maior ativação da Análise Geral (entrevista);
    `sumarios`, o par (com, sem) de contar_sumarios; e `sumarios_fora`,
    quantos sumários ficaram fora da mensagem da IA pelo limite de tamanho.
    """
    sessoes = {str(a.session_id): agregacao.aliases[a.audio_id] for a in agregacao.recorte}
    sinc = _recorte_com_alias(sinc_projeto, sessoes)
    transcricao = _recorte_com_alias(transcricao_projeto, sessoes)

    secoes: List[Tuple[str, str]] = [("Voz por tema", _secao_voz_por_tema(agregacao))]
    if sinc.empty:
        secoes.append(("Métricas acústicas", "Os áudios do recorte não têm o Sincronizado com as métricas de voz."))
    else:
        secoes.append((
            "Médias acústicas por áudio",
            _virgula_decimal(sinais.speaker_acoustics_text(_com_linha_do_recorte(sinc), "_alias", "Áudio")),
        ))
        secoes.append((
            "Distribuição de emoções",
            _virgula_decimal(sinais.emotion_distribution_text(_com_linha_do_recorte(sinc), "_alias", "Áudio")),
        ))
        secoes.append(("Momentos de maior ativação", _secao_momentos(sinc)))
    if agregacao.tipo_projeto == ENTREVISTA_QUALITATIVA and perguntas_ativacao:
        secoes.append(("Perguntas com maior ativação", _secao_perguntas(perguntas_ativacao)))

    ranking = ranking_palavras(linha.texto for audio in agregacao.recorte for linha in audio.linhas)
    secoes.append((
        "Ranking de palavras",
        _tabela(["Palavra", "Menções"], ([p, str(n)] for p, n in ranking)) if ranking
        else "Nenhuma palavra relevante nas transcrições do recorte.",
    ))

    fonte = transcricao if sinais.tem_sentimento_texto(transcricao) else sinc
    sentimento = sinais.texto_sentimento_resumo(fonte, "_alias", "Áudio") if not fonte.empty else ""
    secoes.append((
        "Sentimento do texto",
        _virgula_decimal(sentimento) or "Os áudios do recorte não têm nota do sentimento do texto.",
    ))
    secoes.append(("Índice combinado", _secao_indice(sinc, agregacao.reguas.valencia, sessoes)))
    secoes.append(("Divergências voz × texto", divergencias_markdown(agregacao, sinc_projeto)))
    if incluir_legenda:
        secoes.append(("Legenda dos áudios", legenda_markdown(agregacao)))
    secoes.append((
        "Limitações e cortes aplicados",
        "\n".join("- " + item for item in _limitacoes(agregacao, sumarios, sumarios_fora)),
    ))
    return "\n\n".join("#### {}\n\n{}".format(titulo, corpo) for titulo, corpo in secoes)


# ---------------------------------------------------------------------------
# Sumários Executivos que vão para a IA
# ---------------------------------------------------------------------------

# Teto do bloco de sumários na mensagem (uns 20 mil tokens): com 1.200
# caracteres por sumário, cabem uns 65 áudios. Passou disso, entram primeiro
# os dos áudios citados nos trechos de evidência.
_MAX_SUMARIOS_NA_MENSAGEM = 80000
_SESSAO_COM_TELEFONE = re.compile(r"wa_\+?\d{8,}(?:_\d+)?")
_SEM_SUMARIOS = "Nenhuma análise individual dos áudios do recorte traz Sumário Executivo."


@dataclass(frozen=True)
class Sumarios:
    """O bloco de Sumários Executivos da mensagem e o que ficou de fora dele."""

    texto: str
    # Análises individuais dos áudios contados, com e sem Sumário Executivo.
    com: int = 0
    sem: int = 0
    # Sumários que não couberam no bloco.
    fora_do_limite: int = 0


def sumarios_individuais(agregacao: Agregacao, analises: Dict[int, Optional[str]]) -> Sumarios:
    """Os Sumários Executivos das análises individuais dos áudios contados.

    `analises` traz, por audio_id, o texto da análise individual mais
    recente. Cada sumário vai rotulado pelo alias, e a sessão que aparecer
    no texto vira o alias: ela traz o telefone do respondente. Os sumários
    dos áudios citados nos trechos de evidência entram primeiro; o bloco sai
    na ordem dos aliases.
    """
    contados = set(agregacao.contados)
    nomes = agregacao.aliases
    sessoes = sorted(
        ((str(a.session_id), nomes[a.audio_id]) for a in agregacao.recorte if a.session_id),
        key=lambda par: -len(par[0]),
    )
    citados = {t.audio_id for trechos in agregacao.trechos.values() for t in trechos}
    candidatos: List[Tuple[int, str]] = []
    com = sem = 0
    for audio in agregacao.recorte:
        texto = analises.get(audio.audio_id)
        if audio.audio_id not in contados or not str(texto or "").strip():
            continue
        sumario = sumario_executivo(texto)
        if not sumario:
            sem += 1
            continue
        com += 1
        for sessao, alias in sessoes:
            sumario = sumario.replace(sessao, alias)
        sumario = _SESSAO_COM_TELEFONE.sub("[sessão]", sumario)
        candidatos.append((audio.audio_id, "### {}\n{}".format(nomes[audio.audio_id], sumario)))

    escolhidos: List[Tuple[int, str]] = []
    usados = 0
    for audio_id, bloco in sorted(candidatos, key=lambda c: (c[0] not in citados, c[0])):
        if escolhidos and usados + len(bloco) > _MAX_SUMARIOS_NA_MENSAGEM:
            continue
        escolhidos.append((audio_id, bloco))
        usados += len(bloco) + 2
    texto = "\n\n".join(bloco for _, bloco in sorted(escolhidos))
    return Sumarios(texto or _SEM_SUMARIOS, com, sem, len(candidatos) - len(escolhidos))


# ---------------------------------------------------------------------------
# Mensagem da IA
# ---------------------------------------------------------------------------

def _e(itens: Sequence[str]) -> str:
    itens = list(itens)
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def achados_esperados(agregacao: Agregacao) -> List[Tuple[str, LinhaTema]]:
    """O ID de cada achado: um por subtema em padrão ou sinal, na ordem da tabela."""
    com_id = (tema for tema in agregacao.temas if tema.faixa in (PADRAO, SINAL))
    return [("A{}".format(numero), tema) for numero, tema in enumerate(com_id, 1)]


def _grupos_a_ler(agregacao: Agregacao) -> List[str]:
    """Os grupos da Matriz que pedem leitura à IA: os que têm áudio do recorte."""
    if not agregacao.matriz.avaliavel:
        return []
    return [grupo for grupo, _, ler in _grupos_da_matriz(agregacao) if ler]


def mensagem_para_ia(
    agregacao: Agregacao,
    contexto: dict,
    cabecalho: str,
    sinc_projeto: Optional[pd.DataFrame] = None,
    sumarios: Optional[Sumarios] = None,
) -> str:
    """A mensagem do usuário da Análise Geral, com as tabelas prontas.

    A tarefa já traz o ID de cada achado, com o subtema, a faixa, a fração e
    a leitura da voz que ele repete. Os áudios aparecem só pelo alias, e na
    entrevista as divergências vêm só com as falas do entrevistado.
    """
    total = agregacao.n_respondentes
    achados = [
        "{} · {} · {}, {}. Voz: {}.".format(
            ident, tema.subtema, tema.faixa.lower(), _fracao(tema.respondentes, total), tema.voz.texto()
        )
        for ident, tema in achados_esperados(agregacao)
    ]
    delta = agregacao.delta
    tarefa = prompts.tarefa_analise_geral(
        achados,
        linha_final=bool(agregacao.emergentes) or any(t.faixa == MENCAO_ISOLADA for t in agregacao.temas),
        grupos_matriz=_grupos_a_ler(agregacao),
        termometro_avaliavel=agregacao.termometro.avaliavel,
        exploratoria=total if agregacao.exploratoria else None,
        novos_sem_tendencia=(
            delta.novos if delta is not None and delta.exibir and not delta.sustenta_tendencia else None
        ),
    )
    return prompts.mensagem_analise_geral(
        contexto,
        cabecalho,
        tabela_temas_markdown(agregacao, incluir_emergentes=False),
        trechos_markdown(agregacao),
        emergentes_markdown(agregacao) or "Nenhum tema emergente.",
        matriz_markdown(agregacao),
        termometro_markdown(agregacao),
        divergencias_markdown(agregacao, sinc_projeto, so_respondente=True),
        sumarios.texto if sumarios is not None else _SEM_SUMARIOS,
        tarefa,
    )


# ---------------------------------------------------------------------------
# Texto da IA: seções
# ---------------------------------------------------------------------------

RESUMO = "resumo"
ACHADOS = "achados"
HIPOTESES = "hipoteses"
RECOMENDACOES = "recomendacoes"
LEITURA_MATRIZ = "matriz"
LEITURA_TERMOMETRO = "termometro"

# Os títulos da Estrutura de Resposta Obrigatória, na ordem.
TITULOS_DA_IA = {
    RESUMO: "Resumo Executivo",
    ACHADOS: "Achados",
    HIPOTESES: "Hipóteses",
    RECOMENDACOES: "Recomendações",
    LEITURA_MATRIZ: "Matriz de Destaques — leitura",
    LEITURA_TERMOMETRO: "Termômetro — leitura",
}

# Começo do título, sem caixa nem acento nem numeração, de cada seção.
_INICIO_DO_TITULO = (
    ("resumo", RESUMO),
    ("achado", ACHADOS),
    ("hipotese", HIPOTESES),
    ("recomenda", RECOMENDACOES),
    ("matriz", LEITURA_MATRIZ),
    ("termometro", LEITURA_TERMOMETRO),
)
_TITULO_DA_IA = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_TITULO_EM_NEGRITO = re.compile(r"^\s*(?:\*\*|__)([^*_]+?)(?:\*\*|__)\s*:?\s*$")
_NUMERACAO = re.compile(r"^(?:\d+\s*[.)]?\s*)+")
_MARCAS = re.compile(r"[*_`]")
# Títulos de seção que a estrutura proíbe à IA: tabelas, cabeçalho e apêndice
# são do código, e limitações, introdução e conclusão não entram.
_TITULOS_PROIBIDOS = (
    "taxonomia", "tabela", "apendice", "limitac", "conclus", "introduc", "referenc", "cabecalho", "metodolog",
)
_LINHA_DE_TABELA = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class TextoDaIA:
    """A resposta da IA separada nas seções da Estrutura de Resposta Obrigatória."""

    secoes: Dict[str, str]
    # Títulos que a IA escreveu fora da estrutura; o conteúdo deles não entra
    # no relatório.
    fora_da_estrutura: List[str] = field(default_factory=list)
    # Seções em que a IA escreveu tabela, que também não entra: as tabelas
    # são do código.
    com_tabela: List[str] = field(default_factory=list)

    def secao(self, nome: str) -> str:
        return self.secoes.get(nome, "").strip()


def _secao_do_titulo(titulo: str) -> Optional[str]:
    """A seção de um título; "Leitura da Matriz" também é a da Matriz."""
    chave = _NUMERACAO.sub("", chave_rotulo(_MARCAS.sub("", titulo))).strip(" .:—–-")
    for inicio, secao in _INICIO_DO_TITULO:
        if chave.startswith(inicio):
            return secao
    for palavra, secao in (("matriz", LEITURA_MATRIZ), ("termometro", LEITURA_TERMOMETRO)):
        if palavra in chave:
            return secao
    return None


def _vazia(linha: str) -> bool:
    return not linha.strip() or bool(_REGUA.match(linha))


def _aparar(linhas: List[str]) -> str:
    """O texto sem linhas em branco nem réguas nas pontas."""
    inicio, fim = 0, len(linhas)
    while inicio < fim and _vazia(linhas[inicio]):
        inicio += 1
    while fim > inicio and _vazia(linhas[fim - 1]):
        fim -= 1
    return "\n".join(linhas[inicio:fim])


def separar_secoes(texto: Optional[str]) -> TextoDaIA:
    """Separa a resposta da IA nas seções, pelos títulos.

    Vale título de markdown ou linha só de negrito, com ou sem numeração, sem
    diferenciar caixa nem acento. O que vem antes do primeiro título fica de
    fora. Título de seção que a estrutura proíbe (tabela, taxonomia,
    cabeçalho, apêndice, metodologia, limitações, introdução, conclusão,
    referências), no nível dos títulos de seção ou acima, abre um trecho que
    não entra no relatório; qualquer outro título é subtítulo da seção em que
    está. Tabela escrita pela IA também fica de fora.
    """
    secoes: Dict[str, List[str]] = {}
    fora: List[str] = []
    com_tabela: List[str] = []
    atual: Optional[str] = None
    nivel: Optional[int] = None
    for linha in str(texto or "").splitlines():
        markdown = _TITULO_DA_IA.match(linha)
        negrito = None if markdown else _TITULO_EM_NEGRITO.match(linha)
        titulo = markdown.group(2) if markdown else negrito.group(1) if negrito else None
        secao = _secao_do_titulo(titulo) if titulo else None
        if secao:
            atual = secao
            secoes.setdefault(secao, [])
            if markdown and nivel is None:
                nivel = len(markdown.group(1))
            continue
        if (
            markdown
            and (nivel is None or len(markdown.group(1)) <= nivel)
            and any(proibido in chave_rotulo(titulo) for proibido in _TITULOS_PROIBIDOS)
        ):
            fora.append(" ".join(_MARCAS.sub("", titulo).split()))
            atual = None
            continue
        if atual is None:
            continue
        if _LINHA_DE_TABELA.match(linha):
            if atual not in com_tabela:
                com_tabela.append(atual)
            continue
        secoes[atual].append(linha)
    return TextoDaIA({secao: _aparar(linhas) for secao, linhas in secoes.items()}, fora, com_tabela)


# O nome do grupo no começo do parágrafo da leitura da Matriz, como título,
# item ou negrito, com o separador que vier depois.
_ROTULO_DO_GRUPO = re.compile(
    r"^\s*(?:#+\s*|[-*•]\s+)?(?:\*\*|__)?\s*"
    r"(?P<grupo>v[ií]nculo\s+forte|risco\s+de\s+abandono(?:\s+silencioso)?)"
    r"(?:\s*(?:\*\*|__|[:—–-]))*\s*",
    re.IGNORECASE,
)


def _leituras_da_matriz(texto: str) -> Dict[str, str]:
    """A leitura de cada grupo, pelo nome do grupo no começo do parágrafo.

    O texto antes do primeiro nome, ou sem nome nenhum, fica sob a chave "".
    """
    partes: Dict[str, List[str]] = {}
    atual = ""
    for linha in texto.splitlines():
        rotulo = _ROTULO_DO_GRUPO.match(linha)
        if rotulo:
            atual = VINCULO if chave_rotulo(rotulo.group("grupo")).startswith("vinculo") else RISCO
            linha = linha[rotulo.end():]
        partes.setdefault(atual, []).append(linha)
    leituras = {grupo: _aparar(linhas) for grupo, linhas in partes.items()}
    return {grupo: leitura for grupo, leitura in leituras.items() if leitura}


def _leituras_que_entram(agregacao: Agregacao, texto_da_ia: TextoDaIA) -> Dict[str, str]:
    """As leituras da Matriz que entram no relatório, por grupo.

    Entram só as dos grupos com áudio. Sem nenhum grupo nomeado, a leitura
    inteira entra depois das tabelas, sob a chave "".
    """
    a_ler = _grupos_a_ler(agregacao)
    if not a_ler:
        return {}
    leituras = _leituras_da_matriz(texto_da_ia.secao(LEITURA_MATRIZ))
    if any(grupo in leituras for grupo in (VINCULO, RISCO)):
        return {grupo: leituras[grupo] for grupo in a_ler if grupo in leituras}
    return {"": leituras[""]} if "" in leituras else {}


def _texto_que_entra(agregacao: Agregacao, texto_da_ia: TextoDaIA) -> Dict[str, str]:
    """O texto da IA que vai para o relatório, por seção e na ordem delas.

    A leitura da Matriz entra só para os grupos com áudio, e a do Termômetro,
    só com ele avaliável: no resto, o relatório traz só o texto do código.
    """
    textos = {secao: texto_da_ia.secao(secao) for secao in (RESUMO, ACHADOS, HIPOTESES, RECOMENDACOES)}
    textos[LEITURA_MATRIZ] = "\n\n".join(_leituras_que_entram(agregacao, texto_da_ia).values())
    textos[LEITURA_TERMOMETRO] = (
        texto_da_ia.secao(LEITURA_TERMOMETRO) if agregacao.termometro.avaliavel else ""
    )
    return textos


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------

_SEM_TEXTO = "_A IA não escreveu esta seção._"


def _matriz_com_leituras(agregacao: Agregacao, texto_da_ia: TextoDaIA) -> str:
    if not agregacao.matriz.avaliavel:
        return matriz_markdown(agregacao)
    leituras = _leituras_que_entram(agregacao, texto_da_ia)
    blocos = []
    for grupo, markdown, _ in _grupos_da_matriz(agregacao):
        blocos.append(markdown)
        if grupo in leituras:
            blocos.append(leituras[grupo])
    if "" in leituras:
        blocos.append(leituras[""])
    return "\n\n".join(blocos)


def montar_relatorio(agregacao: Agregacao, cabecalho: str, texto_da_ia: TextoDaIA, apendice: str) -> str:
    """O relatório final, na ordem fixa da spec (§8).

    O código põe o cabeçalho, as tabelas e o apêndice, e a IA, o texto de
    cada seção. A leitura de cada grupo da Matriz vai embaixo da tabela do
    grupo, sem o nome, que já está no título dele. Seção que a IA não
    escreveu sai com um aviso no lugar. As referências da base de
    conhecimento ficam fora do texto, como hoje: a página, o .md e o PDF as
    mostram a partir das citações.
    """
    textos = _texto_que_entra(agregacao, texto_da_ia)
    termometro = termometro_markdown(agregacao)
    if textos[LEITURA_TERMOMETRO]:
        termometro += "\n\n" + textos[LEITURA_TERMOMETRO]
    blocos = [
        cabecalho.strip(),
        "### Resumo Executivo\n\n" + (textos[RESUMO] or _SEM_TEXTO),
        "### 1. Taxonomia de Temas\n\n" + tabela_temas_markdown(agregacao),
        "### 2. Achados\n\n" + (textos[ACHADOS] or _SEM_TEXTO),
        "### 3. Hipóteses\n\n" + (textos[HIPOTESES] or _SEM_TEXTO),
        "### 4. Recomendações\n\n" + (textos[RECOMENDACOES] or _SEM_TEXTO),
        "### 5. Matriz de Destaques\n\n" + _matriz_com_leituras(agregacao, texto_da_ia),
        "### 6. Termômetro de Experiência\n\n" + termometro,
        "### Apêndice técnico\n\n" + apendice.strip(),
    ]
    return "\n\n".join(blocos) + "\n"


# ---------------------------------------------------------------------------
# Verificação das referências
# ---------------------------------------------------------------------------

_ID_CITADO = re.compile(r"(?<![\w])([AHR])(\d+)(?![\w])")
_INICIO_DE_ITEM = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)?(?:\*\*|__)?\s*([AHR])(\d+)(?![\w])")
_LINHA_DE_LISTA = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# Linha do achado já sem negrito: "- A1 · Categoria > Subtema · faixa, n/N".
# Aceita os outros separadores de costume entre as partes.
_CABECA_DO_ACHADO = re.compile(
    r"^\s*(?:[-•]\s+|\d+[.)]\s+)?A\d+\s*[·•|:—–-]\s*"
    r"(?P<subtema>[^·•|—–]+?)\s*[·•|—–]\s*"
    r"(?P<faixa>[^·•|—–,(\d]+?)\s*[,(:·•|—–-]?\s*(?P<n>\d+)/(?P<total>\d+)"
)
# n/N sem zero à esquerda: "30/09" e "04/10" são datas, não frações.
_FRACAO = re.compile(r"(?<![\w/.,])(0|[1-9]\d*)/([1-9]\d*)(?![\w/])")
_ID_ANTES = re.compile(r"(?<![\w])A(\d+)\s*[,:(]?\s*$")
_AUDIO_CITADO = re.compile(r"(?<![\w])[ÁáAa]udios?\s+(\d+(?![\d:])(?:\s*(?:,|\be\b)\s*\d+(?![\d:]))*)")
_SETA = re.compile(r"(?<=\w) > (?=\w)")
_SEPARADORES = re.compile(r"[·•|—–()\[\],;:.!?]")

_APOIA_SE = re.compile(r"\bapoia\W*se\s+em\b", re.IGNORECASE)
_COMO_VALIDAR = re.compile(r"\bcomo\s+validar\b", re.IGNORECASE)
# Com a fronteira, "corresponde a" não conta como "Responde a".
_RESPONDE_A = re.compile(r"\bresponde\s+a\b", re.IGNORECASE)
_COMO_MEDIR = re.compile(r"\bcomo\s+medir\b", re.IGNORECASE)
_PARTES_DA_HIPOTESE = (('"Apoia-se em"', _APOIA_SE), ('"Como validar"', _COMO_VALIDAR))
_PARTES_DA_RECOMENDACAO = (
    ("a classificação [Higiene] ou [Encantamento]", re.compile(r"higiene|encantamento", re.IGNORECASE)),
    ('"Driver"', re.compile(r"driver\s*:", re.IGNORECASE)),
    ('"Área"', re.compile(r"[áa]rea\s*:", re.IGNORECASE)),
    ('"Responde a"', _RESPONDE_A),
    ('"Como medir"', _COMO_MEDIR),
)

# A leitura de voz, sem acento, como a tabela a escreve.
_DIRECOES_DE_VOZ = (("acima do habitual", ACIMA), ("abaixo do habitual", ABAIXO), ("no habitual", NO_HABITUAL))
_QUALIFICADOR_CITADO = re.compile(r"\b(reforca|atenua|contradiz)\w*")
_NIVEL_CITADO = re.compile(r"\bativacao\s+(alta|media|baixa)\b")
_COM_ACENTO = {"reforca": "reforça", "media": "média"}


def _limpa_linha(texto: str) -> str:
    """O texto numa linha só, sem negrito, itálico nem código."""
    return " ".join(_MARCAS.sub("", texto).split())


def _normal(texto: str) -> str:
    """Minúsculas sem acento, com o mesmo comprimento do texto."""
    saida = []
    for caractere in texto:
        chave = chave_rotulo(caractere)
        saida.append(chave if len(chave) == 1 else caractere.lower())
    return "".join(saida)


@dataclass
class _Item:
    """Um achado, hipótese ou recomendação, com as linhas de continuação."""

    ident: str
    linhas: List[str]

    @property
    def texto(self) -> str:
        return _limpa_linha(" ".join(self.linhas))


def _itens(texto: str, letra: str) -> List[_Item]:
    itens: List[_Item] = []
    atual: Optional[_Item] = None
    for linha in texto.splitlines():
        inicio = _INICIO_DE_ITEM.match(linha)
        if inicio and inicio.group(1) == letra:
            atual = _Item(letra + inicio.group(2), [linha])
            itens.append(atual)
        elif (
            atual is not None
            and linha.strip()
            and not inicio
            and not _LINHA_DE_LISTA.match(linha)
            and not linha.lstrip().startswith("#")
        ):
            atual.linhas.append(linha)
        else:
            atual = None
    return itens


@dataclass
class _Tabelas:
    """O que as tabelas do relatório trazem, para conferir o texto da IA."""

    # Rótulo inteiro e nome curto, sem acento, de cada linha da tabela.
    temas: Dict[str, LinhaTema]
    sem_mencao: Dict[str, str]
    # Nome (de tema, de subtema sem menção ou de emergente) -> frações dele.
    fracoes_do_nome: Dict[str, Set[Tuple[int, int]]]
    fracao_principal: Dict[str, str]
    nome_exibido: Dict[str, str]
    fracoes: Set[Tuple[int, int]]
    rotulos: List[str]
    audio_do_numero: Dict[int, int]
    recorte: Set[int]
    padrao_dos_nomes: Optional["re.Pattern"]


def _fracoes_do_tema(agregacao: Agregacao, tema: LinhaTema) -> Set[Tuple[int, int]]:
    """As frações de um tema nas tabelas: o total, o Δ e os grupos da Matriz."""
    fracoes = {(tema.respondentes, agregacao.n_respondentes)}
    delta = agregacao.delta
    if delta is not None and delta.exibir:
        fracoes.update({(tema.anteriores or 0, delta.anteriores), (tema.novos or 0, delta.novos)})
    for grupo in (agregacao.matriz.vinculo, agregacao.matriz.risco):
        if grupo is not None:
            fracoes.update((n, grupo.total) for nome, n in grupo.temas_mais_citados if nome == tema.nome)
    return fracoes


def _tabelas(agregacao: Agregacao) -> _Tabelas:
    total = agregacao.n_respondentes
    temas: Dict[str, LinhaTema] = {}
    sem_mencao: Dict[str, str] = {}
    fracoes_do_nome: Dict[str, Set[Tuple[int, int]]] = {}
    fracao_principal: Dict[str, str] = {}
    nome_exibido: Dict[str, str] = {}
    rotulos: List[str] = []

    def registrar(nomes: Iterable[str], exibido: str, fracoes: Set[Tuple[int, int]], principal: str) -> None:
        for nome in nomes:
            chave = _normal(" ".join(nome.split()))
            fracoes_do_nome.setdefault(chave, set()).update(fracoes)
            fracao_principal.setdefault(chave, principal)
            nome_exibido.setdefault(chave, exibido)

    for tema in agregacao.temas:
        for nome in (tema.subtema, tema.nome):
            temas.setdefault(_normal(nome), tema)
        rotulos.append(_normal(tema.subtema))
        registrar(
            (tema.subtema, tema.nome), tema.nome, _fracoes_do_tema(agregacao, tema),
            _fracao(tema.respondentes, total),
        )
    for rotulo in agregacao.sem_mencao:
        nome = rotulo.split(">", 1)[-1].strip()
        for chave in (_normal(rotulo), _normal(nome)):
            sem_mencao.setdefault(chave, rotulo)
        rotulos.append(_normal(rotulo))
        registrar((rotulo, nome), nome, {(0, total)}, _fracao(0, total))
    for emergente in agregacao.emergentes[:_MAX_EMERGENTES]:
        nomes = [emergente.rotulo]
        if emergente.categoria:
            nomes.append("{} > {}".format(emergente.categoria, emergente.rotulo))
            rotulos.append(_normal(nomes[-1]))
        fracao = (emergente.respondentes, total)
        registrar(nomes, emergente.rotulo, {fracao}, _fracao(*fracao))

    padrao = None
    if fracoes_do_nome:
        nomes = sorted(fracoes_do_nome, key=len, reverse=True)
        padrao = re.compile(r"(?:^|[^\w])(" + "|".join(re.escape(n) for n in nomes) + r")\s*[,:(]?\s*$")
    numeros = {int(alias.split()[-1]): audio_id for audio_id, alias in agregacao.aliases.items()}
    return _Tabelas(
        temas=temas,
        sem_mencao=sem_mencao,
        fracoes_do_nome=fracoes_do_nome,
        fracao_principal=fracao_principal,
        nome_exibido=nome_exibido,
        fracoes=set().union(*fracoes_do_nome.values()) if fracoes_do_nome else set(),
        rotulos=rotulos,
        audio_do_numero=numeros,
        recorte={audio.audio_id for audio in agregacao.recorte},
        padrao_dos_nomes=padrao,
    )


def _conflitos_de_voz(texto: str, voz: LeituraVoz) -> List[str]:
    """O que o achado diz da voz e a tabela não diz. `texto` vem sem acento."""
    rotulos = list(re.finditer(r"\bvoz\s*:", texto))
    if rotulos:
        citada = texto[rotulos[-1].end():]
    else:
        primeira = re.search(r"\bvoz\b", texto)
        if primeira is None:
            return []
        citada = texto[primeira.end():]
    qualificadores = sorted({m.group(1) for m in _QUALIFICADOR_CITADO.finditer(citada)})
    direcoes = [direcao for frase, direcao in _DIRECOES_DE_VOZ if frase in citada]
    niveis = sorted({m.group(1) for m in _NIVEL_CITADO.finditer(citada)})
    if voz.avaliavel:
        qualificadores = [q for q in qualificadores if q != _normal(voz.qualificador)]
        direcoes = [d for d in direcoes if d != voz.direcao]
        niveis = [n for n in niveis if n != _normal(voz.nivel)]
    return (
        [_COM_ACENTO.get(q, q) for q in qualificadores]
        + direcoes
        + ["ativação " + _COM_ACENTO.get(n, n) for n in niveis]
    )


def _conferir_achados(
    agregacao: Agregacao, tabelas: _Tabelas, achados: List[_Item], problemas: List[str]
) -> Dict[str, LinhaTema]:
    """O formato de cada achado contra a tabela. Devolve o tema de cada ID."""
    total = agregacao.n_respondentes
    tema_do_achado: Dict[str, LinhaTema] = {}
    achados_do_tema: Dict[str, List[str]] = {}
    for item in achados:
        cabeca = _CABECA_DO_ACHADO.match(_limpa_linha(item.linhas[0]))
        if not cabeca:
            problemas.append('{} não segue o formato "Categoria > Subtema · faixa, n/N".'.format(item.ident))
            continue
        citado = " ".join(cabeca.group("subtema").split())
        chave = _normal(re.sub(r"\s*>\s*", " > ", citado))
        tema = tabelas.temas.get(chave)
        if tema is None:
            onde = "não tem menção no recorte" if chave in tabelas.sem_mencao else "não está na tabela de temas"
            problemas.append('{} cita "{}", que {}.'.format(item.ident, citado, onde))
            continue
        tema_do_achado[item.ident] = tema
        achados_do_tema.setdefault(tema.subtema, []).append(item.ident)
        if tema.faixa == MENCAO_ISOLADA:
            problemas.append("{} é de {}, menção isolada ({}): menção isolada não leva ID.".format(
                item.ident, tema.nome, _fracao(tema.respondentes, total)
            ))
            continue
        faixa_citada = " ".join(cabeca.group("faixa").split())
        if _normal(faixa_citada) != _normal(tema.faixa):
            problemas.append('{} diz "{}", mas {} está em {} na tabela.'.format(
                item.ident, faixa_citada, tema.nome, tema.faixa.lower()
            ))
        fracao = (int(cabeca.group("n")), int(cabeca.group("total")))
        if fracao != (tema.respondentes, total):
            problemas.append("{} traz {}, mas a tabela traz {} para {}.".format(
                item.ident, _fracao(*fracao), _fracao(tema.respondentes, total), tema.nome
            ))
        conflitos = _conflitos_de_voz(_normal(item.texto), tema.voz)
        if conflitos:
            problemas.append('{}: a tabela traz a voz "{}", e o achado diz {}.'.format(
                item.ident, tema.voz.texto(), _e(['"{}"'.format(c) for c in conflitos])
            ))
    for _, tema in achados_esperados(agregacao):
        if tema.subtema not in achados_do_tema:
            problemas.append("Falta o achado de {} ({}, {}).".format(
                tema.subtema, tema.faixa.lower(), _fracao(tema.respondentes, total)
            ))
    for subtema, idents in achados_do_tema.items():
        if len(idents) > 1:
            problemas.append("{} aparece em {}.".format(subtema, _e(idents)))
    return tema_do_achado


def _trecho_depois(texto: str, marcador: "re.Pattern", fim: "re.Pattern") -> Optional[str]:
    """O texto depois do marcador, até o marcador seguinte; None sem ele."""
    inicio = marcador.search(texto)
    if inicio is None:
        return None
    resto = texto[inicio.end():]
    corte = fim.search(resto)
    return resto[:corte.start()] if corte else resto


def _conferir_hipoteses(hipoteses: List[_Item], problemas: List[str]) -> None:
    for item in hipoteses:
        faltam = [nome for nome, marcador in _PARTES_DA_HIPOTESE if not marcador.search(item.texto)]
        if faltam:
            problemas.append("{} está sem {}.".format(item.ident, _e(faltam)))
        base = _trecho_depois(item.texto, _APOIA_SE, _COMO_VALIDAR)
        if base is not None and not any(letra == "A" for letra, _ in _ID_CITADO.findall(base)):
            problemas.append("{} não se apoia em nenhum achado.".format(item.ident))


def _conferir_recomendacoes(recomendacoes: List[_Item], problemas: List[str]) -> None:
    for item in recomendacoes:
        faltam = [nome for nome, marcador in _PARTES_DA_RECOMENDACAO if not marcador.search(item.texto)]
        if faltam:
            problemas.append("{} está sem {}.".format(item.ident, _e(faltam)))
        alvo = _trecho_depois(item.texto, _RESPONDE_A, _COMO_MEDIR)
        if alvo is not None and not any(letra in "AH" for letra, _ in _ID_CITADO.findall(alvo)):
            problemas.append("{} não responde a nenhum achado nem hipótese.".format(item.ident))


def _conferir_linha(
    titulo: str,
    linha: str,
    inicio: int,
    agregacao: Agregacao,
    tabelas: _Tabelas,
    tema_do_achado: Dict[str, LinhaTema],
    problemas: List[str],
) -> None:
    """Frações e subtemas citados numa linha, a partir de `inicio`.

    A fração logo depois de um ID ("A1, 19/64") tem de ser uma das frações
    do tema desse achado; logo depois do nome de um tema ou emergente, uma
    das dele; e solta, uma das que as tabelas trazem.
    """
    delta = agregacao.delta
    data_anterior = delta.anterior.data if delta is not None else ""
    normal = _normal(linha)
    for fracao in _FRACAO.finditer(linha, inicio):
        valor = (int(fracao.group(1)), int(fracao.group(2)))
        if valor[0] > valor[1] or fracao.group(0) == data_anterior:
            continue
        ident = _ID_ANTES.search(linha, 0, fracao.start())
        tema = tema_do_achado.get("A" + ident.group(1)) if ident else None
        if tema is not None:
            if valor not in _fracoes_do_tema(agregacao, tema):
                problemas.append("{} cita A{} com {}, mas a tabela traz {} para {}.".format(
                    titulo, ident.group(1), fracao.group(0),
                    _fracao(tema.respondentes, agregacao.n_respondentes), tema.nome,
                ))
            continue
        nome = None
        if tabelas.padrao_dos_nomes is not None:
            achado = tabelas.padrao_dos_nomes.search(normal, 0, fracao.start())
            nome = achado.group(1) if achado else None
        if nome is not None:
            if valor not in tabelas.fracoes_do_nome[nome]:
                problemas.append("{} cita {} com {}, mas a tabela traz {}.".format(
                    titulo, tabelas.nome_exibido[nome], fracao.group(0), tabelas.fracao_principal[nome]
                ))
        elif valor not in tabelas.fracoes:
            problemas.append("{} cita {}, que não aparece nas tabelas.".format(titulo, fracao.group(0)))

    cobertos = [
        (m.start(), m.end())
        for rotulo in tabelas.rotulos
        for m in re.finditer(re.escape(rotulo), normal)
    ]
    for seta in _SETA.finditer(normal, inicio):
        if not any(a <= seta.start() and seta.end() <= b for a, b in cobertos):
            problemas.append('{} cita "{}", que não está na tabela de temas.'.format(
                titulo, _rotulo_em_volta(linha, seta.start(), seta.end())
            ))


# Palavras que encerram o subtema citado na prosa: "Ambiente > Barulho e pela...".
_FIM_DO_SUBTEMA = frozenset({
    "e", "ou", "que", "pela", "pelo", "pelas", "pelos", "com", "mas", "porque", "quando",
    "foi", "é", "são", "está", "estão", "ficou", "aparece", "aparecem",
})


def _rotulo_em_volta(linha: str, inicio: int, fim: int) -> str:
    """O "Categoria > Subtema" em volta de um ">" da prosa, para a mensagem.

    A categoria começa na última sequência de palavras com maiúscula antes
    da seta, e o subtema vai até a pontuação ou até uma palavra de ligação.
    """
    antes = _SEPARADORES.split(linha[:inicio])[-1].split()[-3:]
    maiusculas = [i for i, palavra in enumerate(antes) if palavra[:1].isupper()]
    if maiusculas:
        comeco = maiusculas[-1]
        while comeco > 0 and antes[comeco - 1][:1].isupper():
            comeco -= 1
        categoria = antes[comeco:]
    else:
        categoria = antes[-1:]
    subtema = []
    for palavra in _SEPARADORES.split(linha[fim:])[0].split()[:6]:
        if subtema and palavra.lower() in _FIM_DO_SUBTEMA:
            break
        subtema.append(palavra)
    return "{} > {}".format(" ".join(categoria), " ".join(subtema))


def verificar_referencias(agregacao: Agregacao, texto_da_ia: TextoDaIA) -> List[str]:
    """Confere o texto da IA contra as tabelas e devolve os problemas.

    Confere o que a spec pede (§8): todo A, H ou R citado existe; toda
    recomendação tem "Responde a" e toda hipótese se apoia em achado; todo
    "Áudio NN" citado é do recorte; todo subtema citado está na tabela; toda
    fração n/N bate com ela. Confere também cada achado (um por subtema em
    padrão ou sinal, com a faixa, a fração e a leitura de voz da tabela), as
    partes de cada hipótese e recomendação, as seções que faltam ou que a IA
    escreveu fora da estrutura, as tabelas que ela escreveu e se alguma
    sessão com telefone escapou para o texto. Só olha o texto que entra no
    relatório, e nada aqui impede salvar.
    """
    problemas: List[str] = []
    for secao in (RESUMO, ACHADOS, HIPOTESES, RECOMENDACOES):
        if not texto_da_ia.secao(secao):
            problemas.append('A resposta da IA não trouxe a seção "{}".'.format(TITULOS_DA_IA[secao]))
    a_ler = _grupos_a_ler(agregacao)
    if a_ler and not texto_da_ia.secao(LEITURA_MATRIZ):
        problemas.append('A resposta da IA não trouxe a seção "{}".'.format(TITULOS_DA_IA[LEITURA_MATRIZ]))
    elif a_ler:
        leituras = _leituras_da_matriz(texto_da_ia.secao(LEITURA_MATRIZ))
        if any(grupo in leituras for grupo in (VINCULO, RISCO)):
            problemas.extend(
                'A leitura da Matriz não trouxe o grupo "{}".'.format(grupo) for grupo in a_ler if grupo not in leituras
            )
    if agregacao.termometro.avaliavel and not texto_da_ia.secao(LEITURA_TERMOMETRO):
        problemas.append('A resposta da IA não trouxe a seção "{}".'.format(TITULOS_DA_IA[LEITURA_TERMOMETRO]))
    problemas.extend(
        'A IA escreveu a seção "{}", fora da estrutura: ela ficou fora do relatório.'.format(titulo)
        for titulo in texto_da_ia.fora_da_estrutura
    )
    problemas.extend(
        'A IA escreveu uma tabela em "{}": ela ficou fora do relatório, que usa as tabelas do código.'.format(
            TITULOS_DA_IA[secao]
        )
        for secao in texto_da_ia.com_tabela
    )

    textos = _texto_que_entra(agregacao, texto_da_ia)
    tabelas = _tabelas(agregacao)
    achados = _itens(textos[ACHADOS], "A")
    hipoteses = _itens(textos[HIPOTESES], "H")
    recomendacoes = _itens(textos[RECOMENDACOES], "R")
    for itens in (achados, hipoteses, recomendacoes):
        for ident, vezes in Counter(item.ident for item in itens).items():
            if vezes > 1:
                problemas.append("{} aparece {} vezes.".format(ident, vezes))
    tema_do_achado = _conferir_achados(agregacao, tabelas, achados, problemas)
    _conferir_hipoteses(hipoteses, problemas)
    _conferir_recomendacoes(recomendacoes, problemas)

    definidos = {item.ident for item in achados + hipoteses + recomendacoes}
    for secao, texto in textos.items():
        titulo = TITULOS_DA_IA[secao]
        for letra, numero in _ID_CITADO.findall(texto):
            if letra + numero not in definidos:
                problemas.append("{} cita {}{}, que não existe.".format(titulo, letra, numero))
        for citacao in _AUDIO_CITADO.finditer(texto):
            for numero in re.findall(r"\d+", citacao.group(1)):
                audio_id = tabelas.audio_do_numero.get(int(numero))
                if audio_id is None:
                    problemas.append("{} cita Áudio {}, que não existe.".format(titulo, numero))
                elif audio_id not in tabelas.recorte:
                    problemas.append("{} cita {}, que está fora do recorte.".format(
                        titulo, agregacao.aliases[audio_id]
                    ))
        if _SESSAO_COM_TELEFONE.search(texto):
            problemas.append(
                '{} cita a sessão de um áudio, que traz o telefone do respondente: use o alias "Áudio NN".'.format(titulo)
            )
        for bruta in texto.splitlines():
            linha = _limpa_linha(bruta)
            inicio = 0
            if secao == ACHADOS:
                item = _INICIO_DE_ITEM.match(bruta)
                cabeca = _CABECA_DO_ACHADO.match(linha) if item and item.group(1) == "A" else None
                inicio = cabeca.end() if cabeca else 0
            _conferir_linha(titulo, linha, inicio, agregacao, tabelas, tema_do_achado, problemas)
    return list(dict.fromkeys(problemas))


# ---------------------------------------------------------------------------
# Etapas 4 e 5 da geração
# ---------------------------------------------------------------------------

# A chamada à IA, montada por quem chama: recebe system_prompt, user_prompt,
# temperature e max_tokens, como ai_provider.generate_analysis, e devolve o
# dict dela (text, citations, search).
GerarTexto = Callable[..., dict]


@dataclass
class Relatorio:
    """O relatório pronto para salvar e o que a tela mostra junto dele."""

    texto: str
    # A verificação das referências: aparece na tela e não impede salvar.
    problemas: List[str]
    citacoes: List[dict]
    busca: dict
    texto_da_ia: str
    sistema: str
    mensagem: str


def escrever_relatorio(
    agregacao: Agregacao,
    contexto: dict,
    recorte: Recorte,
    audios_no_projeto: int,
    taxonomia_status: Optional[str],
    gerar: GerarTexto,
    *,
    taxonomia_da_ia: bool = False,
    sinc_projeto: Optional[pd.DataFrame] = None,
    transcricao_projeto: Optional[pd.DataFrame] = None,
    perguntas_ativacao: Optional[Sequence[dict]] = None,
    analises_individuais: Optional[Dict[int, Optional[str]]] = None,
    incluir_legenda: bool = True,
) -> Relatorio:
    """Etapas 4 e 5 da geração: a IA escreve o texto, e o código monta e confere.

    Uma chamada só à IA, com a temperatura e o limite de tokens do prompt da
    Análise Geral. `contexto` traz nome, especialidade, histórico, problemas
    e briefing do projeto, e `analises_individuais`, o texto da análise
    individual mais recente de cada áudio, por audio_id. Os demais
    parâmetros são os do cabeçalho e do apêndice. Sem respondente contado,
    não há o que analisar, e a IA não é chamada; resposta sem texto também é
    erro, para não salvar um relatório oco.
    """
    if not agregacao.n_respondentes:
        raise ValueError("Nenhum respondente com fala classificada no recorte: não há o que analisar.")
    contexto = contexto or {}
    cabecalho = cabecalho_markdown(
        agregacao, str(contexto.get("nome") or ""), recorte, audios_no_projeto, taxonomia_status, taxonomia_da_ia
    )
    sumarios = sumarios_individuais(agregacao, analises_individuais or {})
    sistema = prompts.sistema_analise_geral(agregacao.tipo_projeto)
    mensagem = mensagem_para_ia(agregacao, contexto, cabecalho, sinc_projeto, sumarios)
    resposta = gerar(
        system_prompt=sistema,
        user_prompt=mensagem,
        temperature=prompts.TEMPERATURA_ANALISE_GERAL,
        max_tokens=prompts.MAX_TOKENS_ANALISE_GERAL,
    ) or {}
    texto_da_ia = str(resposta.get("text") or "")
    if not texto_da_ia.strip():
        raise RespostaInvalida("A IA não devolveu texto para a Análise Geral.")
    separado = separar_secoes(texto_da_ia)
    apendice = apendice_markdown(
        agregacao,
        sinc_projeto,
        transcricao_projeto,
        perguntas_ativacao,
        (sumarios.com, sumarios.sem),
        incluir_legenda,
        sumarios.fora_do_limite,
    )
    return Relatorio(
        texto=montar_relatorio(agregacao, cabecalho, separado, apendice),
        problemas=verificar_referencias(agregacao, separado),
        citacoes=list(resposta.get("citations") or []),
        busca=dict(resposta.get("search") or {}),
        texto_da_ia=texto_da_ia,
        sistema=sistema,
        mensagem=mensagem,
    )
