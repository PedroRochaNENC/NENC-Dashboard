"""Gera as fontes estaticas do relatorio (Regular, Medium e Bold) da Rubik variavel.

O WeasyPrint usa arquivos de fonte estaticos, e `static/fonts/Rubik.ttf` e a
variavel (eixo `wght` de 300 a 900). Rode de novo so se a Rubik mudar:

    python scripts/gerar_fontes_relatorio.py

A licenca (OFL) esta em `static/fonts/Rubik-OFL.txt`.
"""

from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

PASTA = Path(__file__).resolve().parent.parent / "static" / "fonts"
PESOS = (("Regular", 400), ("Medium", 500), ("Bold", 700))


def gerar() -> None:
    origem = PASTA / "Rubik.ttf"
    for nome, peso in PESOS:
        instancia = instancer.instantiateVariableFont(TTFont(origem), {"wght": peso}, inplace=False)
        destino = PASTA / "Rubik-{}.ttf".format(nome)
        instancia.save(destino)
        print("gerada", destino.name, destino.stat().st_size, "bytes")


if __name__ == "__main__":
    gerar()
