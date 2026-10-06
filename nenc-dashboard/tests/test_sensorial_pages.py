"""Contrato das páginas do Teste Sensorial, lido do código-fonte.

As páginas são scripts Streamlit e não rodam num teste de unidade; o que dá
para garantir sem executá-las é que toda página passa pela guarda do módulo,
que as de envio exigem escrita, que nenhuma volta ao fluxo antigo por
organização nem lê nome de participante, e que todo salto aponta para uma
página que existe.
"""

import ast
import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = APP_ROOT / "modules" / "teste_sensorial"
PAGE_PATH = re.compile(r"modules/teste_sensorial/[a-z_]+\.py")


def _pages():
    return sorted(path for path in PAGES_DIR.glob("*.py") if not path.name.startswith("_"))


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield node


class SensorialPagesTests(unittest.TestCase):
    def test_the_sweep_finds_the_pages(self):
        """Glob vazio passaria os outros testes por vacuidade."""
        names = {path.name for path in _pages()}
        self.assertTrue({"projetos.py", "preparacao.py", "analise_geral.py", "sinais.py", "participantes.py",
                         "uploads.py"} <= names)

    def test_every_page_goes_through_the_module_guard(self):
        for path in _pages():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            guards = [
                node for node in _calls(tree)
                if node.func.attr in ("require_module", "require_module_write")
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "teste_sensorial"
            ]
            with self.subTest(page=path.name):
                self.assertTrue(guards, "página sem auth.require_module")

    def test_uploads_requires_write_access(self):
        uploads = PAGES_DIR / "uploads.py"
        if not uploads.exists():
            self.skipTest("Uploads ainda não existe")
        tree = ast.parse(uploads.read_text(encoding="utf-8"))
        self.assertIn("require_module_write", {node.func.attr for node in _calls(tree)})

    def test_no_page_goes_back_to_the_old_flow_or_reads_names(self):
        forbidden = ("hydrate_session_state", "save_session_state", "teste_sensorial_db", "data_loader",
                     "participante_original", "Participant Name")
        for path in _pages():
            source = path.read_text(encoding="utf-8")
            for name in forbidden:
                with self.subTest(page=path.name, name=name):
                    self.assertNotIn(name, source)

    def test_every_page_reference_exists(self):
        sources = [APP_ROOT / "app.py", APP_ROOT / "home.py"] + _pages()
        for source in sources:
            for reference in PAGE_PATH.findall(source.read_text(encoding="utf-8")):
                with self.subTest(source=source.name, reference=reference):
                    self.assertTrue((APP_ROOT / reference).exists(), reference)

    def test_the_old_modules_are_gone(self):
        for name in ("utils/teste_sensorial_db.py", "utils/data_loader.py", "utils/resampler.py",
                     "modules/teste_sensorial/media_geral.py", "modules/teste_sensorial/timeline.py"):
            with self.subTest(name=name):
                self.assertFalse((APP_ROOT / name).exists())


if __name__ == "__main__":
    unittest.main()
