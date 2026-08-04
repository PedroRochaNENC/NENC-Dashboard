import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

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
)


class TestQRVerificationText(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_default_qr_verification_text_content(self):
        expected_text = (
            "Não delete essas informações. Elas estão associadas ao serviço que você está avaliando. "
            "Peço que envie agora esse código, antes da sua mensagem."
        )
        self.assertEqual(DEFAULT_QR_VERIFICATION_TEXT, expected_text)

    @patch("utils.prosodia_db._audit")
    @patch("utils.prosodia_db._active_organization_id", return_value=1)
    @patch("utils.prosodia_db._claim_external_project_resources")
    def test_create_and_update_project_with_qr_verification_text(self, mock_claim, mock_org, mock_audit):
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
        from utils.auth import get_organization_whatsapp_numbers, update_organization, User
        mock_admin = User(
            id=1,
            name="Admin",
            email="admin@example.com",
            phone="5511999999999",
            organization_id=1,
            organization_name="Org",
            is_organization_admin=True,
            is_platform_admin=True,
            is_active=True,
            modules=("prosodia",),
        )

        update_organization(mock_admin, 1, whatsapp_numbers="5511975218007\n5516981360051")
        numbers = get_organization_whatsapp_numbers(1)
        self.assertEqual(numbers, ["5511975218007", "5516981360051"])


if __name__ == "__main__":
    unittest.main()
