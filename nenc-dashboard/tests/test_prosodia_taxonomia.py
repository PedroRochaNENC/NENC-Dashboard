"""Taxonomia de temas do projeto: leitura do campo e persistência.

A Análise Geral conta quantos respondentes trataram de cada subtema da
taxonomia cadastrada na Preparação. O campo é texto livre, um subtema por
linha: o que estiver fora do formato fica de fora da análise, mas não some do
campo nem derruba as linhas boas.
"""

import ast
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import auth, prosodia_db
from utils.prosodia_temas import (
    TAXONOMIA_PROVISORIA,
    TAXONOMIA_VALIDADA,
    Subtema,
    parse_taxonomia,
)

PREPARACAO = Path(__file__).resolve().parent.parent / "modules" / "prosodia" / "preparacao.py"


class ParseTaxonomiaTests(unittest.TestCase):
    def test_reads_category_subtheme_and_definition_in_order(self):
        taxonomia = parse_taxonomia(
            "Equipamentos > Máquina fora de serviço: parada, quebrada ou em manutenção\n"
            "Atendimento > Atendente da unidade\n"
            "Equipamentos > Secagem incompleta: roupa sai úmida"
        )

        self.assertEqual(
            taxonomia.subtemas,
            (
                Subtema("Equipamentos", "Máquina fora de serviço", "parada, quebrada ou em manutenção"),
                Subtema("Atendimento", "Atendente da unidade"),
                Subtema("Equipamentos", "Secagem incompleta", "roupa sai úmida"),
            ),
        )
        self.assertEqual(taxonomia.subtemas[1].rotulo, "Atendimento > Atendente da unidade")
        self.assertEqual(
            {categoria: [s.nome for s in subtemas] for categoria, subtemas in taxonomia.categorias().items()},
            {
                "Equipamentos": ["Máquina fora de serviço", "Secagem incompleta"],
                "Atendimento": ["Atendente da unidade"],
            },
        )
        self.assertEqual(list(taxonomia.categorias()), ["Equipamentos", "Atendimento"])

    def test_pasted_lists_lose_their_markers_and_blank_lines(self):
        taxonomia = parse_taxonomia(
            "- Ambiente > Limpeza\n"
            "\n"
            "* Ambiente > Lotação\n"
            "• Pagamento > Preço\n"
            "1. Pagamento > App\n"
            "2) Atendimento > Suporte\n"
            "   \n"
        )

        self.assertEqual(
            [s.rotulo for s in taxonomia.subtemas],
            [
                "Ambiente > Limpeza",
                "Ambiente > Lotação",
                "Pagamento > Preço",
                "Pagamento > App",
                "Atendimento > Suporte",
            ],
        )
        self.assertEqual(taxonomia.linhas_invalidas, ())

    def test_bad_lines_are_reported_without_dropping_the_good_ones(self):
        taxonomia = parse_taxonomia(
            "Equipamentos:\n"
            "Ambiente > Limpeza\n"
            "> Sem categoria\n"
            "Sem subtema >\n"
            "Três > níveis > demais\n"
            "Pagamento > Preço"
        )

        self.assertEqual(
            [s.rotulo for s in taxonomia.subtemas], ["Ambiente > Limpeza", "Pagamento > Preço"]
        )
        self.assertEqual(
            taxonomia.linhas_invalidas,
            ("Equipamentos:", "> Sem categoria", "Sem subtema >", "Três > níveis > demais"),
        )

    def test_repeated_subtheme_counts_once_ignoring_case_and_accents(self):
        taxonomia = parse_taxonomia(
            "Ambiente > Lotação: academia cheia\n"
            "ambiente > lotacao\n"
            "Ambiente > Limpeza"
        )

        self.assertEqual(
            [s.rotulo for s in taxonomia.subtemas], ["Ambiente > Lotação", "Ambiente > Limpeza"]
        )
        self.assertEqual(taxonomia.subtemas[0].definicao, "academia cheia")
        self.assertEqual(taxonomia.repetidos, ("Ambiente > lotacao",))

    def test_category_keeps_its_first_spelling(self):
        taxonomia = parse_taxonomia("Atendimento > Cordialidade\natendimento > Suporte")

        self.assertEqual(list(taxonomia.categorias()), ["Atendimento"])
        self.assertEqual(taxonomia.subtemas[1].rotulo, "Atendimento > Suporte")

    def test_definition_starts_at_the_first_colon(self):
        taxonomia = parse_taxonomia("Pagamento > Estorno: demora > 7 dias: cliente reclama")

        self.assertEqual(
            taxonomia.subtemas,
            (Subtema("Pagamento", "Estorno", "demora > 7 dias: cliente reclama"),),
        )

    def test_spaces_are_collapsed(self):
        taxonomia = parse_taxonomia("  Ambiente   >   Espera  por máquina :  fim de   semana ")

        self.assertEqual(
            taxonomia.subtemas, (Subtema("Ambiente", "Espera por máquina", "fim de semana"),)
        )

    def test_empty_field(self):
        for texto in (None, "", "  \n \n"):
            with self.subTest(texto=texto):
                taxonomia = parse_taxonomia(texto)
                self.assertEqual(taxonomia.subtemas, ())
                self.assertEqual(taxonomia.linhas_invalidas, ())
                self.assertEqual(taxonomia.categorias(), {})


class TaxonomiaPersistenceTests(unittest.TestCase):
    def setUp(self):
        # Banco descartavel: sem NENC_DB_PATH o teste escreveria no banco local.
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.environment = patch.dict(
            os.environ, {"NENC_DB_PATH": str(self.database_path)}
        )
        self.environment.start()
        organization = auth.create_organization(
            "Organization One", database_path=self.database_path, _bootstrap=True
        )
        admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        prosodia_db.init_db()
        for patcher in (
            patch("utils.prosodia_db._require_write", return_value=admin),
            patch("utils.prosodia_db._active_organization_id", return_value=organization.id),
            patch("utils.prosodia_db._claim_external_project_resources"),
            patch("utils.prosodia_db._audit"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _taxonomia(self, project_id):
        project = prosodia_db.get_project(project_id)
        return project["taxonomia"], project["taxonomia_status"]

    def test_new_project_has_no_taxonomy(self):
        taxonomia, status = self._taxonomia(prosodia_db.create_project("Projeto"))

        self.assertFalse(taxonomia)
        self.assertFalse(status)

    def test_taxonomy_is_saved_on_create(self):
        project_id = prosodia_db.create_project(
            "Recados", taxonomia="Ambiente > Limpeza", taxonomia_status=TAXONOMIA_PROVISORIA
        )

        self.assertEqual(
            self._taxonomia(project_id), ("Ambiente > Limpeza", TAXONOMIA_PROVISORIA)
        )

    def test_update_without_taxonomy_keeps_the_saved_one(self):
        # Como qr_codes.py e whatsapp_campanhas.py: regravam o projeto
        # repassando os proprios campos, sem a taxonomia.
        project_id = prosodia_db.create_project(
            "Recados", taxonomia="Ambiente > Limpeza", taxonomia_status=TAXONOMIA_VALIDADA
        )

        prosodia_db.update_project(project_id, name="Recados", qr_verification_text="Novo")

        self.assertEqual(self._taxonomia(project_id), ("Ambiente > Limpeza", TAXONOMIA_VALIDADA))

    def test_update_changes_it_and_empty_text_clears_it(self):
        project_id = prosodia_db.create_project(
            "Recados", taxonomia="Ambiente > Limpeza", taxonomia_status=TAXONOMIA_PROVISORIA
        )

        prosodia_db.update_project(
            project_id,
            name="Recados",
            taxonomia="Ambiente > Lotação",
            taxonomia_status=TAXONOMIA_VALIDADA,
        )
        self.assertEqual(self._taxonomia(project_id), ("Ambiente > Lotação", TAXONOMIA_VALIDADA))

        prosodia_db.update_project(project_id, name="Recados", taxonomia="", taxonomia_status="")
        taxonomia, status = self._taxonomia(project_id)
        self.assertFalse(taxonomia)
        self.assertFalse(status)

    def test_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            prosodia_db.create_project("Projeto", taxonomia="A > B", taxonomia_status="aprovada")

        project_id = prosodia_db.create_project(
            "Projeto", taxonomia="A > B", taxonomia_status=TAXONOMIA_PROVISORIA
        )
        with self.assertRaises(ValueError):
            prosodia_db.update_project(project_id, name="Projeto", taxonomia_status="aprovada")
        self.assertEqual(self._taxonomia(project_id), ("A > B", TAXONOMIA_PROVISORIA))


class LegacyDatabaseMigrationTests(unittest.TestCase):
    def test_existing_projects_get_no_taxonomy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            auth.create_organization(
                "Legacy Owner", database_path=database_path, _bootstrap=True
            )
            database = sqlite3.connect(database_path)
            try:
                database.execute(
                    "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                database.execute("INSERT INTO projects (name) VALUES ('Projeto antigo')")
                database.commit()
            finally:
                database.close()

            with patch.dict(os.environ, {"NENC_DB_PATH": str(database_path)}):
                prosodia_db.init_db()
                prosodia_db.init_db()  # a migracao roda a cada pagina: precisa ser idempotente

            database = sqlite3.connect(database_path)
            try:
                linha = database.execute(
                    "SELECT taxonomia, taxonomia_status FROM projects"
                ).fetchone()
            finally:
                database.close()

        self.assertEqual(linha, (None, None))


class PreparacaoGravaATaxonomiaTests(unittest.TestCase):
    """A Preparação é a única tela que conhece a taxonomia, e é ela que a grava.

    As demais regravam o projeto sem esses campos, e None neles mantém o valor
    salvo (test_qr_verification.TestPagesKeepProjectFields).
    """

    def test_create_and_update_pass_the_taxonomy(self):
        arvore = ast.parse(PREPARACAO.read_text(encoding="utf-8"))
        chamadas = {}
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Name)
                and no.func.id in ("create_project", "update_project")
            ):
                chamadas[no.func.id] = {keyword.arg for keyword in no.keywords}

        self.assertEqual(set(chamadas), {"create_project", "update_project"})
        for nome, argumentos in chamadas.items():
            with self.subTest(chamada=nome):
                self.assertLessEqual({"taxonomia", "taxonomia_status"}, argumentos)


if __name__ == "__main__":
    unittest.main()
