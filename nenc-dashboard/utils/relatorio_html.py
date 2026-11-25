"""
O relatório por tema em HTML (docs/SPEC_RELATORIO_POR_TEMA.md, §8).

Recebe o documento JSON (utils/relatorio_esquema.py) e devolve HTML. O mesmo
corpo serve à página do app (`corpo_html`) e ao PDF (`documento_html`, que
põe a capa e o índice na frente e leva o CSS de impressão).

Nada aqui chama IA nem lê banco, e nenhuma fala é escrita aqui: o texto, o
alias, o tempo e a máscara de cada fala já vêm prontos em `trechos`.
"""

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from utils import relatorio_graficos as graficos

RAIZ = Path(__file__).resolve().parent.parent
PASTA_TEMPLATES = RAIZ / "templates" / "relatorio"
PASTA_STATIC = RAIZ / "static"

TITULO_RECLAMACOES = "Reclamações e feedbacks negativos"
_LARGURA_MAXIMA_DA_BARRA = 150.0
_LARGURA_MINIMA_DA_BARRA = 14.0
_CABECALHO_MAXIMO = 70


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def _pct(parte: int, total: int) -> int:
    return int(math.floor(100 * parte / total + 0.5)) if total else 0


def _nota(valor: Optional[float]) -> str:
    """+0,70 e −0,80, com vírgula decimal e o sinal de menos de verdade."""
    if valor is None:
        return ""
    texto = "{:.2f}".format(abs(valor)).replace(".", ",")
    if round(valor, 2) == 0:
        return texto
    return ("+" if valor > 0 else "−") + texto


def _rotulo_polaridade(polaridade: str) -> str:
    return {"positiva": "elogio", "negativa": "crítica", "mista": "mista", "neutra": "neutra"}.get(polaridade, polaridade)


def _e(itens: List[Markup]) -> Markup:
    if not itens:
        return Markup("")
    if len(itens) == 1:
        return itens[0]
    return Markup(", ").join(itens[:-1]) + Markup(" e ") + itens[-1]


def _referencias(ids: Iterable[str], numeros: Dict[str, str]) -> Markup:
    """"1.1 e 1.2", cada um com o link para o subcapítulo."""
    return _e([Markup('<a href="#{}">{}</a>').format(destino, numeros.get(destino, destino)) for destino in ids])


def _unidades_resumidas(unidades: List[str], mostrar: int = 2) -> str:
    """Duas unidades por nome e "e mais n", para o cartão não virar lista."""
    if len(unidades) <= mostrar:
        return " · ".join(unidades)
    resto = len(unidades) - mostrar
    return "{} e mais {}".format(" · ".join(unidades[:mostrar]), resto)


def _css_string(texto: str) -> Markup:
    """O texto como conteúdo de uma string CSS: o Jinja não pode escapá-lo como HTML, que quebraria as aspas.

    `<` vira `\\3C ` para o texto nunca fechar o `<style>` onde cai.
    """
    seguro = str(texto).replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\3C ").replace("\n", " ")
    return Markup(seguro)


def _grade(voz: Dict[str, Any]) -> Markup:
    return graficos.grade_voz(
        bool(voz.get("avaliavel")), voz.get("direcao", ""), voz.get("nivel", ""), voz.get("respondentes_com_voz", 0)
    )


def _ambiente() -> Environment:
    ambiente = Environment(
        loader=FileSystemLoader(str(PASTA_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    ambiente.filters.update({
        "pct": _pct,
        "nota": _nota,
        "rotulo_polaridade": _rotulo_polaridade,
        "polaridades_em_palavras": graficos.texto_polaridades,
        "barra_polaridade": graficos.barra_polaridade,
        "medidor": graficos.medidor,
        "referencias": _referencias,
        "referencias_de_capitulo": _referencias,
        "unidades_resumidas": _unidades_resumidas,
        "css_string": _css_string,
    })
    ambiente.globals["grade_voz"] = _grade
    return ambiente


_AMBIENTE = _ambiente()


# ---------------------------------------------------------------------------
# Contexto: o que o template precisa e o JSON não traz pronto
# ---------------------------------------------------------------------------

def _secoes(relatorio: Dict[str, Any]) -> Dict[str, Any]:
    """Numeração das seções depois dos capítulos: n+1 e n+2 (spec §3.1).

    "Leituras transversais e Matriz" só existe se houver uma das duas coisas;
    sem nenhuma, a seção some e as reclamações passam a n+1.
    """
    n = len(relatorio["capitulos"])
    tem_transversais = bool(relatorio["transversais"])
    tem_matriz = bool(relatorio["matriz"].get("avaliavel"))
    proximo = n + 1
    transversais = None
    if tem_transversais or tem_matriz:
        titulo = ("Leituras transversais e Matriz de Destaques" if tem_transversais and tem_matriz
                  else "Leituras transversais" if tem_transversais else "Matriz de Destaques")
        transversais = {"numero": proximo, "titulo": titulo, "tem_transversais": tem_transversais, "tem_matriz": tem_matriz}
        proximo += 1
    return {"transversais": transversais, "reclamacoes": {"numero": proximo, "titulo": TITULO_RECLAMACOES}}


def _visao_geral(relatorio: Dict[str, Any]) -> List[Dict[str, Any]]:
    """As linhas da visão geral, com a largura da barra proporcional aos respondentes."""
    linhas = relatorio["visao_geral"]
    maior = max([linha["respondentes"] for linha in linhas] or [1]) or 1
    return [
        dict(linha, largura=max(_LARGURA_MINIMA_DA_BARRA, _LARGURA_MAXIMA_DA_BARRA * linha["respondentes"] / maior))
        for linha in linhas
    ]


def _destaques(relatorio: Dict[str, Any], quantos: int = 3) -> List[Dict[str, Any]]:
    """Os temas mais citados para a capa: os da taxonomia, sem o capítulo Outros, com barra na mesma escala."""
    tipos = {capitulo["id"]: capitulo["tipo"] for capitulo in relatorio["capitulos"]}
    linhas = [linha for linha in _visao_geral(relatorio) if tipos.get(linha["capitulo"]) != "outros"]
    return sorted(linhas, key=lambda linha: -linha["respondentes"])[:quantos]


def _indice(relatorio: Dict[str, Any], secoes: Dict[str, Any]) -> List[Dict[str, Any]]:
    entradas = [
        {"nivel": 0, "numero": "", "rotulo": "Resumo executivo", "ancora": "resumo"},
        {"nivel": 0, "numero": "", "rotulo": "Visão geral dos temas", "ancora": "visao-geral"},
    ]
    for capitulo in relatorio["capitulos"]:
        entradas.append({"nivel": 1, "numero": capitulo["numero"], "rotulo": capitulo["tema"], "ancora": capitulo["id"]})
        for sub in capitulo.get("subcapitulos", []):
            entradas.append({"nivel": 2, "numero": sub["numero"], "rotulo": sub["nome"], "ancora": sub["id"]})
    if secoes["transversais"]:
        entradas.append({"nivel": 1, "numero": secoes["transversais"]["numero"],
                         "rotulo": secoes["transversais"]["titulo"], "ancora": "transversais"})
    entradas.append({"nivel": 1, "numero": secoes["reclamacoes"]["numero"],
                     "rotulo": secoes["reclamacoes"]["titulo"], "ancora": "reclamacoes"})
    entradas.append({"nivel": 0, "numero": "", "rotulo": "Notas metodológicas", "ancora": "notas"})
    return entradas


def _contexto(relatorio: Dict[str, Any]) -> Dict[str, Any]:
    secoes = _secoes(relatorio)
    numeros: Dict[str, str] = {}
    origens: Dict[str, str] = {}
    numeros_de_capitulo: Dict[str, str] = {}
    for capitulo in relatorio["capitulos"]:
        numeros_de_capitulo[capitulo["id"]] = str(capitulo["numero"])
        origens[str(capitulo["numero"])] = capitulo["id"]
        for sub in capitulo.get("subcapitulos", []):
            numeros[sub["id"]] = sub["numero"]
            origens[sub["numero"]] = sub["id"]
    cabecalho = relatorio["cabecalho"]
    return {
        "cabecalho": cabecalho,
        "alertas": relatorio["alertas"],
        "resumo": relatorio["resumo"],
        "termometro": relatorio["termometro"],
        "como_ler": relatorio["como_ler"],
        "visao_geral": _visao_geral(relatorio),
        "sem_mencao": relatorio["sem_mencao"],
        "capitulos": relatorio["capitulos"],
        "trechos": relatorio["trechos"],
        "transversais": relatorio["transversais"],
        "matriz": relatorio["matriz"],
        "reclamacoes": relatorio["reclamacoes"],
        "notas": relatorio["notas"],
        "anexo": relatorio.get("anexo") or {},
        "exploratoria": bool(cabecalho.get("exploratoria")),
        "unidades_no_recorte": cabecalho.get("unidades") or 0,
        "secao_transversais": secoes["transversais"],
        "secao_reclamacoes": secoes["reclamacoes"],
        "numeros": numeros,
        "numeros_de_capitulo": numeros_de_capitulo,
        "origens": origens,
        "indice": _indice(relatorio, secoes),
        "destaques": _destaques(relatorio),
    }


# ---------------------------------------------------------------------------
# Saídas
# ---------------------------------------------------------------------------

def _ler(nome: str) -> str:
    return (PASTA_STATIC / nome).read_text(encoding="utf-8")


def _corpo(relatorio: Dict[str, Any]) -> Markup:
    return Markup(_AMBIENTE.get_template("corpo.html").render(**_contexto(relatorio)))


def corpo_html(relatorio: Dict[str, Any], com_css: bool = True) -> Markup:
    """O relatório sem capa nem índice, para a página do app.

    O CSS de componentes vai junto, como `<style>`, porque a página recebe um
    pedaço de HTML e não tem folha de estilo própria.
    """
    corpo = _corpo(relatorio)
    if not com_css:
        return corpo
    return Markup("<style>{}</style>").format(Markup(_ler("relatorio.css"))) + corpo


def _cabecalho_direita(cabecalho: Dict[str, Any]) -> str:
    texto = "{} · {}".format(cabecalho.get("projeto", ""), cabecalho.get("recorte", ""))
    return texto if len(texto) <= _CABECALHO_MAXIMO else texto[: _CABECALHO_MAXIMO - 1].rstrip() + "…"


def documento_html(relatorio: Dict[str, Any]) -> str:
    """O documento inteiro para virar PDF: capa, índice e o corpo, com o CSS de impressão."""
    contexto = _contexto(relatorio)
    contexto["css"] = _ler("relatorio.css") + "\n" + _ler("relatorio_pdf.css")
    contexto["cabecalho_direita"] = _cabecalho_direita(relatorio["cabecalho"])
    contexto["corpo"] = _corpo(relatorio)
    return _AMBIENTE.get_template("documento.html").render(**contexto)
