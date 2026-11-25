"""Relatório por tema: esquema, gráficos e HTML (Fase 3 de docs/SPEC_RELATORIO_POR_TEMA.md).

Os testes de HTML rodam em qualquer máquina; os de PDF estão em test_relatorio_pdf.py.
"""

import copy
import re
import unittest

from tests.fixtures import relatorio_exemplos as exemplos
from utils import relatorio_esquema as esquema
from utils import relatorio_graficos as graficos
from utils import relatorio_html


def _texto_visivel(html) -> str:
    """O texto que o leitor vê: sem o CSS, as marcas e os títulos acessíveis dos SVG."""
    html = re.sub(r"<style.*?</style>", " ", str(html), flags=re.S)
    html = re.sub(r"<title>.*?</title>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class EsquemaTests(unittest.TestCase):
    def test_as_fixtures_seguem_o_esquema(self):
        for nome in ("rede", "exploratorio"):
            documento = getattr(exemplos, nome)()
            self.assertEqual(esquema.erros_do_esquema(documento), [], nome)
            self.assertEqual(esquema.erros_de_referencia(documento), [], nome)

    def test_campo_obrigatorio_ausente_e_apontado(self):
        documento = exemplos.rede()
        del documento["resumo"]
        self.assertTrue(any("resumo" in erro for erro in esquema.erros_do_esquema(documento)))

    def test_fala_inexistente_e_apontada(self):
        documento = exemplos.rede()
        documento["capitulos"][0]["subcapitulos"][0]["falas"].append("t999")
        self.assertTrue(any("t999" in erro for erro in esquema.erros_de_referencia(documento)))

    def test_hipotese_que_aponta_para_subcapitulo_inexistente_e_apontada(self):
        documento = exemplos.rede()
        documento["capitulos"][0]["hipotese"]["apoia_em"] = ["c9.s9"]
        self.assertTrue(any("c9.s9" in erro for erro in esquema.erros_de_referencia(documento)))

    def test_polaridade_fora_da_lista_e_recusada(self):
        documento = exemplos.rede()
        documento["trechos"]["t1"]["polaridade"] = "feliz"
        self.assertTrue(esquema.erros_do_esquema(documento))


class GraficosTests(unittest.TestCase):
    def test_polaridades_em_palavras_do_maior_para_o_menor(self):
        self.assertEqual(
            graficos.texto_polaridades({"positiva": 1, "negativa": 9, "mista": 1, "neutra": 0}),
            "9 críticas · 1 elogio · 1 mista",
        )

    def test_singular_e_plural(self):
        self.assertEqual(graficos.palavra_da_polaridade("positiva", 1), "1 elogio")
        self.assertEqual(graficos.palavra_da_polaridade("positiva", 2), "2 elogios")

    def test_barra_ocupa_a_largura_pedida_e_nenhum_segmento_some(self):
        svg = str(graficos.barra_polaridade({"positiva": 60, "negativa": 1, "mista": 1}, largura=100))
        larguras = [float(valor) for valor in re.findall(r'<rect x="[\d.]+" y="0" width="([\d.]+)"', svg)][1:]
        self.assertAlmostEqual(sum(larguras), 100, places=1)
        self.assertEqual(len(larguras), 3)
        self.assertTrue(all(largura >= 2 for largura in larguras))

    def test_barra_sem_respondentes_e_so_a_trilha(self):
        svg = str(graficos.barra_polaridade({}, largura=80))
        self.assertEqual(svg.count("<rect"), 1)

    def test_medidor_negativo_vai_para_a_esquerda_e_o_positivo_para_a_direita(self):
        negativo, positivo = str(graficos.medidor(-3)), str(graficos.medidor(3))
        self.assertIn("−3", negativo)
        self.assertIn("+3", positivo)
        self.assertIn(graficos.COR_POLARIDADE["negativa"], negativo)
        self.assertIn(graficos.COR_POLARIDADE["positiva"], positivo)

    def test_medidor_sem_nota_diz_nao_avaliavel(self):
        svg = str(graficos.medidor(None))
        self.assertIn("não avaliável", svg)
        self.assertNotIn(graficos.COR_POLARIDADE["positiva"], svg)

    def test_grade_de_voz_marca_so_a_celula_da_leitura(self):
        svg = str(graficos.grade_voz(True, "abaixo", "alta"))
        self.assertEqual(svg.count("<circle"), 1)
        self.assertIn("tom mais negativo que a média", svg)
        self.assertIn("energia alta", svg)

    def test_grade_de_voz_nao_avaliavel_diz_quantos_tinham_voz(self):
        svg = str(graficos.grade_voz(False, respondentes_com_voz=3))
        self.assertIn("voz não avaliável", svg)
        self.assertIn("3 com voz", svg)
        self.assertEqual(svg.count("<circle"), 0)

    def test_texto_da_legenda_e_escapado(self):
        svg = str(graficos.barra_polaridade({"positiva": 1}, descricao="<b>x</b>"))
        self.assertNotIn("<b>", svg)


class DocumentoTests(unittest.TestCase):
    def setUp(self):
        self.rede = exemplos.rede()
        self.exploratorio = exemplos.exploratorio()

    def test_ordem_da_spec(self):
        html = relatorio_html.documento_html(self.rede)
        marcas = ['class="capa"', 'class="indice"', 'id="alertas"', 'id="resumo"', 'id="visao-geral"',
                  'id="c1"', 'id="c7"', 'id="transversais"', 'id="reclamacoes"', 'id="notas"']
        posicoes = [html.index(marca) for marca in marcas]
        self.assertEqual(posicoes, sorted(posicoes))

    def test_o_corpo_do_app_nao_traz_capa_nem_indice_e_leva_o_css(self):
        corpo = str(relatorio_html.corpo_html(self.rede))
        self.assertNotIn('class="capa"', corpo)
        self.assertNotIn('class="indice"', corpo)
        self.assertTrue(corpo.startswith("<style>"))
        self.assertIn(".relatorio", corpo)
        self.assertNotIn("@page", corpo)

    def test_indice_leva_a_cada_capitulo_e_subcapitulo(self):
        html = relatorio_html.documento_html(self.rede)
        indice = html[html.index('<nav class="indice">'):html.index("</nav>")]
        for capitulo in self.rede["capitulos"]:
            self.assertIn('href="#{}"'.format(capitulo["id"]), indice)
            self.assertIn('id="{}"'.format(capitulo["id"]), html)
            for sub in capitulo["subcapitulos"]:
                self.assertIn('href="#{}"'.format(sub["id"]), indice)
                self.assertIn('id="{}"'.format(sub["id"]), html)

    def test_toda_ancora_do_indice_existe(self):
        html = relatorio_html.documento_html(self.rede)
        for destino in re.findall(r'href="#([^"]+)"', html):
            self.assertIn('id="{}"'.format(destino), html, destino)

    def test_capitulo_por_faixa(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        c1 = html[html.index('id="c1"'):html.index('id="c2"')]
        c4 = html[html.index('id="c4"'):html.index('id="c5"')]
        c5 = html[html.index('id="c5"'):html.index('id="c6"')]
        self.assertIn('class="cartao cartao--recomendacao"', c1)
        self.assertIn('class="cartao cartao--hipotese"', c1)
        self.assertNotIn('class="cartao cartao--recomendacao"', c4)
        self.assertIn("O que acompanhar", c4)
        self.assertIn("capitulo--curto", c5)
        for cartao in ('cartao--recomendacao"', 'cartao--hipotese"', 'cartao--acompanhar"', 'class="ficha"'):
            self.assertNotIn(cartao, c5)

    def test_leitura_exploratoria_nao_tem_recomendacao(self):
        html = str(relatorio_html.documento_html(self.exploratorio))
        self.assertNotIn('class="cartao cartao--recomendacao"', html)
        self.assertIn("O que acompanhar e investigar", html)
        self.assertIn("Leitura exploratória", html)

    def test_leitura_nao_exploratoria_chama_so_de_acompanhar(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertNotIn("e investigar", html)

    def test_alerta_so_aparece_quando_ha(self):
        com_alerta = relatorio_html.documento_html(self.rede)
        sem_alerta = relatorio_html.documento_html(self.exploratorio)
        self.assertIn('id="alertas"', com_alerta)
        self.assertIn("1 alerta crítico", _texto_visivel(com_alerta.split('<nav class="indice">')[0]))
        self.assertNotIn('id="alertas"', sem_alerta)
        self.assertNotIn("alerta crítico", _texto_visivel(sem_alerta.split('<nav class="indice">')[0]))

    def test_numeracao_das_secoes_depois_dos_capitulos(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertIn('<span class="num">8</span> Leituras transversais e Matriz de Destaques', html)
        self.assertIn('<span class="num">9</span> Reclamações e feedbacks negativos', html)

    def test_sem_transversais_a_secao_vira_so_matriz(self):
        html = str(relatorio_html.corpo_html(self.exploratorio, com_css=False))
        self.assertIn('<span class="num">7</span> Matriz de Destaques', html)
        self.assertIn('<span class="num">8</span> Reclamações e feedbacks negativos', html)
        self.assertNotIn("Leitura transversal", html)

    def test_sem_transversais_nem_matriz_a_secao_some_e_as_reclamacoes_sobem(self):
        documento = exemplos.exploratorio()
        documento["matriz"] = {"avaliavel": False, "grupos": []}
        html = relatorio_html.documento_html(documento)
        self.assertNotIn('id="transversais"', html)
        self.assertNotIn("Matriz de Destaques", html)
        self.assertIn('<span class="num">7</span> Reclamações e feedbacks negativos', html)

    def test_unidade_so_aparece_em_relatorio_de_varias_unidades(self):
        rede = str(relatorio_html.corpo_html(self.rede, com_css=False))
        unica = str(relatorio_html.corpo_html(self.exploratorio, com_css=False))
        self.assertIn("<th>Unidade</th>", rede)
        self.assertIn("Unidades:", rede)
        self.assertIn("Moema (LJ-MOE-01)", rede)
        self.assertNotIn("<th>Unidade</th>", unica)
        self.assertNotIn("Unidades:", unica)

    def test_unidades_do_cartao_sao_resumidas(self):
        rede = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertIn("Moema (LJ-MOE-01) · Pinheiros (LJ-PIN-02) e mais 2", rede)

    def test_reclamacoes_so_com_isoladas_vira_tabela_sem_cartao(self):
        html = str(relatorio_html.corpo_html(self.exploratorio, com_css=False))
        self.assertNotIn('class="reclamacao"', html)
        self.assertIn('class="isoladas"', html)

    def test_reclamacoes_vazias_dizem_que_nao_ha(self):
        documento = exemplos.exploratorio()
        documento["reclamacoes"] = {"abertura": "", "itens": [], "isoladas": []}
        html = str(relatorio_html.corpo_html(documento, com_css=False))
        self.assertIn("Nenhuma reclamação ou feedback negativo nesta amostra.", html)

    def test_grupo_vazio_da_matriz(self):
        html = str(relatorio_html.corpo_html(self.exploratorio, com_css=False))
        self.assertIn("Nenhum áudio do recorte neste grupo.", html)

    def test_as_falas_vem_dos_trechos_com_alias_tempo_e_nota(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertIn("Áudio 012 · 00:14 · texto −0,80", html)
        self.assertIn("A máquina travou no meio".lower(), html.lower())

    def test_fala_mascarada_chega_como_esta_no_json(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertIn("[funcionário]", html)
        self.assertNotIn("Marcos me", html.split('id="reclamacoes"')[1])

    def test_delta_so_aparece_no_capitulo_que_o_traz(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        texto = _texto_visivel(html)
        self.assertEqual(texto.count("Desde a análise anterior"), 1)
        self.assertIn("6 de 52 respondentes anteriores e 5 de 12 novos", texto)
        self.assertIn("variação observada", texto)

    def test_selo_de_outros(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertIn("Fora da taxonomia validada com a cliente", html)

    def test_temas_sem_mencao_ficam_numa_linha(self):
        html = str(relatorio_html.corpo_html(self.rede, com_css=False))
        self.assertIn("Sem menção nesta amostra:</strong> Fidelidade e cupons, Entrega em domicílio", html)
        self.assertNotIn("0/64", html)

    def test_texto_do_json_e_escapado(self):
        documento = exemplos.rede()
        documento["resumo"] = "Texto com <script>alert(1)</script> e & comercial"
        html = str(relatorio_html.corpo_html(documento, com_css=False))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_nada_de_markdown_vazando(self):
        for nome in ("rede", "exploratorio"):
            texto = _texto_visivel(relatorio_html.documento_html(getattr(exemplos, nome)()))
            for resto in ("**", "*Apoia", "*Como", "_Faixas", "*Perfil", "*Ação", "###"):
                self.assertNotIn(resto, texto, "{}: {!r}".format(nome, resto))

    def test_o_texto_visivel_nao_usa_termos_tecnicos_de_voz(self):
        for nome in ("rede", "exploratorio"):
            texto = _texto_visivel(relatorio_html.documento_html(getattr(exemplos, nome)())).lower()
            for termo in ("valência", "ativação", "habitual", "pitch", "loudness"):
                self.assertNotIn(termo, texto, "{}: {}".format(nome, termo))

    def test_nao_ha_referencia_a_ids_internos_no_texto(self):
        texto = _texto_visivel(relatorio_html.documento_html(self.rede))
        self.assertIsNone(re.search(r"\b[AHRM]\d\b", texto))
        self.assertIsNone(re.search(r"\bc\d\.s\d\b", texto))

    def test_documento_nao_altera_o_json(self):
        antes = copy.deepcopy(self.rede)
        relatorio_html.documento_html(self.rede)
        self.assertEqual(self.rede, antes)

    def test_cabecalho_corrido_e_truncado(self):
        documento = exemplos.rede()
        documento["cabecalho"]["projeto"] = "P" * 200
        html = relatorio_html.documento_html(documento)
        regra = re.search(r'@page \{ @top-right \{ content: "([^"]*)"', html).group(1)
        self.assertTrue(regra.endswith("…"))
        self.assertLessEqual(len(regra), 70)

    def test_aspas_no_cabecalho_nao_quebram_o_css(self):
        documento = exemplos.rede()
        documento["cabecalho"]["projeto"] = 'Projeto "A"'
        html = relatorio_html.documento_html(documento)
        self.assertIn('content: "Projeto \\"A\\" · Rede · 6 unidades"', html)

    def test_sinal_de_menor_no_cabecalho_nao_fecha_o_style(self):
        documento = exemplos.rede()
        documento["cabecalho"]["projeto"] = "A</style><script>x</script>"
        html = relatorio_html.documento_html(documento)
        self.assertEqual(html.count("</style>"), 1)
        self.assertNotIn("<script>x</script>", html)


if __name__ == "__main__":
    unittest.main()
