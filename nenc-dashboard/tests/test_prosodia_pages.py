"""Contrato das páginas de prosódia: os sinais chegam mesmo à IA.

Duas formas de o prompt sair sem os dados que ele próprio pede já aconteceram
aqui, e são o que estes testes seguram.

A primeira: o CSV Sincronizado é lido dentro de try/except em várias telas.
Enquanto o except era `pass`, um arquivo ilegível virava dataframe vazio sem
rastro, e o relatório concluía que o áudio "não tem dados acústicos". Foi assim
que 15 análises individuais do Smart Fit nasceram afirmando uma lacuna
inexistente.

A segunda: a etapa estratégica da Análise Geral recebe um prompt montado à mão,
que ficou para trás dos sinais acrescentados depois. Ela tinha de nomear os
temas dos momentos de maior engajamento sem receber a tabela deles.

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


class EtapaEstrategicaRecebeOsSinaisTests(unittest.TestCase):
    """A segunda etapa nao pode interpretar sem os dados da primeira.

    `strat_user` e montado a mao, fora de build_project_user_prompt, e por isso
    nao acompanha sozinho os sinais que entram no prompt principal. O prompt
    estrategico pede os temas dos momentos de maior engajamento e a separacao
    entre entusiasmo e friccao pela valencia: as duas coisas saem da tabela de
    ativacao.
    """

    _ESPERADOS = (
        "stat_result",           # o texto da etapa estatistica
        "acoustic_stats_text",   # medias, dimensoes e distribuicao de emocoes
        "high_activation_text",  # os momentos de maior ativacao
        "top_words_text",        # ranking de assuntos
        "secao_sentimento",      # sentimento do texto e divergencias voz x texto
    )

    def _fonte_do_strat_user(self) -> str:
        fonte = (PAGES_DIR / "analise_geral.py").read_text(encoding="utf-8")
        for no in ast.walk(ast.parse(fonte)):
            if not isinstance(no, ast.Assign):
                continue
            alvos = [a.id for a in no.targets if isinstance(a, ast.Name)]
            if "strat_user" in alvos:
                return ast.get_source_segment(fonte, no.value) or ""
        self.fail("strat_user nao encontrado em analise_geral.py")

    def test_the_glue_prompt_exists(self):
        """Se o nome mudar, o teste abaixo passa sem verificar nada."""
        self.assertTrue(self._fonte_do_strat_user().strip())

    def test_the_strategic_step_gets_every_signal(self):
        trecho = self._fonte_do_strat_user()

        for nome in self._ESPERADOS:
            with self.subTest(sinal=nome):
                self.assertIn(
                    nome,
                    trecho,
                    "a etapa estrategica nao recebe {}; ela vai interpretar "
                    "sem esse sinal.".format(nome),
                )


if __name__ == "__main__":
    unittest.main()
