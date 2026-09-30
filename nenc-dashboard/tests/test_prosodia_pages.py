"""Contrato das páginas de prosódia quanto à leitura do Sincronizado.

O CSV Sincronizado é lido dentro de try/except em várias telas. Enquanto o
except era `pass`, um arquivo ilegível virava dataframe vazio sem rastro: o
prompt saía sem pitch, loudness nem as três dimensões, e o relatório concluía
que o áudio "não tem dados acústicos". Foi assim que 15 análises individuais do
Smart Fit nasceram afirmando uma lacuna que não existia.

As páginas são scripts Streamlit e não rodam num teste de unidade. O que dá
para garantir sem executá-las é que nenhuma leitura do Sincronizado volta a
falhar em silêncio.
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
        self.assertIn("audio_analise.py", nomes)
        self.assertIn("audio_timeline.py", nomes)

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
        for nome in ("analise_geral.py", "audio_analise.py", "audio_timeline.py"):
            with self.subTest(pagina=nome):
                fonte = (PAGES_DIR / nome).read_text(encoding="utf-8")
                self.assertIn("logging.getLogger(__name__)", fonte)


if __name__ == "__main__":
    unittest.main()
