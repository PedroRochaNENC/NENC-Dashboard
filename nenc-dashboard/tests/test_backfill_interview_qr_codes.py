import unittest

from scripts.backfill_interview_qr_codes import (
    CARIMBAR,
    MENSAGEM_DIVERGENTE,
    NAO_ENCONTRADO,
    SEM_MENSAGEM_LOCAL,
    SEM_QR_NA_API,
    SESSAO_SEM_ID,
    api_audio_id,
    classificar,
)


class ApiAudioIdTests(unittest.TestCase):
    def test_extracts_the_api_id_from_the_session(self):
        self.assertEqual(api_audio_id("wa_+5511975218007_20"), 20)
        self.assertEqual(api_audio_id("wa_5511975218007_1"), 1)
        self.assertEqual(api_audio_id("wa_upload_17"), 17)

    def test_sessions_without_an_api_id(self):
        self.assertIsNone(api_audio_id("entrevista-manual"))
        self.assertIsNone(api_audio_id("wa_sem_numero"))
        self.assertIsNone(api_audio_id(None))


class ClassificarTests(unittest.TestCase):
    LOCAL = {"session_id": "wa_+5511975218007_20", "whatsapp_message_id": "MSG-20"}

    def test_stamps_when_the_message_matches(self):
        api = {"whatsapp_message_id": "MSG-20", "qr_code_name": "Banner Homs"}
        self.assertEqual(classificar(self.LOCAL, api), (CARIMBAR, "Banner Homs"))

    def test_falls_back_to_the_code_when_the_qr_has_no_name(self):
        api = {"whatsapp_message_id": "MSG-20", "qr_code_name": None, "qr_code_code": "05-01"}
        self.assertEqual(classificar(self.LOCAL, api), (CARIMBAR, "05-01"))

    def test_same_id_from_another_api_instance_is_never_stamped(self):
        """A razao do script: o mesmo id em producao pode ser outro audio."""
        api = {"whatsapp_message_id": "OUTRA-MSG", "qr_code_name": "QR de outro audio"}
        self.assertEqual(classificar(self.LOCAL, api), (MENSAGEM_DIVERGENTE, None))

    def test_without_a_local_message_there_is_nothing_to_compare(self):
        local = {"session_id": "wa_upload_17", "whatsapp_message_id": None}
        api = {"whatsapp_message_id": None, "qr_code_name": "Qualquer"}
        self.assertEqual(classificar(local, api), (SEM_MENSAGEM_LOCAL, None))

    def test_audio_without_qr_in_the_api(self):
        api = {"whatsapp_message_id": "MSG-20", "qr_code_name": None, "qr_code_code": None}
        self.assertEqual(classificar(self.LOCAL, api), (SEM_QR_NA_API, None))

    def test_audio_missing_from_the_api(self):
        self.assertEqual(classificar(self.LOCAL, None), (NAO_ENCONTRADO, None))

    def test_session_without_an_api_id(self):
        local = {"session_id": "entrevista-manual", "whatsapp_message_id": "MSG-1"}
        self.assertEqual(classificar(local, {}), (SESSAO_SEM_ID, None))


if __name__ == "__main__":
    unittest.main()
