"""
Taxonomia de temas da Análise Geral do NencBoost.

Cada projeto cadastra a sua na Preparação, um subtema por linha:

    Categoria > Subtema: definição opcional

A Análise Geral classifica as falas de cada áudio nesses subtemas e conta
quantos respondentes trataram de cada um; o que não couber vira tema
emergente. O módulo não depende do banco, como prosodia_project_types:
prosodia_db importa daqui os status, e os testes rodam sem SQLite.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

TAXONOMIA_PROVISORIA = "provisoria"
TAXONOMIA_VALIDADA = "validada"

# Provisória é a taxonomia ainda sem o aval da cliente, escrita pela equipe ou
# proposta pela IA; enquanto for, o relatório avisa no cabeçalho.
TAXONOMIA_STATUS_LABELS = {
    TAXONOMIA_PROVISORIA: "Provisória, a validar com a cliente",
    TAXONOMIA_VALIDADA: "Validada com a cliente",
}

# Marcadores de lista e numeração que vêm junto quando a taxonomia é colada de
# um documento: "- ", "* ", "• ", "1. ", "2) ".
_MARCADOR = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass(frozen=True)
class Subtema:
    categoria: str
    nome: str
    definicao: str = ""

    @property
    def rotulo(self) -> str:
        return "{} > {}".format(self.categoria, self.nome)


@dataclass(frozen=True)
class Taxonomia:
    """Subtemas válidos, na ordem do texto, e as linhas que ficaram de fora."""

    subtemas: Tuple[Subtema, ...] = ()
    # Linhas sem "Categoria > Subtema", com mais de dois níveis ou com uma das
    # partes vazia.
    linhas_invalidas: Tuple[str, ...] = ()
    # Subtemas repetidos, sem diferenciar caixa nem acento: vale o primeiro.
    repetidos: Tuple[str, ...] = ()

    def categorias(self) -> Dict[str, List[Subtema]]:
        """Subtemas agrupados por categoria, na ordem em que aparecem."""
        grupos: Dict[str, List[Subtema]] = {}
        for subtema in self.subtemas:
            grupos.setdefault(subtema.categoria, []).append(subtema)
        return grupos


def _limpa(texto: str) -> str:
    return " ".join(str(texto or "").split())


def _chave(texto: str) -> str:
    """Forma de comparação: "Lotação" e "lotacao" são o mesmo subtema."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def parse_taxonomia(texto: Optional[str]) -> Taxonomia:
    """Lê o campo de taxonomia do projeto.

    A definição começa no primeiro dois-pontos, e categoria e subtema se
    separam pelo primeiro ">". Linha em branco é ignorada; linha fora do
    formato não derruba as demais e vai para `linhas_invalidas`, para a tela
    mostrar. A categoria fica com a grafia da primeira vez em que aparece.
    """
    subtemas: List[Subtema] = []
    invalidas: List[str] = []
    repetidos: List[str] = []
    grafia_da_categoria: Dict[str, str] = {}
    vistos = set()
    for bruta in str(texto or "").splitlines():
        linha = _MARCADOR.sub("", bruta).strip()
        if not linha:
            continue
        rotulo, _, definicao = linha.partition(":")
        categoria, separador, nome = rotulo.partition(">")
        categoria, nome = _limpa(categoria), _limpa(nome)
        if not separador or not categoria or not nome or ">" in nome:
            invalidas.append(_limpa(bruta))
            continue
        categoria = grafia_da_categoria.setdefault(_chave(categoria), categoria)
        subtema = Subtema(categoria, nome, _limpa(definicao))
        chave = (_chave(categoria), _chave(nome))
        if chave in vistos:
            repetidos.append(subtema.rotulo)
            continue
        vistos.add(chave)
        subtemas.append(subtema)
    return Taxonomia(tuple(subtemas), tuple(invalidas), tuple(repetidos))
