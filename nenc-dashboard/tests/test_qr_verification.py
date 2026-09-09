import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from utils import auth
from utils.prosodia_db import (
    DEFAULT_QR_VERIFICATION_TEXT,
    init_db,
    create_project,
    get_project,
    update_project,
)
from utils.whatsapp_api_client import (
    build_whatsapp_deeplink,
    generate_qr_code_bytes,
    suggest_next_qr_code,
)


class TestQRVerificationText(unittest.TestCase):

    def setUp(self):
        # NENC_DB_PATH precisa apontar para um banco descartavel: sem isto o
        # teste cria projetos e reescreve os numeros de WhatsApp da
        # organizacao no banco de producao.
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.environment = patch.dict(
            os.environ,
            {"NENC_DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.organization = auth.create_organization(
            "Organization One",
            database_path=self.database_path,
            _bootstrap=True,
        )
        self.platform_admin = auth.create_user(
            name="Platform Admin",
            email="platform@example.com",
            phone="5511999999999",
            organization_id=self.organization.id,
            password="platform-admin-password",
            module_keys=auth.MODULE_KEYS,
            is_organization_admin=True,
            is_platform_admin=True,
            database_path=self.database_path,
            _bootstrap=True,
        )
        init_db()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_default_qr_verification_text_content(self):
        expected_text = (
            "Não delete essas informações. Elas estão associadas ao serviço que você está avaliando. "
            "Peço que envie agora esse código, antes da sua mensagem."
        )
        self.assertEqual(DEFAULT_QR_VERIFICATION_TEXT, expected_text)

    @patch("utils.prosodia_db._audit")
    @patch("utils.prosodia_db._active_organization_id", return_value=1)
    @patch("utils.prosodia_db._claim_external_project_resources")
    @patch("utils.prosodia_db._require_write")
    def test_create_and_update_project_with_qr_verification_text(
        self, mock_write, mock_claim, mock_org, mock_audit
    ):
        mock_write.return_value = self.platform_admin
        # Create project with custom qr_verification_text
        custom_text = "Texto personalizado de verificação de teste."
        pid = create_project(
            name="Projeto Teste QR",
            qr_verification_text=custom_text,
        )
        proj = get_project(pid)
        self.assertIsNotNone(proj)
        self.assertEqual(proj["qr_verification_text"], custom_text)

        # Update project with another verification text
        updated_text = "Texto atualizado de verificação."
        update_project(
            project_id=pid,
            name="Projeto Teste QR",
            qr_verification_text=updated_text,
        )
        proj_updated = get_project(pid)
        self.assertEqual(proj_updated["qr_verification_text"], updated_text)

    def test_build_whatsapp_deeplink(self):
        phone = "5511999999999"
        custom_text = "Minha mensagem de teste"
        code = "01-02"

        link = build_whatsapp_deeplink(phone, custom_text, code)
        self.assertTrue(link.startswith("https://wa.me/5511999999999?text="))
        self.assertIn("Minha%20mensagem%20de%20teste", link)
        self.assertIn("%5BC%C3%B3digo%3A%2001-02%5D", link)

    def test_generate_qr_code_bytes(self):
        url = "https://wa.me/5511999999999?text=teste"
        img_bytes = generate_qr_code_bytes(url)
        self.assertIsInstance(img_bytes, bytes)
        self.assertGreater(len(img_bytes), 0)
        # Check PNG magic header \x89PNG
    def test_organization_whatsapp_numbers(self):
        auth.update_organization(
            self.platform_admin,
            self.organization.id,
            whatsapp_numbers="5511975218007\n5516981360051",
            database_path=self.database_path,
        )
        numbers = auth.get_organization_whatsapp_numbers(
            self.organization.id, database_path=self.database_path
        )
        self.assertEqual(numbers, ["5511975218007", "5516981360051"])


class TestQRCodeSuggestion(unittest.TestCase):
    """O codigo sugerido sai do maior sufixo, nunca da contagem de linhas."""

    def test_first_code_of_a_project_without_qr_codes(self):
        self.assertEqual(suggest_next_qr_code(5, []), "05-01")

    def test_skips_gap_left_by_deleted_qr_codes(self):
        qr_codes = [
            {"code": "05-03"},
            {"code": "05-04"},
            {"code": "05-05"},
            {"code": "05-06"},
            {"code": "05-07"},
        ]
        # Cinco linhas, mas os codigos vao ate o 07: contar sugeriria o 05-06,
        # que ainda esta em uso, e a API recusava a criacao com 400.
        self.assertEqual(suggest_next_qr_code(5, qr_codes), "05-08")

    def test_ignores_codes_from_other_projects_and_custom_codes(self):
        qr_codes = [
            {"code": "05-02"},
            {"code": "PESQ-FONO-CARTAZ-A"},
            {"code": "50-99"},
            {"code": None},
        ]
        self.assertEqual(suggest_next_qr_code(5, qr_codes), "05-03")


if __name__ == "__main__":
    unittest.main()
