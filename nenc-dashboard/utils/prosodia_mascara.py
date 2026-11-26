"""
Máscara de nomes e dados pessoais nas falas citadas no relatório
(docs/SPEC_RELATORIO_POR_TEMA.md, §6).

Roda sobre o texto literal da fala depois de escolhida, nunca sobre o texto da
IA. Na fala crítica, nome de pessoa vira "[funcionário]" (quando o texto diz
que a pessoa é da unidade) ou "[pessoa]"; em toda fala, valor, cartão, CPF,
telefone, e-mail e banco viram "[valor]", "[dado pessoal]" e "[banco]". A fala
de elogio mantém o nome (`nomes=False`).

Pessoas saem de duas fontes: o reconhecimento de entidades do
`pt_core_news_sm` e a palavra com inicial maiúscula logo depois de "o/a",
"seu/dona", "professor", "gerente" e afins. O que está em `preservar` (marca,
projeto, unidades, rótulos da taxonomia) nunca se mascara. Na dúvida entre
pessoa e marca, mascara: o modelo pequeno às vezes dá nome de gente como
"MISC", e por isso MISC com cara de nome próprio também entra. Sem o modelo
instalado, vale só a heurística, mais larga: toda palavra com inicial
maiúscula fora do começo da frase.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable, List, Optional, Sequence, Set, Tuple

PESSOA = "[pessoa]"
FUNCIONARIO = "[funcionário]"
VALOR = "[valor]"
DADO_PESSOAL = "[dado pessoal]"
BANCO = "[banco]"

_MARCAS = (PESSOA, FUNCIONARIO, VALOR, DADO_PESSOAL, BANCO)

# ---------------------------------------------------------------------------
# Dados financeiros e de contato
# ---------------------------------------------------------------------------

_NUMERO_POR_EXTENSO = (
    r"(?:um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quatorze|catorze|"
    r"quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|sessenta|setenta|"
    r"oitenta|noventa|cem|cento|duzentos|trezentos|quatrocentos|quinhentos|seiscentos|setecentos|"
    r"oitocentos|novecentos|mil)"
)
_NUMERO = r"(?:\d+(?:\.\d{3})*(?:,\d+)?|" + _NUMERO_POR_EXTENSO + r")"

_DADOS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE), DADO_PESSOAL),
    (re.compile(r"\bR\$\s*\d+(?:\.\d{3})*(?:,\d{1,2})?"), VALOR),
    (
        re.compile(
            r"\b" + _NUMERO + r"(?:(?:\s+e\s+|\s+|-)" + _NUMERO + r")*\s+(?:reais|real|conto|contos|pila)\b",
            re.IGNORECASE,
        ),
        VALOR,
    ),
    # CPF antes do telefone: os dois são 11 dígitos.
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-?\d{2}\b"), DADO_PESSOAL),
    # Cartão: 13 a 19 dígitos, com ou sem separador.
    (re.compile(r"\b\d(?:[ .-]?\d){12,18}\b"), DADO_PESSOAL),
    # Telefone: 8 dígitos ou mais, com DDD e o 9 opcionais.
    (re.compile(r"(?:\+?55[\s-]?)?(?:\(?\d{2}\)?[\s-]?)?(?:9[\s.-]?)?\d{4}[\s.-]?\d{4}\b"), DADO_PESSOAL),
    (re.compile(r"\b\d{11}\b"), DADO_PESSOAL),
    (
        re.compile(
            r"\b(?:Ita[uú](?:\s+Unibanco)?|Bradesco|Santander|Caixa\s+Econ[oô]mica(?:\s+Federal)?|"
            r"Banco\s+do\s+Brasil|Nubank|Nu\s+Bank|Banco\s+Inter|C6\s+Bank|PicPay|Mercado\s+Pago|Sicredi|"
            r"Sicoob|BTG(?:\s+Pactual)?|Banco\s+Original|Neon|PagBank|PagSeguro|Banrisul)\b"
        ),
        BANCO,
    ),
)


def _mascarar_dados(texto: str) -> str:
    for padrao, marca in _DADOS:
        texto = padrao.sub(marca, texto)
    return texto


# ---------------------------------------------------------------------------
# Pessoas
# ---------------------------------------------------------------------------

# Depois destas palavras, a palavra com inicial maiúscula é nome de gente.
_GATILHOS = frozenset({
    "o", "a", "da", "do", "pela", "pelo", "seu", "dona", "sr", "sra", "senhor", "senhora", "professor", "professora", "prof",
    "gerente", "atendente", "recepcionista", "instrutor", "instrutora", "personal", "coordenador",
    "coordenadora", "funcionário", "funcionária", "funcionario", "funcionaria", "moço", "moça",
    "menino", "menina", "rapaz", "tio", "tia", "supervisor", "supervisora", "estagiário", "estagiária",
})
# Perto do nome, estas dizem que a pessoa é da unidade.
_PAPEIS = frozenset({
    "professor", "professora", "prof", "professores", "gerente", "atendente", "recepcionista",
    "recepção", "recepcao", "instrutor", "instrutora", "personal", "coordenador", "coordenadora",
    "funcionário", "funcionária", "funcionario", "funcionaria", "equipe", "limpeza", "supervisor",
    "supervisora", "estagiário", "estagiária", "líder", "lider", "treinador", "treinadora", "staff",
})
# Palavras com inicial maiúscula que não são nome: a heurística não as mascara.
_NAO_SAO_NOME = frozenset({
    "eu", "ele", "ela", "eles", "elas", "você", "voce", "vocês", "a", "o", "os", "as", "um", "uma",
    "deus", "nossa", "senhor", "senhora", "sr", "sra", "dona", "seu", "não", "nao", "sim", "obrigado",
    "obrigada", "bom", "boa", "olá", "ola", "oi", "tudo", "muito", "mas", "então", "entao", "também",
    "tambem", "academia", "app", "aplicativo", "whatsapp", "instagram", "google", "pix", "qr", "code",
    "brasil", "são", "sao", "santo", "santa", "wi-fi", "wifi", "internet", "tv", "youtube", "spotify",
    "netflix", "android", "iphone", "totalpass", "gympass", "wellhub", "bluetooth",
})
_CONECTORES = frozenset({"da", "de", "do", "das", "dos", "e"})

_PALAVRA = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-][^\W\d_])*", re.UNICODE)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def _maiuscula(palavra: str) -> bool:
    return palavra[:1].isupper() and not palavra.isupper() or (len(palavra) > 1 and palavra.istitle())


@lru_cache(maxsize=1)
def _modelo():
    """O pt_core_news_sm com o reconhecimento de entidades, ou None sem ele."""
    try:
        import spacy

        return spacy.load("pt_core_news_sm", disable=["parser", "lemmatizer"])
    except Exception:  # noqa: BLE001 - sem o modelo, vale a heurística mais larga
        return None


def modelo_disponivel() -> bool:
    return _modelo() is not None


def _trechos_preservados(texto: str, preservar: Sequence[str]) -> List[Tuple[int, int]]:
    """Onde, no texto, aparece algo de `preservar`, sem diferenciar caixa, acento e espaço."""
    if not preservar:
        return []
    # Comparação caractere a caractere: o texto sem acento tem o mesmo tamanho
    # do original quando cada letra acentuada vira uma letra só.
    base = "".join(_sem_acento(c) or c for c in texto)
    if len(base) != len(texto):
        base = texto.casefold()
    trechos = []
    for termo in sorted({t for t in preservar if t and t.strip()}, key=len, reverse=True):
        partes = [re.escape(_sem_acento(p)) for p in termo.split()]
        padrao = re.compile(r"(?<!\w)" + r"\s*".join(partes) + r"(?!\w)")
        trechos.extend((m.start(), m.end()) for m in padrao.finditer(base))
    return trechos


def _dentro(inicio: int, fim: int, trechos: Iterable[Tuple[int, int]]) -> bool:
    return any(inicio < f and fim > i for i, f in trechos)


# A transcrição erra o nome da marca ("Smartfish" por "Smart Fit"): palavra longa
# quase igual a um termo preservado também se preserva.
_MIN_LETRAS_APROXIMADO = 6
_SEMELHANCA_MINIMA = 0.8


def _aproximados(palavras: Sequence[Tuple[int, int, str]], preservar: Sequence[str]) -> List[Tuple[int, int]]:
    # Só termos compostos ("Smart Fit", "Caxias Shopping"): nome de gente parecido
    # com uma palavra solta da unidade ("Teresa" e "Teresina") continua mascarado.
    alvos = {
        _sem_acento(termo.replace(" ", ""))
        for termo in preservar
        if (" " in termo.strip() or re.search(r"[a-zà-ú][A-Z]", termo))
        and len(termo.replace(" ", "")) >= _MIN_LETRAS_APROXIMADO
        and not termo.startswith("[")
    }
    if not alvos:
        return []
    trechos = []
    for inicio, fim, palavra in palavras:
        forma = _sem_acento(palavra)
        if len(forma) < _MIN_LETRAS_APROXIMADO:
            continue
        if any(SequenceMatcher(None, forma, alvo).ratio() >= _SEMELHANCA_MINIMA for alvo in alvos):
            trechos.append((inicio, fim))
    return trechos


def _da_unidade(palavras: Sequence[Tuple[int, int, str]], indice: int, fim: int) -> bool:
    """O nome nas palavras[indice:fim] tem um papel da unidade por perto."""
    perto = palavras[max(0, indice - 4) : indice] + palavras[fim : fim + 3]
    return any(_sem_acento(p) in {_sem_acento(x) for x in _PAPEIS} for _, _, p in perto)


def _nomes_pela_heuristica(
    texto: str, palavras: Sequence[Tuple[int, int, str]], preservados, larga: bool
) -> Set[int]:
    """Índices das palavras que a heurística toma por nome de gente."""
    marcadas: Set[int] = set()
    inicio_de_frase = {0}
    for i in range(1, len(palavras)):
        entre = texto[palavras[i - 1][1] : palavras[i][0]]
        if re.search(r"[.!?…]", entre):
            inicio_de_frase.add(i)
    for i, (ini, fim, palavra) in enumerate(palavras):
        if not _maiuscula(palavra) or _sem_acento(palavra) in _NAO_SAO_NOME:
            continue
        if _dentro(ini, fim, preservados):
            continue
        anterior = palavras[i - 1][2].rstrip(".").casefold() if i else ""
        if anterior in _GATILHOS or (larga and i not in inicio_de_frase):
            marcadas.add(i)
    # O sobrenome e o "da Silva" que vêm colados ao nome marcado.
    for i in sorted(marcadas):
        j = i + 1
        while j < len(palavras):
            palavra = palavras[j][2]
            if palavra.casefold() in _CONECTORES and j + 1 < len(palavras) and _maiuscula(palavras[j + 1][2]):
                j += 1
                continue
            if _maiuscula(palavra) and not _dentro(palavras[j][0], palavras[j][1], preservados):
                if palavras[j - 1][2].casefold() in _CONECTORES:
                    marcadas.add(j - 1)
                marcadas.add(j)
                j += 1
                continue
            break
    return marcadas


def _nomes_pelo_modelo(
    texto: str, palavras: Sequence[Tuple[int, int, str]], preservados
) -> Set[int]:
    """Índices das palavras dentro de uma entidade de pessoa (ou MISC com cara de nome)."""
    nlp = _modelo()
    if nlp is None:
        return set()
    doc = nlp(texto)
    marcadas: Set[int] = set()
    for entidade in doc.ents:
        if entidade.label_ not in ("PER", "MISC"):
            continue
        if entidade.label_ == "MISC" and not all(
            t.is_alpha and _maiuscula(t.text) and t.pos_ == "PROPN" for t in entidade if not t.is_punct
        ):
            continue
        for i, (ini, fim, palavra) in enumerate(palavras):
            if ini >= entidade.start_char and fim <= entidade.end_char:
                if not _maiuscula(palavra) or _sem_acento(palavra) in _NAO_SAO_NOME:
                    continue
                if _dentro(ini, fim, preservados):
                    continue
                marcadas.add(i)
    return marcadas


def _mascarar_nomes(texto: str, preservar: Sequence[str]) -> str:
    palavras = [(m.start(), m.end(), m.group()) for m in _PALAVRA.finditer(texto)]
    if not palavras:
        return texto
    preservados = _trechos_preservados(texto, preservar) + _aproximados(palavras, preservar)
    larga = _modelo() is None
    marcadas = _nomes_pela_heuristica(texto, palavras, preservados, larga) | _nomes_pelo_modelo(
        texto, palavras, preservados
    )
    if not marcadas:
        return texto
    # Palavras marcadas em sequência (com "da/de" no meio) viram uma marca só.
    blocos: List[Tuple[int, int]] = []
    for i in sorted(marcadas):
        if blocos and i == blocos[-1][1] + 1:
            blocos[-1] = (blocos[-1][0], i)
        elif blocos and i == blocos[-1][1] + 2 and palavras[i - 1][2].casefold() in _CONECTORES:
            blocos[-1] = (blocos[-1][0], i)
        else:
            blocos.append((i, i))
    partes, cursor = [], 0
    for primeiro, ultimo in blocos:
        inicio, fim = palavras[primeiro][0], palavras[ultimo][1]
        marca = FUNCIONARIO if _da_unidade(palavras, primeiro, ultimo + 1) else PESSOA
        partes.append(texto[cursor:inicio])
        partes.append(marca)
        cursor = fim
    partes.append(texto[cursor:])
    return "".join(partes)


def mascarar_fala(texto: Optional[str], preservar: Sequence[str] = (), nomes: bool = True) -> str:
    """A fala com nomes e dados pessoais trocados por marcas.

    `preservar` é o que nunca se mascara (preservar_do_projeto). `nomes=False`
    é a fala de elogio: ela mantém o nome da pessoa e perde só os dados.
    """
    texto = str(texto or "")
    if not texto.strip():
        return texto
    texto = _mascarar_dados(texto)
    if nomes:
        texto = _mascarar_nomes(texto, list(preservar) + list(_MARCAS))
    return texto


_CODIGO_DE_UNIDADE = re.compile(r"^[A-Z]{2,}[A-Z0-9-]*\d[A-Z0-9-]*$")


def preservar_do_projeto(
    nome_projeto: str = "", unidades: Iterable[str] = (), rotulos: Iterable[str] = (), outros: Iterable[str] = ()
) -> List[str]:
    """O que a máscara nunca troca: marca e projeto, unidades e rótulos da taxonomia.

    O rótulo de unidade ("SBRPICPRI01 Atacadão Teresina Primavera", "Moema ·
    LJ-MOE-01") entra inteiro, sem o código, e palavra por palavra, porque a
    fala costuma citar só o bairro ("aqui no Itapoã"). O nome do projeto
    entra também sem espaço ("Smart Fit" e "SmartFit").
    """
    termos: Set[str] = set()

    def acrescentar(texto: str, por_palavra: bool) -> None:
        texto = " ".join(str(texto or "").replace("·", " ").replace("—", " ").split())
        if not texto:
            return
        palavras = [p for p in texto.split() if not _CODIGO_DE_UNIDADE.match(p)]
        if palavras:
            termos.add(" ".join(palavras))
        if por_palavra:
            termos.update(p for p in palavras if len(p) >= 3 and p.casefold() not in _CONECTORES)

    for parte in re.split(r"[—–:|]", nome_projeto or ""):
        acrescentar(parte, por_palavra=False)
        # Marca de duas palavras ("Smart Fit") também sem o espaço ("SmartFit").
        palavras = parte.split()
        for i in range(len(palavras) - 1):
            if _maiuscula(palavras[i]) and palavras[i + 1][:1].isupper():
                termos.add(palavras[i] + " " + palavras[i + 1])
                termos.add(palavras[i] + palavras[i + 1])
    for unidade in unidades:
        acrescentar(unidade, por_palavra=True)
    for rotulo in rotulos:
        for parte in str(rotulo or "").split(">"):
            acrescentar(parte, por_palavra=False)
    for termo in outros:
        acrescentar(termo, por_palavra=False)
    return sorted(termos)
