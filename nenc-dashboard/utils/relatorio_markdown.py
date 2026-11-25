"""
Markdown do texto v2 e das análises antigas em HTML (docs/SPEC_RELATORIO_POR_TEMA.md, §9).

A Análise Geral dos projetos sem tags abertas segue em markdown, e as análises
anteriores à spec também. Elas saem pelo mesmo PDF novo: este módulo converte o
texto, separa o "Apêndice técnico", que vira o Anexo técnico, e devolve os
títulos de seção para o índice.

O texto vem da IA e do código: o HTML cru é descartado, não repassado.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup

# "### Apêndice técnico" e variações de nível, de caixa e de acento.
_TITULO_DO_APENDICE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*ap[êe]ndice[ \t]+t[ée]cnico[ \t]*#*[ \t]*$", re.IGNORECASE | re.MULTILINE)
_MARCA_SOLTA = re.compile(r"\*\*|__")
_AUDIO_E_NUMERO = re.compile(r"\b([ÁáAa]udios?) (\d)")
_COLUNAS_DA_TABELA_LARGA = 8
_COLUNAS_DA_TABELA_ESTREITA = 4


@dataclass(frozen=True)
class Titulo:
    """Um título do nível mais alto do texto, com a âncora que o índice usa."""

    ancora: str
    texto: str


@dataclass(frozen=True)
class Convertido:
    html: Markup
    titulos: Tuple[Titulo, ...]


def separar_anexo(texto: Optional[str]) -> Tuple[str, str]:
    """(corpo, anexo): o texto antes e o depois do título "Apêndice técnico".

    Sem o título, o anexo vem vazio e o corpo é o texto inteiro. O título em si
    não fica em nenhuma das partes; os níveis dos títulos do anexo seguem como
    estão, e a conversão os reajusta.
    """
    texto = str(texto or "")
    achado = _TITULO_DO_APENDICE.search(texto)
    if not achado:
        return texto.strip(), ""
    return texto[: achado.start()].strip(), texto[achado.end():].strip()


def _novo() -> MarkdownIt:
    # "commonmark" com html desligado: o texto da IA não injeta marcação. As tabelas não vêm no preset.
    return MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def _texto_do_titulo(inline: Token) -> str:
    partes = [filho.content for filho in inline.children or [] if filho.type in ("text", "code_inline")]
    return " ".join("".join(partes).split())


def _ajustar_titulos(tokens: List[Token], nivel_do_topo: int, prefixo: str) -> List[Titulo]:
    """Põe o título mais alto do texto em `nivel_do_topo` e numera as âncoras dele.

    Os demais níveis acompanham, sem passar de h6. Devolve os títulos do topo,
    que o índice do PDF lista.
    """
    niveis = [int(t.tag[1]) for t in tokens if t.type == "heading_open"]
    if not niveis:
        return []
    deslocamento = nivel_do_topo - min(niveis)
    titulos: List[Titulo] = []
    for posicao, token in enumerate(tokens):
        if token.type not in ("heading_open", "heading_close"):
            continue
        nivel = int(token.tag[1])
        token.tag = "h{}".format(min(6, max(1, nivel + deslocamento)))
        if token.type == "heading_open" and nivel + deslocamento == nivel_do_topo and posicao + 1 < len(tokens):
            ancora = "{}{}".format(prefixo, len(titulos) + 1)
            token.attrSet("id", ancora)
            titulos.append(Titulo(ancora, _texto_do_titulo(tokens[posicao + 1])))
    return titulos


def _ajustar_textos(tokens: List[Token]) -> None:
    """Tira o `**` e o `__` que sobraram sem par (a IA fecha o negrito na célula errada).

    E prende "Áudio" ao número, para a coluna estreita do anexo não quebrar "Áudio / 02".
    """
    for token in tokens:
        for filho in token.children or []:
            if filho.type != "text":
                continue
            filho.content = _AUDIO_E_NUMERO.sub("\\1\u00a0\\2", _MARCA_SOLTA.sub("", filho.content))


def _classificar_tabelas(tokens: List[Token]) -> None:
    """Dá a cada tabela uma classe pelo número de colunas, para o CSS ajustar a fonte."""
    for posicao, token in enumerate(tokens):
        if token.type != "table_open":
            continue
        colunas = 0
        for seguinte in tokens[posicao + 1:]:
            if seguinte.type == "th_open":
                colunas += 1
            elif seguinte.type == "tr_close":
                break
        tamanho = (
            "larga" if colunas >= _COLUNAS_DA_TABELA_LARGA
            else "estreita" if colunas <= _COLUNAS_DA_TABELA_ESTREITA
            else "media"
        )
        token.attrSet("class", "tabela tabela--{}".format(tamanho))


def converter(texto: Optional[str], nivel_do_topo: int = 2, prefixo_das_ancoras: str = "s") -> Convertido:
    """O markdown em HTML, com o título mais alto no nível `nivel_do_topo` (h2 por padrão)."""
    md = _novo()
    tokens = md.parse(str(texto or ""))
    titulos = _ajustar_titulos(tokens, nivel_do_topo, prefixo_das_ancoras)
    _ajustar_textos(tokens)
    _classificar_tabelas(tokens)
    return Convertido(Markup(md.renderer.render(tokens, md.options, {})), tuple(titulos))
