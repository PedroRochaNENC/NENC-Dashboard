"""Unidades da organização e o vínculo com os QR codes (spec de Unidades, §3).

A unidade é da organização. O áudio a herda do QR code por onde entrou, pelo
código do QR, e a correção feita à mão na página do áudio vence o vínculo. A
unidade é calculada na leitura: ligar um QR vale também para os áudios que já
tinham chegado por ele.
"""

import ast
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts import backfill_audio_qr_code as backfill
from utils import auth, prosodia_db
from utils import prosodia_geracao as geracao
from utils import prosodia_overview as overview
from utils import prosodia_relatorio as rel
from utils import prosodia_summary as summary
from utils import prosodia_unidades as pu
from utils.prosodia_project_types import PESQUISA_OPINIAO
from utils.prosodia_temas import Linha, parse_taxonomia

APP_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = APP_ROOT / "modules" / "prosodia"


class NormalizarUnidadeTests(unittest.TestCase):
    def test_cleans_the_fields_and_empty_optionals_become_none(self):
        self.assertEqual(
            pu.normalizar_unidade("  Smart Fit   Paulista ", " SP012 ", "São Paulo", "sp"),
            {"nome": "Smart Fit Paulista", "codigo": "SP012", "cidade": "São Paulo", "uf": "SP"},
        )
        self.assertEqual(
            pu.normalizar_unidade("Moema"),
            {"nome": "Moema", "codigo": None, "cidade": None, "uf": None},
        )

    def test_name_is_required_and_uf_has_two_letters(self):
        with self.assertRaises(ValueError):
            pu.normalizar_unidade("   ")
        for uf in ("São Paulo", "S", "1A"):
            with self.subTest(uf=uf), self.assertRaises(ValueError):
                pu.normalizar_unidade("Moema", uf=uf)

    def test_numeric_code_read_by_the_spreadsheet_loses_the_decimal(self):
        self.assertEqual(pu.normalizar_unidade("Moema", 120.0)["codigo"], "120")


class LerPlanilhaTests(unittest.TestCase):
    def test_accepts_header_variants_and_skips_blank_rows(self):
        unidades, erros = pu.ler_planilha([
            {"Código": "SP012", "Nome da unidade": "Paulista", "Município": "São Paulo", "Estado": "sp"},
            {"Código": None, "Nome da unidade": float("nan"), "Município": "", "Estado": None},
            {"Código": "", "Nome da unidade": "Tijuca", "Município": "Rio de Janeiro", "Estado": "RJ"},
        ])

        self.assertEqual(erros, [])
        self.assertEqual(
            unidades,
            [
                {"nome": "Paulista", "codigo": "SP012", "cidade": "São Paulo", "uf": "SP"},
                {"nome": "Tijuca", "codigo": None, "cidade": "Rio de Janeiro", "uf": "RJ"},
            ],
        )

    def test_bad_rows_are_reported_with_the_spreadsheet_line(self):
        unidades, erros = pu.ler_planilha([
            {"codigo": "SP012", "nome": "Paulista", "uf": "SP"},
            {"codigo": "SP013", "nome": "", "uf": "SP"},
            {"codigo": "SP012", "nome": "Paulista de novo", "uf": "SP"},
            {"codigo": "RJ004", "nome": "Tijuca", "uf": "Rio"},
        ])

        self.assertEqual([u["codigo"] for u in unidades], ["SP012"])
        self.assertEqual(len(erros), 3)
        self.assertTrue(erros[0].startswith("Linha 3:"))
        self.assertIn("SP012", erros[1])
        self.assertTrue(erros[2].startswith("Linha 5:"))

    def test_spreadsheet_without_the_columns_says_which_are_expected(self):
        unidades, erros = pu.ler_planilha([{"academia": "Paulista"}])

        self.assertEqual(unidades, [])
        self.assertIn("codigo, nome, cidade e uf", erros[0])


class PlanejarImportacaoTests(unittest.TestCase):
    EXISTENTES = [
        {"id": 1, "codigo": "SP012", "nome": "Paulista"},
        {"id": 2, "codigo": None, "nome": "Cartaz Moema"},
        {"id": 3, "codigo": "RJ004", "nome": "Tijuca"},
    ]

    def _plano(self, *unidades):
        return pu.planejar_importacao([pu.normalizar_unidade(**u) for u in unidades], self.EXISTENTES)

    def test_same_code_updates(self):
        (item,) = self._plano({"nome": "Smart Fit Paulista", "codigo": "SP012", "uf": "SP"})

        self.assertEqual((item["acao"], item["unidade_id"]), (pu.ATUALIZAR, 1))

    def test_name_completes_a_unit_created_without_code(self):
        # As unidades criadas pela sugestão dos QR codes nascem sem código.
        (item,) = self._plano({"nome": "cartaz moema", "codigo": "SP020"})

        self.assertEqual((item["acao"], item["unidade_id"], item["codigo"]), (pu.ATUALIZAR, 2, "SP020"))

    def test_name_never_takes_a_unit_with_another_code(self):
        (item,) = self._plano({"nome": "Tijuca", "codigo": "RJ999"})

        self.assertEqual(item["acao"], pu.CRIAR)

    def test_row_without_code_keeps_the_saved_code(self):
        (item,) = self._plano({"nome": "Tijuca", "cidade": "Rio de Janeiro"})

        self.assertEqual((item["acao"], item["unidade_id"], item["codigo"]), (pu.ATUALIZAR, 3, "RJ004"))

    def test_each_saved_unit_is_updated_once(self):
        primeiro, segundo = self._plano({"nome": "Cartaz Moema"}, {"nome": "Cartaz Moema"})

        self.assertEqual((primeiro["acao"], segundo["acao"]), (pu.ATUALIZAR, pu.CRIAR))


class SugestaoPelosQrsTests(unittest.TestCase):
    QRS = [
        {"project_id": 16, "project_name": "Piloto", "code": "16-01", "name": "Paulista - Recepção"},
        {"project_id": 16, "project_name": "Piloto", "code": "16-02", "name": "Moema"},
        {"project_id": 16, "project_name": "Piloto", "code": "16-03", "name": "Tijuca"},
        {"project_id": 16, "project_name": "Piloto", "code": "", "name": "Sem código"},
    ]

    def test_proposes_the_qr_name_and_skips_linked_qrs(self):
        propostas = pu.propor_pelos_qrs(
            self.QRS, [(16, "16-03")], [{"id": 7, "nome": "moema", "codigo": None}]
        )

        self.assertEqual(
            [(p["qr_code"], p["nome"], p["unidade_id"]) for p in propostas],
            [("16-01", "Paulista - Recepção", None), ("16-02", "moema", 7)],
        )

    def test_same_name_forms_one_unit_and_existing_name_links_to_it(self):
        grupos = pu.agrupar_sugestoes(
            [
                {"project_id": 16, "qr_code": "16-01", "nome": "Smart Fit Paulista"},
                {"project_id": 16, "qr_code": "16-04", "nome": "smart fit paulista "},
                {"project_id": 16, "qr_code": "16-02", "nome": "Moema"},
                {"project_id": 16, "qr_code": "16-05", "nome": "  "},
            ],
            [{"id": 7, "nome": "Moema", "codigo": "SP020"}],
        )

        self.assertEqual(
            grupos,
            [
                {"nome": "Smart Fit Paulista", "unidade_id": None, "qrs": [(16, "16-01"), (16, "16-04")]},
                {"nome": "Moema", "unidade_id": 7, "qrs": [(16, "16-02")]},
            ],
        )


class _BancoComDuasOrganizacoes(unittest.TestCase):
    def setUp(self):
        # Banco descartavel: sem NENC_DB_PATH o teste escreveria no banco local.
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.environment = patch.dict(os.environ, {"NENC_DB_PATH": str(self.database_path)})
        self.environment.start()
        self.org_um = auth.create_organization("Smart Fit", database_path=self.database_path, _bootstrap=True)
        admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=self.org_um.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.org_dois = auth.create_organization("Outra rede", actor=admin, database_path=self.database_path)
        prosodia_db.init_db()
        self.organizacao_ativa = self.org_um.id
        for patcher in (
            patch("utils.prosodia_db._require_write", return_value=admin),
            patch("utils.prosodia_db._active_organization_id", side_effect=lambda: self.organizacao_ativa),
            patch("utils.prosodia_db._claim_external_project_resources"),
            patch("utils.prosodia_db._audit"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _na_organizacao(self, organizacao):
        anterior = self.organizacao_ativa
        self.organizacao_ativa = organizacao.id

        def voltar():
            self.organizacao_ativa = anterior

        self.addCleanup(voltar)


class UnidadeDoAudioTests(_BancoComDuasOrganizacoes):
    def setUp(self):
        super().setUp()
        self.project_id = prosodia_db.create_project("Piloto VoC")
        self.paulista = prosodia_db.create_unidade("Smart Fit Paulista", "SP012", "São Paulo", "SP")
        self.moema = prosodia_db.create_unidade("Smart Fit Moema", "SP020")

    def _audio(self, sessao, qr_code_code=None, qr_code_name=None):
        return prosodia_db.create_audio(
            self.project_id, sessao, qr_code_name=qr_code_name or qr_code_code, qr_code_code=qr_code_code
        )

    def _unidade(self, audio_id):
        audio = prosodia_db.get_audio(audio_id)
        return audio["unidade_id"], audio["unidade_origem"]

    def test_audio_inherits_the_unit_of_its_qr_code_including_earlier_audios(self):
        anterior = self._audio("wa_1_1", "16-01")
        self.assertEqual(self._unidade(anterior), (None, None))

        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)
        posterior = self._audio("wa_2_2", "16-01")

        for audio_id in (anterior, posterior):
            with self.subTest(audio=audio_id):
                self.assertEqual(self._unidade(audio_id), (self.paulista, prosodia_db.UNIDADE_PELO_QR))
        audio = prosodia_db.get_audio(anterior)
        self.assertEqual((audio["unidade_nome"], audio["unidade_codigo"]), ("Smart Fit Paulista", "SP012"))

    def test_relinking_the_qr_moves_all_its_audios_and_none_unlinks(self):
        audio_id = self._audio("wa_1_1", "16-01")
        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)

        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.moema)
        self.assertEqual(self._unidade(audio_id)[0], self.moema)
        self.assertEqual(prosodia_db.get_qr_unidades(self.project_id)["16-01"]["unidade_id"], self.moema)

        prosodia_db.set_qr_unidade(self.project_id, "16-01", None)
        self.assertEqual(self._unidade(audio_id), (None, None))
        self.assertEqual(prosodia_db.get_qr_unidades(self.project_id), {})

    def test_the_link_is_by_code_not_by_name(self):
        # O nome do QR pode ser renomeado na tela; o código não.
        audio_id = self._audio("wa_1_1", "16-01", qr_code_name="Cartaz antigo")
        sem_codigo = self._audio("wa_2_2", qr_code_name="16-01")
        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)

        self.assertEqual(self._unidade(audio_id)[0], self.paulista)
        self.assertEqual(self._unidade(sem_codigo), (None, None))

    def test_manual_correction_wins_and_none_follows_the_qr_again(self):
        audio_id = self._audio("wa_1_1", "16-01")
        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)

        prosodia_db.set_audio_unidade(audio_id, self.moema)
        self.assertEqual(self._unidade(audio_id), (self.moema, prosodia_db.UNIDADE_MANUAL))

        prosodia_db.set_audio_unidade(audio_id, None)
        self.assertEqual(self._unidade(audio_id), (self.paulista, prosodia_db.UNIDADE_PELO_QR))

    def test_audio_without_qr_has_no_unit_until_corrected(self):
        audio_id = self._audio("upload_1")

        self.assertEqual(self._unidade(audio_id), (None, None))
        prosodia_db.set_audio_unidade(audio_id, self.moema)
        self.assertEqual(self._unidade(audio_id), (self.moema, prosodia_db.UNIDADE_MANUAL))

    def test_qr_of_another_project_does_not_leak(self):
        outro = prosodia_db.create_project("Onda 2")
        audio_id = prosodia_db.create_audio(outro, "wa_9_9", qr_code_code="16-01")
        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)

        self.assertEqual(self._unidade(audio_id), (None, None))

    def test_deleting_the_unit_unlinks_qrs_and_manual_audios_follow_the_qr(self):
        pelo_qr = self._audio("wa_1_1", "16-01")
        corrigido = self._audio("wa_2_2", "16-02")
        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)
        prosodia_db.set_qr_unidade(self.project_id, "16-02", self.moema)
        prosodia_db.set_audio_unidade(corrigido, self.paulista)

        prosodia_db.delete_unidade(self.paulista)

        self.assertEqual(self._unidade(pelo_qr), (None, None))
        self.assertEqual(self._unidade(corrigido), (self.moema, prosodia_db.UNIDADE_PELO_QR))
        self.assertEqual(list(prosodia_db.get_qr_unidades(self.project_id)), ["16-02"])

    def test_counts_qrs_and_audios_of_each_unit(self):
        self._audio("wa_1_1", "16-01")
        self._audio("wa_2_2", "16-01")
        corrigido = self._audio("wa_3_3", "16-02")
        prosodia_db.set_qr_unidade(self.project_id, "16-01", self.paulista)
        prosodia_db.set_qr_unidade(self.project_id, "16-02", self.paulista)
        prosodia_db.set_audio_unidade(corrigido, self.moema)

        contagem = {u["nome"]: (u["n_qr"], u["n_audios"]) for u in prosodia_db.get_unidades()}

        self.assertEqual(contagem, {"Smart Fit Moema": (0, 1), "Smart Fit Paulista": (2, 2)})

    def test_blank_qr_code_is_refused(self):
        with self.assertRaises(ValueError):
            prosodia_db.set_qr_unidade(self.project_id, "  ", self.paulista)


class CadastroDeUnidadesTests(_BancoComDuasOrganizacoes):
    def test_code_is_unique_in_the_organization_only(self):
        prosodia_db.create_unidade("Paulista", "SP012")
        with self.assertRaises(ValueError):
            prosodia_db.create_unidade("Outra Paulista", "SP012")

        self._na_organizacao(self.org_dois)
        prosodia_db.create_unidade("Paulista da outra rede", "SP012")

    def test_many_units_without_code(self):
        prosodia_db.create_unidade("Paulista")
        prosodia_db.create_unidade("Moema")

        self.assertEqual([u["nome"] for u in prosodia_db.get_unidades()], ["Moema", "Paulista"])

    def test_update_rewrites_the_fields_and_refuses_a_taken_code(self):
        paulista = prosodia_db.create_unidade("Paulista", "SP012")
        moema = prosodia_db.create_unidade("Moema")

        prosodia_db.update_unidade(moema, "Smart Fit Moema", "SP020", "São Paulo", "sp")
        with self.assertRaises(ValueError):
            prosodia_db.update_unidade(paulista, "Paulista", "SP020")

        unidades = {u["id"]: u for u in prosodia_db.get_unidades()}
        self.assertEqual(
            (unidades[moema]["nome"], unidades[moema]["codigo"], unidades[moema]["uf"]),
            ("Smart Fit Moema", "SP020", "SP"),
        )
        self.assertEqual(unidades[paulista]["codigo"], "SP012")

    def test_all_organizations_mode_lists_everything_but_cannot_create(self):
        prosodia_db.create_unidade("Paulista")
        self._na_organizacao(self.org_dois)
        prosodia_db.create_unidade("Centro")
        self.organizacao_ativa = 0

        self.assertEqual(
            sorted((u["nome"], u["organization_name"]) for u in prosodia_db.get_unidades()),
            [("Centro", "Outra rede"), ("Paulista", "Smart Fit")],
        )
        with self.assertRaises(ValueError):
            prosodia_db.create_unidade("Sem organização")
        with self.assertRaises(ValueError):
            prosodia_db.importar_unidades([pu.normalizar_unidade("Sem organização")])

    def test_import_creates_and_updates_in_one_go(self):
        criada_pelo_qr = prosodia_db.create_unidade("Cartaz Moema")
        prosodia_db.create_unidade("Paulista", "SP012")
        unidades, erros = pu.ler_planilha([
            {"codigo": "SP012", "nome": "Smart Fit Paulista", "cidade": "São Paulo", "uf": "SP"},
            {"codigo": "SP020", "nome": "Cartaz Moema", "cidade": "São Paulo", "uf": "SP"},
            {"codigo": "RJ004", "nome": "Smart Fit Tijuca", "cidade": "Rio de Janeiro", "uf": "RJ"},
        ])
        self.assertEqual(erros, [])

        contagem = prosodia_db.importar_unidades(unidades)

        self.assertEqual(contagem, {"criadas": 1, "atualizadas": 2})
        por_codigo = {u["codigo"]: u for u in prosodia_db.get_unidades()}
        self.assertEqual(set(por_codigo), {"SP012", "SP020", "RJ004"})
        self.assertEqual(por_codigo["SP020"]["id"], criada_pelo_qr)
        self.assertEqual(por_codigo["SP012"]["nome"], "Smart Fit Paulista")

    def test_import_without_code_keeps_the_saved_code(self):
        moema = prosodia_db.create_unidade("Moema", "SP020")

        prosodia_db.importar_unidades([pu.normalizar_unidade("moema", cidade="São Paulo", uf="SP")])

        (unidade,) = prosodia_db.get_unidades()
        self.assertEqual(
            (unidade["id"], unidade["codigo"], unidade["cidade"]), (moema, "SP020", "São Paulo")
        )

    def test_import_is_all_or_nothing(self):
        # ler_planilha já tira o código repetido; aqui ele chega direto, e a
        # segunda linha bate no índice único depois de a primeira entrar.
        with self.assertRaises(ValueError):
            prosodia_db.importar_unidades([
                pu.normalizar_unidade("Centro", "SP030"),
                pu.normalizar_unidade("Centro Novo", "SP030"),
            ])

        self.assertEqual(prosodia_db.get_unidades(), [])


class IsolamentoDasUnidadesTests(_BancoComDuasOrganizacoes):
    def setUp(self):
        super().setUp()
        self.project_id = prosodia_db.create_project("Piloto VoC")
        self.audio_id = prosodia_db.create_audio(self.project_id, "wa_1_1", qr_code_code="16-01")
        self._na_organizacao(self.org_dois)
        self.unidade_alheia = prosodia_db.create_unidade("Centro")
        self.organizacao_ativa = self.org_um.id

    def test_another_organization_unit_is_invisible(self):
        self.assertEqual(prosodia_db.get_unidades(), [])
        self.assertEqual(prosodia_db.get_unidades(self.org_dois.id), [])
        with self.assertRaises(ValueError):
            prosodia_db.update_unidade(self.unidade_alheia, "Tomada")
        with self.assertRaises(ValueError):
            prosodia_db.delete_unidade(self.unidade_alheia)

    def test_qr_and_audio_never_link_to_another_organization_unit(self):
        with self.assertRaises(ValueError):
            prosodia_db.set_qr_unidade(self.project_id, "16-01", self.unidade_alheia)
        with self.assertRaises(ValueError):
            prosodia_db.set_audio_unidade(self.audio_id, self.unidade_alheia)

        # Nem em "Todas as organizações": a unidade tem de ser da organização
        # do projeto.
        self.organizacao_ativa = 0
        with self.assertRaises(ValueError):
            prosodia_db.set_qr_unidade(self.project_id, "16-01", self.unidade_alheia)
        self.assertIsNone(prosodia_db.get_audio(self.audio_id)["unidade_id"])

    def test_another_organization_cannot_read_or_link_the_project_qrs(self):
        self._na_organizacao(self.org_dois)
        with self.assertRaises(ValueError):
            prosodia_db.get_qr_unidades(self.project_id)
        with self.assertRaises(ValueError):
            prosodia_db.set_qr_unidade(self.project_id, "16-01", self.unidade_alheia)
        with self.assertRaises(ValueError):
            prosodia_db.set_audio_unidade(self.audio_id, self.unidade_alheia)


class MigracaoTests(unittest.TestCase):
    def test_existing_audios_get_the_new_columns_empty(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "nenc-insights.db"
            auth.create_organization("Legacy Owner", database_path=database_path, _bootstrap=True)
            database = sqlite3.connect(database_path)
            try:
                database.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
                database.execute(
                    "CREATE TABLE audios (id INTEGER PRIMARY KEY, project_id INTEGER, session_id TEXT NOT NULL)"
                )
                database.execute("INSERT INTO projects (name) VALUES ('Projeto antigo')")
                database.execute("INSERT INTO audios (project_id, session_id) VALUES (1, 'wa_1_1')")
                database.commit()
            finally:
                database.close()

            with patch.dict(os.environ, {"NENC_DB_PATH": str(database_path)}):
                prosodia_db.init_db()
                prosodia_db.init_db()  # a migracao roda a cada pagina: precisa ser idempotente

            database = sqlite3.connect(database_path)
            try:
                linha = database.execute("SELECT qr_code_code, unidade_manual_id FROM audios").fetchone()
                tabelas = {
                    row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
            finally:
                database.close()

        self.assertEqual(linha, (None, None))
        self.assertLessEqual({"unidades", "qr_unidades"}, tabelas)


class BackfillDoCodigoTests(unittest.TestCase):
    QRS = [
        {"code": "16-01", "name": "Paulista - Recepção"},
        {"code": "16-02", "name": "Moema"},
        {"code": "16-03", "name": "moema "},
        {"code": "16-04", "name": None},
    ]

    def test_matches_the_code_first_then_the_name(self):
        self.assertEqual(backfill.codigo_do_qr("16-04", self.QRS), (backfill.PREENCHER, "16-04"))
        self.assertEqual(
            backfill.codigo_do_qr("paulista -  recepção", self.QRS), (backfill.PREENCHER, "16-01")
        )

    def test_ambiguous_or_missing_names_stay_empty(self):
        self.assertEqual(backfill.codigo_do_qr("Moema", self.QRS), (backfill.AMBIGUO, None))
        self.assertEqual(backfill.codigo_do_qr("Cartaz apagado", self.QRS), (backfill.NAO_ENCONTRADO, None))

    def _banco(self, pasta: Path) -> Path:
        database_path = pasta / "prosodia.db"
        database = sqlite3.connect(database_path)
        try:
            database.executescript(
                """
                CREATE TABLE projects (id INTEGER PRIMARY KEY, api_project_id INTEGER);
                CREATE TABLE audios (id INTEGER PRIMARY KEY, project_id INTEGER,
                                     qr_code_name TEXT, qr_code_code TEXT);
                INSERT INTO projects VALUES (1, 16), (2, NULL);
                INSERT INTO audios VALUES
                    (1, 1, 'Paulista - Recepção', NULL),
                    (2, 1, 'Moema', NULL),
                    (3, 1, '16-04', NULL),
                    (4, 1, 'Paulista - Recepção', '16-09'),
                    (5, 2, 'Sem API', NULL),
                    (6, 1, NULL, NULL);
                """
            )
            database.commit()
        finally:
            database.close()
        return database_path

    def _rodar(self, database_path: Path, *extra) -> str:
        saida = io.StringIO()
        with patch.object(sys, "argv", ["backfill", "--database", str(database_path), *extra]), \
                patch.dict(os.environ, {"WHATSAPP_API_URL": "http://api", "WHATSAPP_API_KEY": "chave"}), \
                patch.object(backfill, "_qrs_na_api", return_value=self.QRS) as api, \
                redirect_stdout(saida):
            self.assertEqual(backfill.main(), 0)
        self.assertEqual(api.call_count, 1)  # uma chamada por projeto da API
        return saida.getvalue()

    def _codigos(self, database_path: Path):
        database = sqlite3.connect(database_path)
        try:
            return dict(database.execute("SELECT id, qr_code_code FROM audios"))
        finally:
            database.close()

    def test_dry_run_writes_nothing_and_apply_fills_only_the_matches(self):
        with tempfile.TemporaryDirectory() as pasta:
            database_path = self._banco(Path(pasta))

            self.assertIn("Dry-run", self._rodar(database_path))
            self.assertEqual(self._codigos(database_path)[1], None)

            self._rodar(database_path, "--apply")
            self.assertEqual(
                self._codigos(database_path),
                {1: "16-01", 2: None, 3: "16-04", 4: "16-09", 5: None, 6: None},
            )
            self.assertTrue(any((Path(pasta) / "backups").glob("prosodia_before_qr_code_backfill_*.db")))


# ---------------------------------------------------------------------------
# Fase 2: a unidade nas telas, na exportação e na Análise Geral
# ---------------------------------------------------------------------------

def _linha_de_audio(audio_id, unidade=None, codigo=None, qr="Cartaz", sessao=None):
    """Uma linha como get_audios_for_interviews a devolve, só com o que o filtro usa."""
    return {
        "id": audio_id,
        "session_id": sessao or "wa_5511900000{:03d}_{}".format(audio_id, audio_id),
        "qr_code_name": qr,
        "unidade_id": audio_id * 10 if unidade else None,
        "unidade_nome": unidade,
        "unidade_codigo": codigo,
    }


class FiltroPorUnidadeTests(unittest.TestCase):
    def test_label_has_the_code_and_without_unit_says_so(self):
        self.assertEqual(overview.unidade_label(_linha_de_audio(1, "Paulista", "SP012")), "Paulista · SP012")
        self.assertEqual(overview.unidade_label(_linha_de_audio(2, "Moema")), "Moema")
        self.assertEqual(overview.unidade_label(_linha_de_audio(3)), overview.SEM_UNIDADE)

    def test_options_by_count_with_no_unit_last_and_empty_without_units(self):
        audios = [
            _linha_de_audio(1), _linha_de_audio(2), _linha_de_audio(3),
            _linha_de_audio(4, "Moema"), _linha_de_audio(5, "Paulista"), _linha_de_audio(6, "Paulista"),
        ]

        self.assertEqual(overview.unidade_options(audios), ["Paulista", "Moema", overview.SEM_UNIDADE])
        self.assertEqual(overview.unidade_options(audios[:3]), [])

    def test_filter_and_description(self):
        audios = [_linha_de_audio(1, "Paulista"), _linha_de_audio(2, "Moema"), _linha_de_audio(3)]

        filtrados = overview.filter_audios(audios, unidades=["Moema", overview.SEM_UNIDADE])

        self.assertEqual([a["id"] for a in filtrados], [2, 3])
        self.assertEqual(
            overview.filter_description(["Cartaz"], None, None, (None, None), ["Moema"]),
            "unidade Moema; QR code Cartaz",
        )
        self.assertEqual(
            overview.filter_description([], None, None, (None, None), ["Moema", "Paulista"]),
            "unidades Moema, Paulista",
        )


class RankingPorUnidadeTests(unittest.TestCase):
    def test_counts_audios_with_unit_and_the_rest_apart(self):
        entries = pd.DataFrame({"unidade": ["Paulista", "Paulista", "Moema", "", "Tijuca", "Centro"]})

        ranking = summary.unidade_ranking(entries, top=2)

        # No empate vale a ordem em que a unidade aparece, como no ranking por QR.
        self.assertEqual([(l["nome"], l["entradas"]) for l in ranking["linhas"]], [("Paulista", 2), ("Moema", 1)])
        self.assertEqual(
            (ranking["distintas"], ranking["outras"], ranking["outras_entradas"], ranking["sem_unidade"],
             ranking["total"], ranking["maximo"]),
            (4, 2, 2, 1, 5, 2),
        )


class LeiturasComUnidadeTests(_BancoComDuasOrganizacoes):
    def test_list_readers_bring_the_unit(self):
        project_id = prosodia_db.create_project("Piloto VoC")
        pelo_qr = prosodia_db.create_audio(project_id, "wa_1_1", qr_code_name="Cartaz", qr_code_code="16-01")
        sem = prosodia_db.create_audio(project_id, "wa_2_2")
        unidade = prosodia_db.create_unidade("Paulista", "SP012", "São Paulo", "SP")
        prosodia_db.set_qr_unidade(project_id, "16-01", unidade)

        for nome, leitura in (
            ("get_audios", prosodia_db.get_audios),
            ("get_audio_entries", prosodia_db.get_audio_entries),
            ("get_audios_for_interviews", prosodia_db.get_audios_for_interviews),
        ):
            with self.subTest(leitura=nome):
                por_id = {a["id"]: a for a in leitura(project_id)}
                self.assertEqual(
                    (por_id[pelo_qr]["unidade_id"], por_id[pelo_qr]["unidade_nome"], por_id[pelo_qr]["unidade_codigo"]),
                    (unidade, "Paulista", "SP012"),
                )
                self.assertIsNone(por_id[sem]["unidade_id"])

        entradas = summary.project_entries(project_id).set_index("id")
        self.assertEqual((entradas.loc[pelo_qr, "unidade"], entradas.loc[sem, "unidade"]), ("Paulista · SP012", ""))


TAXONOMIA_FASE2 = parse_taxonomia("Ambiente > Lotação\nAmbiente > Limpeza\nAtendimento > Recepção")
LOTACAO, LIMPEZA, RECEPCAO = (s.rotulo for s in TAXONOMIA_FASE2.subtemas)


def _audio_com_unidade(audio_id, unidade, *subtemas, numero=None):
    """Áudio do WhatsApp classificado; `numero` repetido é o mesmo respondente na Opinião."""
    linhas = tuple(Linha(i, "Respondente", "Fala {}.".format(i), i * 4.0, i * 4.0 + 3.5, i) for i in range(1, 4))
    classificacao = {
        "respondente": None,
        "respondente_incerto": False,
        "temas": [{"subtema": s, "polaridade": "negativa", "evidencia": "", "trechos": [{"linha": 1}]} for s in subtemas],
        "emergentes": [],
    }
    return rel.AudioDoProjeto(
        audio_id,
        "wa_+552199{:06d}_{}".format(audio_id if numero is None else numero, audio_id),
        linhas,
        classificacao,
        datetime(2026, 10, 1) + timedelta(hours=audio_id),
        unidade=unidade,
    )


def _agregar_fase2(audios, recorte=None):
    ids = [a.audio_id for a in audios] if recorte is None else recorte
    return rel.agregar(audios, ids, TAXONOMIA_FASE2, PESQUISA_OPINIAO)


class ColunaUnidadesTests(unittest.TestCase):
    def setUp(self):
        # Lotação espalhada pelas 3 unidades (2, 2 e 1 de 4 com unidade);
        # Limpeza concentrada em Moema (3 de 4); um respondente sem unidade e um
        # que mandou áudios de duas unidades.
        self.audios = [
            _audio_com_unidade(1, "Paulista", LOTACAO),
            _audio_com_unidade(2, "Paulista", LOTACAO, LIMPEZA),
            _audio_com_unidade(3, "Moema", LOTACAO, LIMPEZA),
            _audio_com_unidade(4, "Moema", LIMPEZA),
            _audio_com_unidade(5, "Moema", LIMPEZA, numero=4),
            _audio_com_unidade(6, "Tijuca", LOTACAO, numero=1),
            _audio_com_unidade(7, "", LOTACAO, RECEPCAO),
            _audio_com_unidade(8, "Moema", LIMPEZA),
            _audio_com_unidade(9, "Moema", LOTACAO),
        ]

    def _tema(self, agregacao, rotulo):
        return next(t for t in agregacao.temas if t.subtema == rotulo)

    def test_counts_respondents_per_unit_and_the_ones_without_unit(self):
        agregacao = _agregar_fase2(self.audios)

        self.assertEqual(agregacao.unidades, ("Moema", "Paulista", "Tijuca"))
        self.assertEqual(agregacao.sem_unidade, 1)
        lotacao = self._tema(agregacao, LOTACAO)
        # O respondente 1 mandou áudios por Paulista e por Tijuca: conta nas duas
        # e uma vez só no total do tema.
        self.assertEqual(lotacao.respondentes, 5)
        self.assertEqual(lotacao.unidades, {"Paulista": 2, "Moema": 2, "Tijuca": 1})
        self.assertEqual(lotacao.com_unidade, 4)
        self.assertEqual(self._tema(agregacao, LIMPEZA).unidades, {"Paulista": 1, "Moema": 3})

    def test_cell_says_how_many_units_or_where_it_concentrates(self):
        agregacao = _agregar_fase2(self.audios)

        self.assertEqual(rel.texto_unidades(self._tema(agregacao, LOTACAO), 3), "3 de 3")
        self.assertEqual(
            rel.texto_unidades(self._tema(agregacao, LIMPEZA), 3), "concentrada em Moema (3 de 4 respondentes)"
        )
        self.assertEqual(rel.texto_unidades(self._tema(agregacao, RECEPCAO), 3), "—")

    def test_fewer_than_three_respondents_never_concentrate(self):
        agregacao = _agregar_fase2([
            _audio_com_unidade(1, "Moema", LIMPEZA),
            _audio_com_unidade(2, "Moema", LIMPEZA),
            _audio_com_unidade(3, "Paulista", LOTACAO),
        ])

        self.assertEqual(rel.texto_unidades(self._tema(agregacao, LIMPEZA), 2), "1 de 2")

    def test_table_shows_the_column_and_the_legend(self):
        agregacao = _agregar_fase2(self.audios)

        tabela = rel.tabela_temas_markdown(agregacao)

        self.assertIn("| Unidades |", tabela.splitlines()[0])
        self.assertIn("concentrada em Moema (3 de 4 respondentes)", tabela)
        self.assertIn("\"Unidades\" diz em quantas das 3 unidades do recorte", tabela)
        self.assertIn("1 respondente sem unidade fica fora dessa coluna.", tabela)

    def test_one_unit_or_none_hides_the_column(self):
        uma = _agregar_fase2([a for a in self.audios if a.unidade in ("Moema", "")])
        nenhuma = _agregar_fase2([replace(a, unidade="") for a in self.audios])

        for agregacao in (uma, nenhuma):
            with self.subTest(unidades=agregacao.unidades):
                self.assertFalse(agregacao.por_unidade)
                self.assertNotIn("Unidades", rel.tabela_temas_markdown(agregacao))

    def test_matrix_rows_carry_the_unit(self):
        agregacao = _agregar_fase2(self.audios)
        linhas = [
            rel.LinhaMatriz(a.audio_id, agregacao.aliases[a.audio_id], 0.5, 0.2, 0.6, 0.1, ("Lotação",), a.unidade)
            for a in self.audios[:2]
        ]
        agregacao.matriz = rel.Matriz(12, 0.5, 0.2, rel._grupo(linhas), rel._grupo([]))

        texto = rel.matriz_markdown(agregacao)

        self.assertIn("| Respondente | Unidade | Texto | Voz | Índice | Temas |", texto)
        self.assertIn("| Áudio 01 | Paulista |", texto)


class RecorteComUnidadeTests(unittest.TestCase):
    def test_unit_is_saved_and_old_snapshots_read_as_all_units(self):
        recorte = rel.Recorte(qr=("Cartaz",), unidade=("Moema",))

        lido = rel.Recorte.de_dict(recorte.como_dict())

        self.assertEqual(lido.unidade, ("Moema",))
        self.assertTrue(lido.mesmo_filtro(recorte))
        self.assertEqual(rel.Recorte.de_dict({"qr": [], "origem": []}).unidade, ())
        self.assertFalse(rel.Recorte(unidade=("Moema",)).mesmo_filtro(rel.Recorte()))

    def test_delta_compares_only_with_the_same_unit_cut(self):
        audios = [_audio_com_unidade(i, "Moema", LOTACAO) for i in range(1, 7)]
        ids = [a.audio_id for a in audios]
        antes = datetime(2026, 10, 1, 3, 30).strftime("%Y-%m-%d %H:%M:%S")
        outra_unidade = {"id": 2, "created_at": antes, "analysis_text": "",
                         "temas": {"audios": [1, 2, 3], "recorte": rel.Recorte(unidade=("Paulista",)).como_dict()}}
        mesma_unidade = {"id": 1, "created_at": antes, "analysis_text": "",
                         "temas": {"audios": [1, 2], "recorte": rel.Recorte(unidade=("Moema",)).como_dict()}}
        antiga_sem_snapshot = {"id": 3, "created_at": antes, "analysis_text": "", "temas": None}

        escolhida = rel.escolher_analise_anterior(
            [outra_unidade, mesma_unidade], rel.Recorte(unidade=("Moema",)), audios, ids
        )

        self.assertEqual(escolhida.analise_id, 1)
        # Análise anterior à v2 cobriu o projeto inteiro: não serve a um recorte por unidade.
        self.assertIsNone(rel.escolher_analise_anterior(
            [antiga_sem_snapshot], rel.Recorte(unidade=("Moema",)), audios, ids
        ))

    def test_page_cut_selecting_every_unit_is_no_filter(self):
        periodo = (date(2026, 10, 1), date(2026, 10, 2))

        todas = geracao.recorte_da_pagina([], ["A"], periodo, [], "", ["Moema", "Paulista"], ["Paulista", "Moema"])
        uma = geracao.recorte_da_pagina([], ["A"], periodo, [], "unidade Moema", ["Moema"], ["Paulista", "Moema"])

        self.assertEqual((todas.unidade, uma.unidade), ((), ("Moema",)))

    def test_generation_takes_the_unit_of_each_audio(self):
        com = geracao._audio_do_projeto(dict(_linha_de_audio(1, "Paulista", "SP012"), created_at=None), {}, {})
        sem = geracao._audio_do_projeto(dict(_linha_de_audio(2), created_at=None), {}, {})

        self.assertEqual((com.unidade, sem.unidade), ("Paulista · SP012", ""))


class ContratoDasPaginasTests(unittest.TestCase):
    def test_every_import_that_saves_the_qr_name_also_saves_the_code(self):
        verificadas = 0
        for pagina in sorted(PAGES_DIR.glob("*.py")):
            arvore = ast.parse(pagina.read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                if not (isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "create_audio"):
                    continue
                argumentos = {keyword.arg for keyword in no.keywords}
                if "qr_code_name" not in argumentos:
                    continue
                verificadas += 1
                with self.subTest(pagina=pagina.name, linha=no.lineno):
                    self.assertIn("qr_code_code", argumentos)
        # Hoje: três em entrevistas.py e uma em whatsapp_monitor.py.
        self.assertGreaterEqual(verificadas, 4)

    def test_units_page_is_in_the_module_menu(self):
        fonte = (APP_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('_page("modules/prosodia/unidades.py", "Unidades", "buildings")', fonte)

    def test_general_analysis_filters_by_unit_and_saves_it_in_the_cut(self):
        arvore = ast.parse((PAGES_DIR / "analise_geral.py").read_text(encoding="utf-8"))
        chamadas = {
            no.func.attr: no
            for no in ast.walk(arvore)
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
            and no.func.attr in ("filter_audios", "recorte_da_pagina")
        }

        self.assertIn("filtro_unidade", [ast.unparse(a) for a in chamadas["filter_audios"].args])
        self.assertLessEqual(
            {"unidades", "unidades_do_projeto"}, {k.arg for k in chamadas["recorte_da_pagina"].keywords}
        )


if __name__ == "__main__":
    unittest.main()
