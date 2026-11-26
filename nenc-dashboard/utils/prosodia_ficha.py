"""
A ficha de contexto de cada áudio (docs/SPEC_RELATORIO_POR_TEMA.md, §10; Fase 4).

A análise individual rápida devolve uma ficha em markdown com títulos fixos
(prosodia_prompts.SECOES_DA_FICHA). Este módulo a reconhece, põe o rótulo de
"interpretação prévia", troca "Temas tocados" pela classificação já gravada
do áudio quando ela existe (Pesquisa de Opinião), e a prepara para a Análise
Geral, que a recebe inteira no lugar do Sumário Executivo.

Só a biblioteca padrão: prosodia_relatorio e as páginas importam daqui.
"""

import re
from typing import Dict, Iterable, List, Optional

from utils.prosodia_prompts import ROTULO_DA_FICHA, SECOES_DA_FICHA, TITULO_DA_FICHA

_TITULO = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$")
_MAX_NA_GERAL = 1600
_SEM_DIZER = re.compile(r"^\s*n[ãa]o disse\.?\s*$", re.IGNORECASE)


def _chave(texto: str) -> str:
    import unicodedata

    decomposto = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold().strip(" :*")


_CHAVES = {_chave(s): s for s in SECOES_DA_FICHA}


def eh_ficha(texto: Optional[str]) -> bool:
    """O texto é uma ficha de contexto (tem o título "Ficha de contexto")."""
    return any(
        (m := _TITULO.match(linha)) and _chave(m.group(1)) == _chave(TITULO_DA_FICHA)
        for linha in str(texto or "").splitlines()
    )


def secoes(texto: Optional[str]) -> Dict[str, str]:
    """{seção: conteúdo} das seções fixas da ficha, na ordem do texto; as que faltam ficam de fora."""
    resultado: Dict[str, List[str]] = {}
    atual: Optional[str] = None
    for linha in str(texto or "").splitlines():
        titulo = _TITULO.match(linha)
        if titulo:
            atual = _CHAVES.get(_chave(titulo.group(1)))
            if atual:
                resultado.setdefault(atual, [])
            continue
        if atual:
            resultado[atual].append(linha)
    return {secao: "\n".join(linhas).strip() for secao, linhas in resultado.items()}


def temas_do_codigo(classificacao: Optional[dict], alertas: Iterable[str] = ()) -> str:
    """"Temas tocados" montados da classificação gravada em audio_temas, sem a IA.

    Um item por menção: "- Tag > subtema (polaridade): evidência". A linha de
    alerta crítico entra só como tema, sem a evidência, que pode trazer o caso.
    """
    alertas = set(alertas)
    itens = []
    for tema in (classificacao or {}).get("temas") or []:
        if not isinstance(tema, dict) or not tema.get("subtema"):
            continue
        rotulo = str(tema["subtema"])
        if tema.get("detalhe"):
            rotulo += " > " + " ".join(str(tema["detalhe"]).split())
        polaridade = tema.get("polaridade") or "neutra"
        evidencia = "" if tema["subtema"] in alertas else " ".join(str(tema.get("evidencia") or "").split())
        itens.append("- {} ({}){}".format(rotulo, polaridade, ": " + evidencia if evidencia else ""))
    return "\n".join(itens)


def montar_ficha(texto_da_ia: str, temas: str = "") -> str:
    """A ficha como fica gravada: títulos fixos, o rótulo e, se houver, os temas do código.

    `temas` (de temas_do_codigo) substitui a seção que a IA escreveu: a
    classificação já é a contagem da Geral. Seções que a IA não escreveu
    ficam com "Não disse.".
    """
    lidas = secoes(texto_da_ia)
    if temas.strip():
        lidas["Temas tocados"] = temas.strip()
    partes = ["## " + TITULO_DA_FICHA, "*" + ROTULO_DA_FICHA + "*"]
    for secao in SECOES_DA_FICHA:
        partes.append("### " + secao)
        partes.append(lidas.get(secao) or "Não disse.")
    return "\n\n".join(partes) + "\n"


def ficha_da_resposta(texto_da_ia: Optional[str]) -> str:
    """A resposta da análise rápida como ficha gravável; vazia quando a IA não respondeu.

    Para as telas que geram em lote (Áudios, Entrevistas, monitor do
    WhatsApp): elas não montam os temas da classificação, e a IA os escreve.
    """
    return montar_ficha(texto_da_ia) if str(texto_da_ia or "").strip() else ""


def situacao(texto: Optional[str]) -> str:
    """A primeira seção, para o resumo curto da página do áudio."""
    return " ".join(secoes(texto).get("Situação", "").split())


def para_a_geral(texto: Optional[str], limite: int = _MAX_NA_GERAL) -> Optional[str]:
    """A ficha compacta para a Análise Geral: uma linha por seção, sem as vazias e sem o rótulo.

    None quando o texto não é ficha (a Geral cai no Sumário Executivo).
    """
    if not eh_ficha(texto):
        return None
    linhas = []
    for secao, conteudo in secoes(texto).items():
        conteudo = conteudo.strip()
        if not conteudo or _SEM_DIZER.match(conteudo):
            continue
        if secao == "Temas tocados":
            linhas.append("**{}:**\n{}".format(secao, conteudo))
        else:
            linhas.append("**{}:** {}".format(secao, " ".join(conteudo.split())))
    compacta = "\n".join(linhas).strip()
    if len(compacta) > limite:
        compacta = compacta[:limite].rsplit(" ", 1)[0] + "…"
    return compacta or None
