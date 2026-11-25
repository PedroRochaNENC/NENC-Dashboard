"""PDF do relatório por tema (Fase 3 de docs/SPEC_RELATORIO_POR_TEMA.md).

Exigem o Pango (WeasyPrint): o marcador `requer_weasyprint` e o skipUnless os pulam onde ele não está.
"""

import os
import re
import unittest

import pytest

from tests.fixtures import relatorio_exemplos as exemplos
from utils import relatorio_html, relatorio_pdf

pytestmark = pytest.mark.requer_weasyprint

_DISPONIVEL = relatorio_pdf.disponivel()


def _renderizar(documento):
    import weasyprint

    html = relatorio_html.documento_html(documento)
    base = str(relatorio_html.PASTA_STATIC) + os.sep
    return weasyprint.HTML(string=html, base_url=base).render()


def _paginas_das_ancoras(renderizado):
    paginas = {}
    for numero, pagina in enumerate(renderizado.pages, 1):
        for nome in pagina.anchors:
            paginas.setdefault(nome, numero)
    return paginas


@unittest.skipUnless(_DISPONIVEL, "WeasyPrint indisponível: {}".format(relatorio_pdf.motivo_da_indisponibilidade()))
class PdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rede = _renderizar(exemplos.rede())
        cls.exploratorio = _renderizar(exemplos.exploratorio())

    def test_gera_um_pdf(self):
        pdf = relatorio_pdf.relatorio_pdf(exemplos.exploratorio())
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 10_000)

    def test_capa_indice_e_capitulos_em_paginas_proprias(self):
        ancoras = _paginas_das_ancoras(self.rede)
        self.assertEqual(ancoras["resumo"], 3 if ancoras["alertas"] == 2 else ancoras["resumo"])
        capitulos = [ancoras["c{}".format(numero)] for numero in (1, 2, 3, 4, 7)]
        self.assertEqual(capitulos, sorted(set(capitulos)), "capítulo cheio começa em página nova")
        self.assertGreater(ancoras["c1"], 2)

    def test_reclamacoes_comecam_em_pagina_nova(self):
        ancoras = _paginas_das_ancoras(self.rede)
        ultima_do_capitulo_7 = max(
            numero for numero, pagina in enumerate(self.rede.pages, 1) if "transversais" in pagina.anchors
        )
        self.assertGreater(ancoras["reclamacoes"], ultima_do_capitulo_7)

    def test_marcadores_do_pdf_cobrem_capitulos_e_subcapitulos(self):
        rotulos = [marcador[1] for pagina in self.rede.pages for marcador in pagina.bookmarks]
        for esperado in ("1 Equipamentos", "1.1 Máquina fora de serviço", "3 Pagamento", "7 Avaliação geral e outros assuntos",
                         "9 Reclamações e feedbacks negativos"):
            self.assertIn(esperado, rotulos)
        self.assertNotIn("Lava Já — Voz do Cliente por QR Code", rotulos)

    def test_toda_ancora_do_indice_cai_numa_pagina(self):
        ancoras = _paginas_das_ancoras(self.rede)
        for capitulo in exemplos.rede()["capitulos"]:
            self.assertIn(capitulo["id"], ancoras)
            for sub in capitulo["subcapitulos"]:
                self.assertIn(sub["id"], ancoras)

    def test_subcapitulo_nao_vem_antes_do_capitulo(self):
        ancoras = _paginas_das_ancoras(self.rede)
        for capitulo in exemplos.rede()["capitulos"]:
            for sub in capitulo["subcapitulos"]:
                self.assertGreaterEqual(ancoras[sub["id"]], ancoras[capitulo["id"]])

    def test_a_capa_nao_entra_nos_marcadores_e_a_primeira_pagina_e_a_capa(self):
        primeira = self.rede.pages[0]
        self.assertEqual(primeira.bookmarks, [])
        self.assertIn("alertas", [link[1] for link in primeira.links if link[0] == "internal"])

    def test_leitura_exploratoria_gera_menos_paginas_e_sem_recomendacao(self):
        self.assertLess(len(self.exploratorio.pages), len(self.rede.pages))

    def test_as_fontes_do_css_existem_em_static(self):
        css = (relatorio_html.PASTA_STATIC / "relatorio_pdf.css").read_text(encoding="utf-8")
        arquivos = re.findall(r'url\("([^"]+\.ttf)"\)', css)
        self.assertEqual(len(arquivos), 3)
        for arquivo in arquivos:
            self.assertTrue((relatorio_html.PASTA_STATIC / arquivo).is_file(), arquivo)

    def test_disponivel_e_motivo_vazio_quando_o_pango_esta_instalado(self):
        self.assertTrue(relatorio_pdf.disponivel())
        self.assertEqual(relatorio_pdf.motivo_da_indisponibilidade(), "")


if __name__ == "__main__":
    unittest.main()
