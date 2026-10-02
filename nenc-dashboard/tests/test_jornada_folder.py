"""Leitura da pasta do projeto: papel de cada arquivo pela estrutura NENC.

A pasta sintética reproduz a estrutura dos projetos (2.DADOS/2.2 e 2.3,
1.GESTAO_PROJETOS, 3.DRAFTS RELATÓRIOS, 4.ARQUIVOS AUXILIARES) com marcas e
lojas genéricas.
"""

import tempfile
import unittest
from pathlib import Path

from utils.jornada_folder import (
    CONFIG_NAME,
    is_probably_template,
    load_config,
    scan_project,
    summarize,
)

FILES = {
    "1.GESTAO_PROJETOS/1.1 Documentação/TERMO DE ABERTURA.docx": b"docx",
    "1.GESTAO_PROJETOS/1.3.Pré campo/Lista de Checagem setup.xlsx": b"x",
    "1.GESTAO_PROJETOS/1.4.Campo/Relação Coletas.xlsx": b"x",
    "1.GESTAO_PROJETOS/1.4.Campo/Recrutamento/agenda.xlsx": b"x",
    "2.DADOS/2.0 Dados Originais (Backup)/2.Kexxu/Pt01-Emb-DSP1234.mp4": b"v",
    "2.DADOS/2.2.Dados Processados/Eyetracking/Jornadas Livres-1234/Pt01-JLivre-DSP1234.csv": b"f",
    "2.DADOS/2.2.Dados Processados/Eyetracking/Jornadas Livres-1234/Pt01-JLivre-DSP1234-out.mp4": b"v",
    "2.DADOS/2.2.Dados Processados/Eyetracking/Jornadas Livres-1234/sem-nome.csv": b"f",
    "2.DADOS/2.2.Dados Processados/Videos Processados Heatmap/Embalagens/Pt02-Emb-Atacado.mp4": b"v",
    # O nome não diz a tarefa ("Simulada", como no 1060); a pasta diz.
    "2.DADOS/2.2.Dados Processados/Videos Processados Heatmap/Jornadas Estimuladas-Atacado/"
    "Pt14-Simulada-Atacado.mp4": b"v",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/DGSP1234/DGSP1234-INDIVIDUAL2.csv": b"d",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/DGSP1234/DGSP1234.bsproj": b"b",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/DGSP1234/DGSP1234_Data/a.dat": b"b",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/DGSP1234/heatmap-1234.png": b"p",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/estatisticas-todos.xlsx": b"x",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/estatisticas-convertido.xlsx": b"x",
    "2.DADOS/2.3.Dados Consolidados (finais para análise)/~$estatisticas-todos.xlsx": b"x",
    "3.DRAFTS RELATÓRIOS/3.1 Análise e material de apoio/apoio.xlsx": b"x",
    "3.DRAFTS RELATÓRIOS/3.2 Relatório/Relatório - Estudo V0.pptx": b"r",
    "3.DRAFTS RELATÓRIOS/3.2 Relatório/Relatório - Estudo V2.pptx": b"r",
    "4.ARQUIVOS AUXILIARES/000-Fluxo Experimental.xlsx": b"x",
    "4.ARQUIVOS AUXILIARES/Fotos Gôndolas/DSP-1234/Gondola.jpeg": b"i",
    "4.ARQUIVOS AUXILIARES/Fotos Gôndolas/DSP-1234/Thumbs.db": b"t",
    "4.ARQUIVOS AUXILIARES/Fotos Gôndolas/Atacado/Gondola-editada.jpg": b"i",
    "4.ARQUIVOS AUXILIARES/Fotos pacotes/Marca A/Frente 1.jpeg": b"i",
    "4.ARQUIVOS AUXILIARES/Fotos pacotes/Marca A/marca_a-editada.jpeg": b"i",
    "4.ARQUIVOS AUXILIARES/Fotos participantes/Pt01.jpeg": b"pessoal",
}


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "Estudo"
        for rel, content in FILES.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def _entry(self, suffix, entries=None):
        matches = [e for e in (entries or scan_project(self.root)) if e.rel_path.endswith(suffix)]
        self.assertEqual(len(matches), 1, suffix)
        return matches[0]

    def test_each_folder_gets_its_role(self):
        entries = scan_project(self.root)
        roles = {suffix: self._entry(suffix, entries).role for suffix in (
            "DGSP1234-INDIVIDUAL2.csv", "/estatisticas-todos.xlsx", "Pt01-JLivre-DSP1234.csv",
            "Pt01-JLivre-DSP1234-out.mp4", "Pt02-Emb-Atacado.mp4", "heatmap-1234.png",
            "Relação Coletas.xlsx", "TERMO DE ABERTURA.docx", "000-Fluxo Experimental.xlsx",
            "Relatório - Estudo V2.pptx", "DSP-1234/Gondola.jpeg", "Marca A/Frente 1.jpeg",
        )}
        self.assertEqual(roles, {
            "DGSP1234-INDIVIDUAL2.csv": "dados",
            "/estatisticas-todos.xlsx": "dados",
            "Pt01-JLivre-DSP1234.csv": "quadros",
            "Pt01-JLivre-DSP1234-out.mp4": "video_cena",
            "Pt02-Emb-Atacado.mp4": "video_heatmap",
            "heatmap-1234.png": "imagem",
            "Relação Coletas.xlsx": "registro_campo",
            "TERMO DE ABERTURA.docx": "documento",
            "000-Fluxo Experimental.xlsx": "documento",
            "Relatório - Estudo V2.pptx": "documento",
            "DSP-1234/Gondola.jpeg": "imagem",
            "Marca A/Frente 1.jpeg": "imagem",
        })

    def test_store_brand_task_and_view_come_from_folder_and_name(self):
        entries = scan_project(self.root)
        video = self._entry("Pt01-JLivre-DSP1234-out.mp4", entries)
        self.assertEqual((video.meta["participant"], video.meta["task"], video.meta["store"]),
                         ("Pt01", "livre", "1234"))
        heatmap = self._entry("Pt02-Emb-Atacado.mp4", entries)
        self.assertEqual((heatmap.meta["task"], heatmap.meta["store"]), ("embalagens", "atacado"))
        unnamed = self._entry("Pt14-Simulada-Atacado.mp4", entries)
        self.assertEqual((unnamed.role, unnamed.meta["task"], unnamed.meta["task_from_folder"]),
                         ("video_heatmap", "estimulada", "Jornadas Estimuladas-Atacado"))
        self.assertNotIn("task_from_folder", heatmap.meta)
        self.assertEqual(self._entry("heatmap-1234.png", entries).meta, {"category": "heatmap", "store": "1234"})
        gondola = self._entry("DSP-1234/Gondola.jpeg", entries)
        self.assertEqual((gondola.meta["category"], gondola.meta["store"]), ("gondola", "1234"))
        edited_gondola = self._entry("Gondola-editada.jpg", entries).meta
        self.assertEqual((edited_gondola["store"], edited_gondola.get("edited")), ("atacado", True))
        self.assertNotIn("edited", gondola.meta)
        package = self._entry("Frente 1.jpeg", entries)
        self.assertEqual((package.meta["brand"], package.meta["view"]), ("Marca A", "frente"))
        self.assertTrue(self._entry("marca_a-editada.jpeg", entries).meta["edited"])

    def test_personal_data_backups_and_tool_files_stay_out_with_a_reason(self):
        entries = scan_project(self.root)
        cases = {
            "Fotos participantes/Pt01.jpeg": "dado pessoal",
            "Recrutamento/agenda.xlsx": "dados pessoais",
            "2.Kexxu/Pt01-Emb-DSP1234.mp4": "backup",
            "DGSP1234.bsproj": "ferramenta",
            "a.dat": "ferramenta",
            "Thumbs.db": "sistema",
            "~$estatisticas-todos.xlsx": "trava",
            "estatisticas-convertido.xlsx": "duplicada",
            "apoio.xlsx": "material de apoio",
            "Lista de Checagem setup.xlsx": "gestão",
            "sem-nome.csv": "nome sem participante",
            "Relatório - Estudo V0.pptx": "versão anterior",
        }
        for suffix, reason in cases.items():
            entry = self._entry(suffix, entries)
            self.assertEqual(entry.role, "ignorado", suffix)
            self.assertIn(reason, entry.reason, suffix)

    def test_summary_counts_files_and_bytes_by_role(self):
        summary = summarize(scan_project(self.root))
        self.assertEqual(summary["quadros"], {"files": 1, "bytes": 1})
        self.assertEqual(summary["imagem"]["files"], 5)
        self.assertNotIn("nenhum", summary)

    def test_the_toml_adds_folders_stores_brands_and_exclusions(self):
        (self.root / "Fotos lojas" / "Loja Centro").mkdir(parents=True)
        (self.root / "Fotos lojas" / "Loja Centro" / "g.jpeg").write_bytes(b"i")
        (self.root / "Exports extras").mkdir()
        (self.root / "Exports extras" / "extra.csv").write_bytes(b"d")
        (self.root / CONFIG_NAME).write_text(
            'ignorar = ["**/marca_a-editada*"]\n'
            '[lojas]\n"Loja Centro" = "9999"\n'
            '[marcas]\n"Marca A" = "Marca Alfa"\n'
            '[pastas]\nfotos_gondola = ["Fotos lojas"]\ndados = ["Exports extras"]\n',
            encoding="utf-8",
        )
        entries = scan_project(self.root)
        store = self._entry("Loja Centro/g.jpeg", entries)
        self.assertEqual((store.role, store.meta["store"]), ("imagem", "9999"))
        self.assertEqual(self._entry("extra.csv", entries).role, "dados")
        self.assertEqual(self._entry("Frente 1.jpeg", entries).meta["brand"], "Marca Alfa")
        self.assertIn(CONFIG_NAME, self._entry("marca_a-editada.jpeg", entries).reason)
        self.assertFalse(any(e.rel_path == CONFIG_NAME for e in entries))

    def test_an_unknown_role_in_the_toml_is_refused(self):
        (self.root / CONFIG_NAME).write_text('[pastas]\nqualquer = ["x"]\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(self.root)

    def test_a_missing_folder_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            scan_project(self.root / "nao-existe")


class TemplateTests(unittest.TestCase):
    def test_empty_forms_are_templates_and_reports_are_not(self):
        form = "\n".join(["TERMO DE ABERTURA", "OBJETIVO DO ESTUDO:", "MÉTODO", "ANÁLISE"] + [
            "{}.".format(i) for i in range(1, 30)])
        self.assertTrue(is_probably_template(form))
        labels = "\n".join("Etapa {} — Data de início".format(i) for i in range(60))
        self.assertTrue(is_probably_template(labels))
        report = "\n".join(
            ["Objetivo do Estudo", "O estudo busca reproduzir a experiência de compra em dois contextos de varejo, "
             "avaliando como as consumidoras se comportam ao longo da jornada de compra da categoria."] * 30)
        self.assertFalse(is_probably_template(report))
        self.assertTrue(is_probably_template(""))


if __name__ == "__main__":
    unittest.main()
