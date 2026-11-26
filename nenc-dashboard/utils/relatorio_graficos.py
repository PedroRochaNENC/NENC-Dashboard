"""
Gráficos do relatório por tema, em SVG puro (docs/SPEC_RELATORIO_POR_TEMA.md, §8.3).

Cada função devolve um `Markup` pronto para ir inline no HTML: o mesmo SVG
serve ao PDF (WeasyPrint) e à página do app. Nada aqui lê dados de fora: o
que entra é o que está no JSON do relatório.

Regra de acessibilidade da spec: toda cor leva rótulo ou ícone, e o PDF tem de
continuar legível em escala de cinza. Por isso as barras trazem a ordem fixa
(elogio, mista, neutra, crítica) e a legenda com a contagem em palavras, e a
mista e a neutra têm hachura e tom próprios, e não só uma cor.
"""

import math
from html import escape
from typing import Dict, Optional

from markupsafe import Markup

# Tinta e linhas: os tokens do pdf_report, que o relatório novo mantém.
INK = "#0B0B0B"
SECONDARY = "#52514E"
MUTED = "#898781"
RULE = "#E1E0D9"
ACCENT = "#5D5294"

# Semânticas. O texto sobre fundo branco usa os tons escuros (contraste de 4,5:1).
COR_POLARIDADE = {"positiva": "#2F7D57", "negativa": "#B3402F", "mista": "#B7791F", "neutra": "#8A8F9C"}
COR_TEXTO_POLARIDADE = {"positiva": "#1F6B47", "negativa": "#A03624", "mista": "#8A5A0F", "neutra": "#5E6370"}

# Da esquerda para a direita: o que é bom à esquerda e o que é crítica à direita.
ORDEM_NA_BARRA = ("positiva", "mista", "neutra", "negativa")

_ROTULO = {
    "positiva": ("elogio", "elogios"),
    "negativa": ("crítica", "críticas"),
    "mista": ("mista", "mistas"),
    "neutra": ("neutra", "neutras"),
}
_FONTE = "Rubik, Helvetica, Arial, sans-serif"


def _numero(valor: float) -> str:
    return "{:.2f}".format(valor).rstrip("0").rstrip(".")


def palavra_da_polaridade(polaridade: str, quantidade: int) -> str:
    """"1 elogio", "9 críticas": a contagem em palavras."""
    singular, plural = _ROTULO[polaridade]
    return "{} {}".format(quantidade, singular if quantidade == 1 else plural)


def texto_polaridades(polaridades: Dict[str, int]) -> str:
    """"9 críticas · 1 mista · 1 elogio": do mais numeroso ao menos, só o que existe."""
    itens = [(polaridades.get(nome, 0), indice, nome) for indice, nome in enumerate(ORDEM_NA_BARRA)]
    itens = sorted((item for item in itens if item[0] > 0), key=lambda item: (-item[0], item[1]))
    return " · ".join(palavra_da_polaridade(nome, n) for n, _, nome in itens)


# Mista e neutra levam listras verticais mais claras por cima da cor, para o PDF ler bem em escala de
# cinza. São retângulos e não um <pattern> de SVG: os ids de um padrão se repetem a cada gráfico, e o
# HTML do relatório trata id repetido como âncora duplicada.
_LISTRA = {"mista": "#F3E2BB", "neutra": "#D6D8DE"}
_PASSO_DA_LISTRA = 3.0
_LARGURA_DA_LISTRA = 1.3


def _segmento(x: float, largura: float, altura: float, polaridade: str) -> str:
    partes = ['<rect x="{}" y="0" width="{}" height="{}" fill="{}"/>'.format(
        _numero(x), _numero(largura), _numero(altura), COR_POLARIDADE[polaridade])]
    cor_da_listra = _LISTRA.get(polaridade)
    if cor_da_listra:
        posicao = x + 0.8
        while posicao + _LARGURA_DA_LISTRA <= x + largura:
            partes.append('<rect x="{}" y="0" width="{}" height="{}" fill="{}"/>'.format(
                _numero(posicao), _numero(_LARGURA_DA_LISTRA), _numero(altura), cor_da_listra))
            posicao += _PASSO_DA_LISTRA
    return "".join(partes)


def barra_polaridade(polaridades: Dict[str, int], largura: float = 160, altura: float = 8,
                     descricao: Optional[str] = None) -> Markup:
    """Barra empilhada: elogios, mistas, neutras e críticas, na proporção dos respondentes.

    `largura` é a largura da barra inteira; quem quer comparar temas passa
    uma largura proporcional ao número de respondentes de cada um. Segmento
    que existe nunca some: tem pelo menos 2 px.
    """
    total = sum(polaridades.get(nome, 0) for nome in ORDEM_NA_BARRA)
    titulo = escape(descricao or texto_polaridades(polaridades) or "sem respondentes")
    partes = ['<svg xmlns="http://www.w3.org/2000/svg" class="grafico barra" role="img" '
              'width="{l}" height="{a}" viewBox="0 0 {l} {a}"><title>{t}</title>'.format(
                  l=_numero(largura), a=_numero(altura), t=titulo)]
    partes.append('<rect x="0" y="0" width="{l}" height="{a}" fill="{c}"/>'.format(
        l=_numero(largura), a=_numero(altura), c=RULE))
    if total > 0:
        presentes = [nome for nome in ORDEM_NA_BARRA if polaridades.get(nome, 0) > 0]
        piso = 2.0
        larguras = {nome: max(piso, largura * polaridades[nome] / total) for nome in presentes}
        excesso = sum(larguras.values()) - largura
        if excesso > 0:
            maior = max(presentes, key=lambda nome: larguras[nome])
            larguras[maior] -= excesso
        x = 0.0
        for nome in presentes:
            partes.append(_segmento(x, larguras[nome], altura, nome))
            x += larguras[nome]
    partes.append("</svg>")
    return Markup("".join(partes))


def _ponto(cx: float, cy: float, raio: float, angulo: float):
    radianos = math.radians(angulo)
    return cx + raio * math.cos(radianos), cy - raio * math.sin(radianos)


def _sinal(valor: int) -> str:
    return "+{}".format(valor) if valor > 0 else ("−{}".format(abs(valor)) if valor < 0 else "0")


def medidor(nota: Optional[int], largura: float = 220, legenda: str = "Termômetro de Experiência") -> Markup:
    """Medidor semicircular de −5 a +5; sem nota, um medidor cinza que diz "não avaliável".

    O arco colorido sai do zero (o topo) até a nota, para a esquerda se
    negativa e para a direita se positiva. O número e as pontas da escala
    carregam o significado, e não só a cor.
    """
    cx, cy, raio, espessura = 120.0, 112.0, 88.0, 14.0
    altura = 150.0
    titulo = "{}: {}".format(legenda, "não avaliável" if nota is None else _sinal(nota))
    partes = ['<svg xmlns="http://www.w3.org/2000/svg" class="grafico medidor" role="img" '
              'width="{w}" height="{h}" viewBox="0 0 240 {alt}"><title>{t}</title>'.format(
                  w=_numero(largura), h=_numero(largura * altura / 240), alt=_numero(altura), t=escape(titulo))]
    esquerda, direita = _ponto(cx, cy, raio, 180), _ponto(cx, cy, raio, 0)
    partes.append('<path d="M {} {} A {r} {r} 0 0 1 {} {}" fill="none" stroke="{c}" stroke-width="{e}" '
                  'stroke-linecap="butt"/>'.format(_numero(esquerda[0]), _numero(esquerda[1]),
                                                   _numero(direita[0]), _numero(direita[1]),
                                                   r=_numero(raio), c=RULE, e=_numero(espessura)))
    if nota is not None and nota != 0:
        topo = _ponto(cx, cy, raio, 90)
        fim = _ponto(cx, cy, raio, 180 - (nota + 5) * 18)
        cor = COR_POLARIDADE["positiva" if nota > 0 else "negativa"]
        varredura = 1 if nota > 0 else 0
        partes.append('<path d="M {} {} A {r} {r} 0 0 {v} {} {}" fill="none" stroke="{c}" stroke-width="{e}" '
                      'stroke-linecap="butt"/>'.format(_numero(topo[0]), _numero(topo[1]),
                                                       _numero(fim[0]), _numero(fim[1]), v=varredura,
                                                       r=_numero(raio), c=cor, e=_numero(espessura)))
        partes.append('<circle cx="{}" cy="{}" r="{}" fill="{}" stroke="#FFFFFF" stroke-width="2.5"/>'.format(
            _numero(fim[0]), _numero(fim[1]), _numero(espessura / 2 + 2), cor))
    topo_externo, topo_interno = _ponto(cx, cy, raio + espessura / 2 + 3, 90), _ponto(cx, cy, raio - espessura / 2 - 3, 90)
    partes.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="{}" stroke-width="1.2"/>'.format(
        _numero(topo_externo[0]), _numero(topo_externo[1]), _numero(topo_interno[0]), _numero(topo_interno[1]), SECONDARY))
    texto = "n/d" if nota is None else _sinal(nota)
    partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-weight="700" font-size="40" '
                  'fill="{}">{}</text>'.format(_numero(cx), _numero(cy - 14), _FONTE, INK, texto))
    partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="10" fill="{}">{}</text>'.format(
        _numero(cx), _numero(cy + 4), _FONTE, SECONDARY, "não avaliável" if nota is None else "de −5 a +5"))
    partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="9" fill="{}">−5 crítico</text>'.format(
        _numero(esquerda[0]), _numero(cy + 24), _FONTE, MUTED))
    partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="9" fill="{}">+5 excelência</text>'.format(
        _numero(direita[0]), _numero(cy + 24), _FONTE, MUTED))
    partes.append("</svg>")
    return Markup("".join(partes))


_COLUNAS = ("abaixo", "habitual", "acima")
_LINHAS = ("alta", "media", "baixa")
_ROTULO_COLUNA = ("negativo", "médio", "positivo")
_ROTULO_LINHA = ("alta", "média", "baixa")
# O que o leitor lê no título acessível, com o eixo por extenso.
_DESCRICAO_COLUNA = ("tom mais negativo que a média", "tom como a média", "tom mais positivo que a média")
_DESCRICAO_LINHA = ("energia alta", "energia média", "energia baixa")


def grade_voz(avaliavel: bool, direcao: str = "", nivel: str = "", respondentes_com_voz: int = 0,
              largura: float = 150) -> Markup:
    """Grade 3×3 da voz do tema: o tom (de mais negativo a mais positivo) por energia (alta a baixa).

    O marcador cai na célula da leitura calculada (`ler_voz`); no centro
    (tom como a média e energia média) ele é cinza e a grade não diz nada,
    que é o caso mais comum. Sem leitura avaliável, a grade fica tracejada e
    diz quantos respondentes tinham voz.
    """
    x0, y0, w, h = 52.0, 6.0, 30.0, 22.0
    altura = 100.0
    titulo = "Voz do tema: não avaliável" if not avaliavel else "Voz do tema: {} / {}".format(
        _DESCRICAO_COLUNA[_COLUNAS.index(direcao)] if direcao in _COLUNAS else "?",
        _DESCRICAO_LINHA[_LINHAS.index(nivel)] if nivel in _LINHAS else "?")
    partes = ['<svg xmlns="http://www.w3.org/2000/svg" class="grafico voz" role="img" '
              'width="{w}" height="{h}" viewBox="0 0 150 {alt}"><title>{t}</title>'.format(
                  w=_numero(largura), h=_numero(largura * altura / 150), alt=_numero(altura), t=escape(titulo))]
    coluna = _COLUNAS.index(direcao) if direcao in _COLUNAS else 1
    linha = _LINHAS.index(nivel) if nivel in _LINHAS else 1
    for j in range(3):
        for i in range(3):
            ativa = avaliavel and i == coluna and j == linha
            estilo = ('fill="#FFFFFF" stroke="{}" stroke-dasharray="2 2"'.format(RULE) if not avaliavel else
                      ('fill="#E7E3F3" stroke="{}"'.format(ACCENT) if ativa else 'fill="#F5F4F0" stroke="{}"'.format(RULE)))
            partes.append('<rect x="{}" y="{}" width="{}" height="{}" rx="2" {}/>'.format(
                _numero(x0 + i * w), _numero(y0 + j * h), _numero(w - 2), _numero(h - 2), estilo))
    if avaliavel:
        cx, cy = x0 + coluna * w + (w - 2) / 2, y0 + linha * h + (h - 2) / 2
        centro = coluna == 1 and linha == 1
        partes.append('<circle cx="{}" cy="{}" r="6.5" fill="{}" stroke="#FFFFFF" stroke-width="2"/>'.format(
            _numero(cx), _numero(cy), SECONDARY if centro else ACCENT))
    else:
        partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="9.5" fill="{}">voz não avaliável</text>'.format(
            _numero(x0 + 1.5 * w - 1), _numero(y0 + 1.5 * h - 4), _FONTE, SECONDARY))
        partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="8.5" fill="{}">{} com voz</text>'.format(
            _numero(x0 + 1.5 * w - 1), _numero(y0 + 1.5 * h + 8), _FONTE, MUTED, respondentes_com_voz))
    for j, rotulo in enumerate(_ROTULO_LINHA):
        partes.append('<text x="{}" y="{}" text-anchor="end" font-family="{}" font-size="8" fill="{}">{}</text>'.format(
            _numero(x0 - 4), _numero(y0 + j * h + h / 2 + 1), _FONTE, MUTED, rotulo))
    for i, rotulo in enumerate(_ROTULO_COLUNA):
        partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="8" fill="{}">{}</text>'.format(
            _numero(x0 + i * w + (w - 2) / 2), _numero(y0 + 3 * h + 8), _FONTE, MUTED, rotulo))
    partes.append('<text x="{}" y="{}" text-anchor="middle" font-family="{}" font-size="8" font-weight="500" fill="{}">tom da voz</text>'.format(
        _numero(x0 + 1.5 * w - 1), _numero(y0 + 3 * h + 21), _FONTE, SECONDARY))
    partes.append('<text transform="translate(9 {}) rotate(-90)" text-anchor="middle" font-family="{}" font-size="8" font-weight="500" fill="{}">energia</text>'.format(
        _numero(y0 + 1.5 * h), _FONTE, SECONDARY))
    partes.append("</svg>")
    return Markup("".join(partes))
