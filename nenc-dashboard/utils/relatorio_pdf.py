"""
HTML em PDF, com o WeasyPrint (docs/SPEC_RELATORIO_POR_TEMA.md, §8.1).

O WeasyPrint precisa do Pango no sistema. No Docker ele vem dos pacotes do
Dockerfile; no Windows de desenvolvimento, do MSYS2 (docs/OPERATIONS.md). O
import é preguiçoso para o resto do app não depender dele, e `disponivel()`
diz se o PDF pode ser gerado nesta máquina.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from utils import relatorio_html

_PASTA_PANGO_NO_WINDOWS = Path(r"C:\msys64\mingw64\bin")
_ERRO: Optional[str] = None


def _preparar_ambiente() -> None:
    """No Windows, aponta o WeasyPrint para o Pango do MSYS2, quando ele existe e a variável não foi posta."""
    if os.name == "nt" and not os.environ.get("WEASYPRINT_DLL_DIRECTORIES") and _PASTA_PANGO_NO_WINDOWS.is_dir():
        os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(_PASTA_PANGO_NO_WINDOWS)


def _weasyprint():
    global _ERRO
    _preparar_ambiente()
    try:
        import weasyprint
    except (ImportError, OSError) as erro:  # OSError: biblioteca do Pango ausente
        _ERRO = str(erro)
        return None
    return weasyprint


def disponivel() -> bool:
    return _weasyprint() is not None


def motivo_da_indisponibilidade() -> str:
    _weasyprint()
    return _ERRO or ""


def html_para_pdf(html: str) -> bytes:
    """O PDF do HTML, com as fontes e o CSS de `static/` resolvidos pelo caminho."""
    weasyprint = _weasyprint()
    if weasyprint is None:
        raise RuntimeError("WeasyPrint indisponível: {}".format(_ERRO))
    return weasyprint.HTML(string=html, base_url=str(relatorio_html.PASTA_STATIC) + os.sep).write_pdf()


def relatorio_pdf(relatorio: Dict[str, Any]) -> bytes:
    """O PDF do relatório por tema."""
    return html_para_pdf(relatorio_html.documento_html(relatorio))


def legado_pdf(texto: Optional[str], **opcoes: Any) -> bytes:
    """O PDF de um texto em markdown (v2 ou antigo), sem o apêndice. Opções: as de `relatorio_html.legado_html`."""
    return html_para_pdf(relatorio_html.legado_html(texto, **opcoes))


def anexo_pdf(texto: Optional[str], **opcoes: Any) -> bytes:
    """O PDF do Anexo técnico. Opções: as de `relatorio_html.anexo_html`."""
    return html_para_pdf(relatorio_html.anexo_html(texto, **opcoes))
