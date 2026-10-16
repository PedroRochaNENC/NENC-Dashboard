"""Contrato das páginas de prosódia: os sinais chegam mesmo à IA.

Duas formas de o prompt sair sem os dados que ele próprio pede já aconteceram
aqui, e são o que estes testes seguram.

A primeira: o CSV Sincronizado é lido dentro de try/except em várias telas.
Enquanto o except era `pass`, um arquivo ilegível virava dataframe vazio sem
rastro, e o relatório concluía que o áudio "não tem dados acústicos". Foi assim
que 15 análises individuais do Smart Fit nasceram afirmando uma lacuna
inexistente.

A segunda: a etapa estratégica da antiga Análise Geral recebia um prompt
montado à mão, que ficou para trás dos sinais acrescentados depois. A v2
calcula os números em código, a partir dos dataframes do projeto inteiro; o
que sobrou de risco é a página entregar os do recorte, e aí o alias de um
áudio, a régua da voz e os terços da Matriz mudariam com o filtro.

As páginas são scripts Streamlit e não rodam num teste de unidade; o que dá
para garantir sem executá-las é o contrato lido do código-fonte.
"""

import ast
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = APP_ROOT / "modules" / "prosodia"

def _paginas():
    return sorted(p for p in PAGES_DIR.glob("*.py") if p.name != "__init__.py")


def _le_sincronizado(no: ast.Try, fonte: str) -> bool:
    """O bloco parseia o Sincronizado, não apenas menciona a coluna.

    `create_audio(..., sincronizado_csv=None)` cita o nome sem ler nada; exigir
    a chamada de parse evita acusar blocos que tratam de outra coisa.
    """
    trecho = "\n".join(
        ast.get_source_segment(fonte, filho) or "" for filho in no.body
    )
    if "normalizar_sincronizado" in trecho:
        return True
    return "read_csv" in trecho and (
        "sincronizado_csv" in trecho or "sinc_bytes" in trecho
    )


def _handler_mudo(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


class SincronizadoNaoFalhaEmSilencioTests(unittest.TestCase):
    def test_the_sweep_finds_the_pages(self):
        """Glob vazio passaria o teste seguinte por vacuidade."""
        nomes = {p.name for p in _paginas()}

        self.assertIn("analise_geral.py", nomes)
        self.assertIn("audio.py", nomes)

    def test_the_sweep_finds_blocks_that_read_the_sincronizado(self):
        """Se o padrão de leitura mudar, o teste abaixo perde o alvo."""
        encontrados = 0
        for pagina in _paginas():
            fonte = pagina.read_text(encoding="utf-8")
            for no in ast.walk(ast.parse(fonte)):
                if isinstance(no, ast.Try) and _le_sincronizado(no, fonte):
                    encontrados += 1

        self.assertGreaterEqual(encontrados, 3)

    def test_no_page_swallows_a_broken_sincronizado(self):
        for pagina in _paginas():
            fonte = pagina.read_text(encoding="utf-8")
            for no in ast.walk(ast.parse(fonte)):
                if not isinstance(no, ast.Try) or not _le_sincronizado(no, fonte):
                    continue
                for handler in no.handlers:
                    with self.subTest(pagina=pagina.name, linha=handler.lineno):
                        self.assertFalse(
                            _handler_mudo(handler),
                            "{}:{} engole a falha do Sincronizado; registre o "
                            "erro em vez de seguir com dataframe vazio.".format(
                                pagina.name, handler.lineno
                            ),
                        )

    def test_the_pages_that_read_it_have_a_logger(self):
        for nome in ("analise_geral.py", "audio.py"):
            with self.subTest(pagina=nome):
                fonte = (PAGES_DIR / nome).read_text(encoding="utf-8")
                self.assertIn("logging.getLogger(__name__)", fonte)


class PaginaUnicaDoAudioTests(unittest.TestCase):
    """Timeline e Análise viraram `audio.py`; os saltos antigos não podem se perder."""

    def test_old_pages_only_redirect(self):
        for nome in ("audio_timeline.py", "audio_analise.py"):
            with self.subTest(pagina=nome):
                fonte = (PAGES_DIR / nome).read_text(encoding="utf-8")
                self.assertIn('st.switch_page("modules/prosodia/audio.py")', fonte)
                self.assertLess(len(fonte.splitlines()), 30, "{} deveria só redirecionar".format(nome))

    def test_jumps_land_on_the_audio_page(self):
        for nome in ("analise_geral.py", "entrevistas.py"):
            with self.subTest(pagina=nome):
                fonte = (PAGES_DIR / nome).read_text(encoding="utf-8")
                self.assertIn('"modules/prosodia/audio.py"', fonte)
                self.assertNotIn('"modules/prosodia/audio_timeline.py"', fonte)
                self.assertNotIn('"modules/prosodia/audio_analise.py"', fonte)

    def test_menu_has_one_audio_item_and_hides_the_old_pages(self):
        fonte = (APP_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('_page("modules/prosodia/audio.py", audio_label, "waveform")', fonte)
        for antiga in ("audio_timeline.py", "audio_analise.py"):
            trecho = fonte[fonte.index(antiga):fonte.index(antiga) + 120]
            self.assertIn('visibility="hidden"', trecho)

    def test_audios_list_keeps_the_filtered_order_for_the_arrows(self):
        fonte = (PAGES_DIR / "entrevistas.py").read_text(encoding="utf-8")
        self.assertIn('st.session_state["en_filtered_ids"]', fonte)


class AnaliseGeralRecebeOProjetoInteiroTests(unittest.TestCase):
    """A Análise Geral mede contra o projeto inteiro e conta só o recorte.

    A página filtra os dataframes para os painéis. A geração precisa também
    dos do projeto inteiro, guardados antes do filtro. E o relatório tem de
    sair da chamada de gerador_do_relatorio, a que deixa as análises do
    projeto fora da base de conhecimento.
    """

    def setUp(self):
        self.arvore = ast.parse((PAGES_DIR / "analise_geral.py").read_text(encoding="utf-8"))

    def _nos(self, tipo):
        return [no for no in ast.walk(self.arvore) if isinstance(no, tipo)]

    def _chamada(self, nome):
        for no in self._nos(ast.Call):
            if isinstance(no.func, ast.Attribute) and no.func.attr == nome:
                return no
        self.fail("{} não é chamada em analise_geral.py".format(nome))

    def test_the_whole_project_frames_are_kept_before_the_filter(self):
        guarda = [
            no for no in self._nos(ast.Assign)
            if ast.unparse(no.targets[0]) == "(tr_projeto, sinc_projeto)"
            and ast.unparse(no.value) == "(all_tr, all_sinc)"
        ]
        filtro = [no for no in self._nos(ast.If) if ast.unparse(no.test) == "len(audios) < len(audios_projeto)"]

        self.assertEqual((len(guarda), len(filtro)), (1, 1))
        self.assertLess(guarda[0].lineno, filtro[0].lineno)

    def test_the_generation_gets_the_whole_project_and_the_cut(self):
        chamada = self._chamada("gerar_analise_geral")
        argumentos = [ast.unparse(a) for a in chamada.args]

        for esperado in ("audios_projeto", "[a['id'] for a in audios]", "tr_projeto", "sinc_projeto"):
            with self.subTest(argumento=esperado):
                self.assertIn(esperado, argumentos)
        self.assertLessEqual(
            {"perguntas_ativacao", "incluir_legenda", "avisar", "ao_classificar"},
            {palavra.arg for palavra in chamada.keywords},
        )

    def test_the_report_comes_from_the_call_without_the_project_analyses(self):
        chamada = self._chamada("gerar_analise_geral")

        self.assertTrue(any(
            isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "gerador_do_relatorio"
            for a in chamada.args
        ))

    def test_the_pdf_carries_only_the_saved_analysis(self):
        # A seção de métricas acústicas do PDF listava cada áudio pela sessão,
        # que traz o telefone; o apêndice da v2 já tem as médias pelo alias.
        funcoes = {no.name: no for no in self._nos(ast.FunctionDef)}
        self.assertNotIn("_calculate_acoustic_summary_text", funcoes)

        pdf = funcoes["_build_project_analysis_pdf"]
        self.assertEqual(
            [a.arg for a in pdf.args.args + pdf.args.kwonlyargs],
            ["project_name", "project_info", "model", "created_at", "text", "citations"],
        )
        chamadas = [
            no for no in self._nos(ast.Call)
            if isinstance(no.func, ast.Name) and no.func.id == "_build_project_analysis_pdf"
        ]
        self.assertEqual(len(chamadas), 1)
        self.assertNotIn("sinc", ast.unparse(chamadas[0]))


if __name__ == "__main__":
    unittest.main()
