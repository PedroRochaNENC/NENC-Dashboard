import unittest

from scripts.backfill_audio_received_at import (
    CARIMBAR,
    MENSAGEM_DIVERGENTE,
    NAO_ENCONTRADO,
    SEM_HORA_NA_API,
    SEM_MENSAGEM_LOCAL,
    SESSAO_SEM_ID,
    classificar,
)


class ClassificarTests(unittest.TestCase):
    LOCAL = {"session_id": "wa_+5511975218007_20", "whatsapp_message_id": "MSG-20"}

    def test_stamps_when_the_message_matches(self):
        api = {"whatsapp_message_id": "MSG-20", "received_at": "2026-10-08T21:30:00"}
        self.assertEqual(classificar(self.LOCAL, api), (CARIMBAR, "2026-10-08T21:30:00"))

    def test_same_id_from_another_api_instance_is_never_stamped(self):
        api = {"whatsapp_message_id": "OUTRA-MSG", "received_at": "2026-10-08T21:30:00"}
        self.assertEqual(classificar(self.LOCAL, api), (MENSAGEM_DIVERGENTE, None))

    def test_without_a_local_message_there_is_nothing_to_compare(self):
        local = {"session_id": "wa_upload_17", "whatsapp_message_id": None}
        api = {"whatsapp_message_id": None, "received_at": "2026-10-08T21:30:00"}
        self.assertEqual(classificar(local, api), (SEM_MENSAGEM_LOCAL, None))

    def test_api_without_the_time(self):
        api = {"whatsapp_message_id": "MSG-20", "received_at": None}
        self.assertEqual(classificar(self.LOCAL, api), (SEM_HORA_NA_API, None))

    def test_audio_missing_from_the_api(self):
        self.assertEqual(classificar(self.LOCAL, None), (NAO_ENCONTRADO, None))

    def test_session_without_an_api_id(self):
        local = {"session_id": "entrevista-manual", "whatsapp_message_id": "MSG-1"}
        self.assertEqual(classificar(local, {}), (SESSAO_SEM_ID, None))


if __name__ == "__main__":
    unittest.main()
