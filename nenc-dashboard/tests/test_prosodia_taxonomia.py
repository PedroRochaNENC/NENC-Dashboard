"""Taxonomia de temas do projeto: leitura do campo e persistência.

A Análise Geral conta quantos respondentes trataram de cada subtema da
taxonomia cadastrada na Preparação. O campo é texto livre, um subtema por
linha: o que estiver fora do formato fica de fora da análise, mas não some do
campo nem derruba as linhas boas.
"""

import ast
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import auth, prosodia_db
from utils.prosodia_temas import (
    TAXONOMIA_PROVISORIA,
    TAXONOMIA_VALIDADA,
    Subtema,
    formatar_taxonomia,
    parse_taxonomia,
    versao_taxonomia,
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


class VersaoEFormatoTests(unittest.TestCase):
    def test_version_ignores_order_and_spacing_but_not_labels_or_definitions(self):
        base = versao_taxonomia(parse_taxonomia("Ambiente > Lotação: fila\nAtendimento > Cordialidade"))

        self.assertEqual(
            base,
            versao_taxonomia(parse_taxonomia("Atendimento  >  Cordialidade\n- Ambiente > Lotação:   fila")),
        )
        self.assertNotEqual(
            base, versao_taxonomia(parse_taxonomia("Ambiente > Lotação: espera\nAtendimento > Cordialidade"))
        )
        self.assertNotEqual(
            base, versao_taxonomia(parse_taxonomia("Ambiente > Lotação: fila\nAtendimento > Simpatia"))
        )
        self.assertEqual(versao_taxonomia(parse_taxonomia("")), "")

    def test_format_is_the_inverse_of_parse(self):
        taxonomia = parse_taxonomia(
            "- Ambiente > Lotação:  fila\n\nAtendimento > Cordialidade\nlinha fora do formato"
        )

        texto = formatar_taxonomia(taxonomia)

        self.assertEqual(texto, "Ambiente > Lotação: fila\nAtendimento > Cordialidade")
        self.assertEqual(parse_taxonomia(texto).subtemas, taxonomia.subtemas)


class _BancoDescartavel(unittest.TestCase):
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


class TaxonomiaPersistenceTests(_BancoDescartavel):
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

    def test_set_project_taxonomy_touches_only_the_taxonomy(self):
        # Caminho da taxonomia provisória proposta pela IA na Análise Geral.
        project_id = prosodia_db.create_project(
            "Recados",
            especialidade="Lavanderia",
            qr_verification_text="Texto do QR",
            duracao_esperada="ate_30s",
        )

        prosodia_db.set_project_taxonomy(project_id, "Ambiente > Lotação: fila", TAXONOMIA_PROVISORIA)

        project = prosodia_db.get_project(project_id)
        self.assertEqual(
            self._taxonomia(project_id), ("Ambiente > Lotação: fila", TAXONOMIA_PROVISORIA)
        )
        self.assertEqual(
            (project["especialidade"], project["qr_verification_text"], project["duracao_esperada"]),
            ("Lavanderia", "Texto do QR", "ate_30s"),
        )

    def test_set_project_taxonomy_refuses_unknown_status(self):
        project_id = prosodia_db.create_project("Recados")

        with self.assertRaises(ValueError):
            prosodia_db.set_project_taxonomy(project_id, "A > B", "aprovada")
        self.assertFalse(self._taxonomia(project_id)[0])


class AudioTemasPersistenceTests(_BancoDescartavel):
    TEMAS = {
        "respondente": None,
        "respondente_incerto": False,
        "temas": [
            {
                "subtema": "Ambiente > Lotação",
                "polaridade": "negativa",
                "evidencia": "fila no sábado",
                "trechos": [{"linha": 1, "segmento_idx": 4, "inicio_s": 2.5}],
            }
        ],
        "emergentes": [],
    }

    def setUp(self):
        super().setUp()
        self.project_id = prosodia_db.create_project("Recados")
        self.audio_id = prosodia_db.create_audio(self.project_id, "wa_1")

    def _linhas_na_tabela(self):
        with prosodia_db._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM audio_temas").fetchone()[0]

    def test_saves_and_reads_back_by_audio(self):
        prosodia_db.save_audio_temas(self.audio_id, "chave-1", "versao-1", "gpt-4.1-mini", self.TEMAS)

        salvos = prosodia_db.get_audio_temas_for_project(self.project_id)

        self.assertEqual(list(salvos), [self.audio_id])
        item = salvos[self.audio_id]
        self.assertEqual(
            (item["chave"], item["versao_taxonomia"], item["modelo"]),
            ("chave-1", "versao-1", "gpt-4.1-mini"),
        )
        self.assertEqual(item["temas"], self.TEMAS)

    def test_a_new_classification_replaces_the_old_one(self):
        prosodia_db.save_audio_temas(self.audio_id, "chave-1", "versao-1", "modelo", self.TEMAS)
        prosodia_db.save_audio_temas(self.audio_id, "chave-2", "versao-2", "modelo", {"temas": []})

        item = prosodia_db.get_audio_temas_for_project(self.project_id)[self.audio_id]

        self.assertEqual((item["chave"], item["temas"]), ("chave-2", {"temas": []}))
        self.assertEqual(self._linhas_na_tabela(), 1)

    def test_audios_of_other_projects_do_not_show_up(self):
        outro_projeto = prosodia_db.create_project("Outro")
        outro_audio = prosodia_db.create_audio(outro_projeto, "wa_2")
        prosodia_db.save_audio_temas(outro_audio, "chave", "versao", "modelo", self.TEMAS)

        self.assertEqual(prosodia_db.get_audio_temas_for_project(self.project_id), {})

    def test_unreadable_json_is_left_out(self):
        prosodia_db.save_audio_temas(self.audio_id, "chave", "versao", "modelo", self.TEMAS)
        with prosodia_db._connect() as conn:
            conn.execute("UPDATE audio_temas SET temas_json = '{quebrado'")

        self.assertEqual(prosodia_db.get_audio_temas_for_project(self.project_id), {})

    def test_deleting_the_audio_deletes_its_classification(self):
        prosodia_db.save_audio_temas(self.audio_id, "chave", "versao", "modelo", self.TEMAS)

        with patch("utils.prosodia_db._remove_from_knowledge_base"):
            prosodia_db.delete_audio(self.audio_id)

        self.assertEqual(self._linhas_na_tabela(), 0)


class SnapshotDaAnaliseTests(_BancoDescartavel):
    """O snapshot de cada Análise Geral é o que a seguinte usa para o Δ."""

    SNAPSHOT = {
        "versao": 1,
        "recorte": {"qr": ["Centro"], "origem": [], "periodo": [], "descricao": "QR code Centro"},
        "audios": [3, 5],
        "respondentes": 2,
        "temas": [{"subtema": "Ambiente > Lotação", "respondentes": 2, "faixa": "Menção isolada"}],
    }

    def setUp(self):
        super().setUp()
        self.project_id = prosodia_db.create_project("Recados")

    def test_snapshot_is_read_back_as_temas(self):
        prosodia_db.save_project_analysis(self.project_id, "gpt-4.1", "texto", [], temas=self.SNAPSHOT)

        historico = prosodia_db.get_project_analyses(self.project_id)
        ultima = prosodia_db.get_latest_project_analysis(self.project_id)

        self.assertEqual(historico[0]["temas"], self.SNAPSHOT)
        self.assertEqual(ultima["temas"], self.SNAPSHOT)
        self.assertNotIn("temas_json", ultima)

    def test_analysis_without_snapshot_reads_none(self):
        prosodia_db.save_project_analysis(self.project_id, "gpt-4.1", "texto")

        self.assertIsNone(prosodia_db.get_project_analyses(self.project_id)[0]["temas"])
        self.assertIsNone(prosodia_db.get_latest_project_analysis(self.project_id)["temas"])

    def test_unreadable_snapshot_reads_none(self):
        prosodia_db.save_project_analysis(self.project_id, "gpt-4.1", "texto", temas=self.SNAPSHOT)
        with prosodia_db._connect() as conn:
            conn.execute("UPDATE project_analyses SET temas_json = '{quebrado'")

        self.assertIsNone(prosodia_db.get_latest_project_analysis(self.project_id)["temas"])


class ScriptsDoServidorTests(unittest.TestCase):
    """Os scripts de manutenção rodam no Python do servidor, que não tem pandas.

    migrate_legacy_data.py importa prosodia_db, que importa prosodia_temas: se
    algum deles passar a importar pandas no topo, o script quebra só lá.
    """

    def test_prosodia_db_imports_without_pandas(self):
        codigo = "import sys; import utils.prosodia_db; print('pandas' in sys.modules)"
        saida = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(saida.stdout.strip(), "False")


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

    def test_existing_project_analyses_get_no_snapshot(self):
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
                database.execute(
                    "CREATE TABLE project_analyses (id INTEGER PRIMARY KEY, project_id INTEGER, "
                    "model TEXT, analysis_text TEXT, citations TEXT, created_at TEXT)"
                )
                database.execute("INSERT INTO projects (name) VALUES ('Projeto antigo')")
                database.execute(
                    "INSERT INTO project_analyses (project_id, model, analysis_text, citations, created_at) "
                    "VALUES (1, 'gpt-4.1', 'Analise antiga', '[]', '2026-09-30 10:00:00')"
                )
                database.commit()
            finally:
                database.close()

            with patch.dict(os.environ, {"NENC_DB_PATH": str(database_path)}):
                prosodia_db.init_db()
                prosodia_db.init_db()

            database = sqlite3.connect(database_path)
            try:
                linha = database.execute(
                    "SELECT analysis_text, temas_json FROM project_analyses"
                ).fetchone()
            finally:
                database.close()

        self.assertEqual(linha, ("Analise antiga", None))


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
