"""Excluir entrevista apaga tambem o audio na API, mas so o audio certo.

Sem apagar na API, a sincronizacao reimporta a entrevista excluida. So que o id
do audio sai do fim do session_id, e parte do acervo veio de uma instancia
anterior da API: o mesmo id hoje pode ser a gravacao de outra pessoa (47
entrevistas assim em producao em 16/09/2026). E o mesmo audio pode ter sido
importado em mais de um projeto.
"""

import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
from unittest.mock import patch

import httpx

from utils import auth
from utils import prosodia_db
from utils import whatsapp_api_client
from utils.whatsapp_api_client import decide_api_audio_deletion


def _interview(**fields):
    base = {
        "session_id": "wa_5511999999999_42",
        "whatsapp_message_id": "wamid-42",
        "created_at": "2026-09-14 12:00:00",
        "other_interviews": 0,
    }
    base.update(fields)
    return base


def _api_audio(**fields):
    base = {
        "id": 42,
        "project_id": 7,
        "whatsapp_message_id": "wamid-42",
        "source": "whatsapp",
        "received_at": "2026-09-14T11:00:00",
    }
    base.update(fields)
    return base


class ApiAudioDeletionDecisionTests(unittest.TestCase):
    def test_whatsapp_audio_with_the_same_message_is_deleted(self):
        plan = decide_api_audio_deletion(_interview(), _api_audio(), 7)

        self.assertEqual((plan.api_audio_id, plan.delete_in_api), (42, True))

    def test_same_id_with_another_message_is_another_recording(self):
        plan = decide_api_audio_deletion(
            _interview(), _api_audio(whatsapp_message_id="wamid-de-outra-pessoa"), 7
        )

        self.assertFalse(plan.delete_in_api)
        self.assertIn("outra gravação", plan.reason)

    def test_audio_already_gone_from_the_api_is_not_deleted_again(self):
        plan = decide_api_audio_deletion(_interview(), None, 7)

        self.assertFalse(plan.delete_in_api)
        self.assertIn("já não existe", plan.reason)

    def test_audio_still_used_by_another_interview_stays(self):
        plan = decide_api_audio_deletion(_interview(other_interviews=1), _api_audio(), 7)

        self.assertFalse(plan.delete_in_api)
        self.assertIn("outra entrevista", plan.reason)

    def test_interview_that_did_not_come_from_the_api_has_no_api_audio(self):
        plan = decide_api_audio_deletion(_interview(session_id="35523510_Fim"), None, 7)

        self.assertEqual((plan.api_audio_id, plan.delete_in_api), (None, False))

    def test_whatsapp_interview_without_local_message_cannot_be_confirmed(self):
        plan = decide_api_audio_deletion(
            _interview(whatsapp_message_id=None), _api_audio(whatsapp_message_id=None), 7
        )

        self.assertFalse(plan.delete_in_api)

    def test_api_upload_in_the_linked_project_imported_after_receipt_is_deleted(self):
        plan = decide_api_audio_deletion(
            _interview(session_id="wa_upload_42", whatsapp_message_id=None),
            _api_audio(whatsapp_message_id=None, source="upload"),
            7,
        )

        self.assertTrue(plan.delete_in_api)

    def test_upload_clock_skew_between_server_and_api_is_tolerated(self):
        # created_at local no fuso do servidor pode parecer horas antes do UTC.
        plan = decide_api_audio_deletion(
            _interview(session_id="wa_upload_42", whatsapp_message_id=None, created_at="2026-09-14 09:00:00"),
            _api_audio(whatsapp_message_id=None, source="upload", received_at="2026-09-14T11:30:00Z"),
            7,
        )

        self.assertTrue(plan.delete_in_api)

    def test_upload_imported_long_before_the_api_received_it_is_another_recording(self):
        plan = decide_api_audio_deletion(
            _interview(session_id="wa_upload_42", whatsapp_message_id=None, created_at="2026-07-14 10:00:00"),
            _api_audio(whatsapp_message_id=None, source="upload", received_at="2026-09-10T10:00:00"),
            7,
        )

        self.assertFalse(plan.delete_in_api)
        self.assertIn("outra gravação", plan.reason)

    def test_upload_from_another_api_project_stays(self):
        plan = decide_api_audio_deletion(
            _interview(session_id="wa_upload_42", whatsapp_message_id=None),
            _api_audio(whatsapp_message_id=None, source="upload", project_id=8),
            7,
        )

        self.assertFalse(plan.delete_in_api)
        self.assertIn("outro projeto", plan.reason)

    def test_upload_session_pointing_to_a_whatsapp_audio_is_another_recording(self):
        plan = decide_api_audio_deletion(
            _interview(session_id="wa_upload_42", whatsapp_message_id=None),
            _api_audio(),
            7,
        )

        self.assertFalse(plan.delete_in_api)


class ApiAudioDeletionRequestTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = {}
        real_client = httpx.Client

        def handler(request):
            self.requests.append((request.method, request.url.path))
            return self.responses.get((request.method, request.url.path), httpx.Response(404))

        for patcher in (
            patch.dict(os.environ, {"WHATSAPP_API_URL": "https://api.invalid", "WHATSAPP_API_KEY": "key"}),
            patch.object(
                whatsapp_api_client.httpx,
                "Client",
                side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
            ),
            patch.object(whatsapp_api_client, "_require_write"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.require = patch.object(whatsapp_api_client, "_require_owned_resource").start()
        self.release = patch.object(whatsapp_api_client, "release_external_resource").start()
        self.addCleanup(patch.stopall)

    def test_delete_checks_ownership_sends_delete_and_releases_the_resource(self):
        self.responses[("DELETE", "/audios/42")] = httpx.Response(204)

        whatsapp_api_client.delete_api_audio(42)

        self.require.assert_called_once_with("whatsapp_audio", 42)
        self.assertEqual(self.requests, [("DELETE", "/audios/42")])
        self.release.assert_called_once_with("whatsapp_audio", 42)

    def test_audio_already_gone_counts_as_deleted(self):
        whatsapp_api_client.delete_api_audio(42)

        self.release.assert_called_once_with("whatsapp_audio", 42)

    def test_api_failure_raises_and_keeps_the_resource(self):
        self.responses[("DELETE", "/audios/42")] = httpx.Response(500)

        with self.assertRaises(httpx.HTTPStatusError):
            whatsapp_api_client.delete_api_audio(42)

        self.release.assert_not_called()

    def test_unowned_audio_is_never_requested(self):
        self.require.side_effect = auth.AuthorizationError("de outra organizacao")

        with self.assertRaises(auth.AuthorizationError):
            whatsapp_api_client.delete_api_audio(42)
        plan = whatsapp_api_client.plan_api_audio_deletion(_interview(), 7)

        self.assertEqual(self.requests, [])
        self.assertFalse(plan.delete_in_api)
        self.assertIn("não pertence", plan.reason)

    def test_plan_reads_the_api_audio_and_applies_the_decision(self):
        self.responses[("GET", "/audios/42")] = httpx.Response(200, json=_api_audio())

        plan = whatsapp_api_client.plan_api_audio_deletion(_interview(), 7)

        self.assertTrue(plan.delete_in_api)
        self.assertEqual(self.requests, [("GET", "/audios/42")])

    def test_plan_raises_when_the_api_does_not_answer(self):
        self.responses[("GET", "/audios/42")] = httpx.Response(502)

        with self.assertRaises(httpx.HTTPStatusError):
            whatsapp_api_client.plan_api_audio_deletion(_interview(), 7)

    def test_plan_skips_the_api_when_another_interview_uses_the_audio(self):
        plan = whatsapp_api_client.plan_api_audio_deletion(_interview(other_interviews=2), 7)

        self.assertFalse(plan.delete_in_api)
        self.assertEqual(self.requests, [])


class AudioDeletionReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nenc-insights.db"
        self.organization = auth.create_organization(
            "Organization One", database_path=self.database_path, _bootstrap=True
        )
        self.environment = patch.dict(os.environ, {"NENC_DB_PATH": str(self.database_path)})
        self.environment.start()
        prosodia_db.init_db()
        with closing(sqlite3.connect(self.database_path)) as conn, conn:
            self.other_organization_id = conn.execute(
                "INSERT INTO organizations (name, is_active, created_at, updated_at)"
                " VALUES ('Organization Two', 1, datetime('now'), datetime('now'))"
            ).lastrowid
        for attribute, kwargs in (
            ("_require_write", {}),
            ("_audit", {}),
            ("_active_organization_id", {"return_value": self.organization.id}),
        ):
            guard = patch.object(prosodia_db, attribute, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        self.project_id = prosodia_db.create_project("Projeto")

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _insert(self, session_id, message_id=None, organization_id=None):
        with closing(sqlite3.connect(self.database_path)) as conn, conn:
            return conn.execute(
                "INSERT INTO audios (organization_id, project_id, session_id, whatsapp_message_id)"
                " VALUES (?, ?, ?, ?)",
                (organization_id or self.organization.id, self.project_id, session_id, message_id),
            ).lastrowid

    def test_counts_other_interviews_of_the_same_audio_in_any_organization(self):
        audio_id = self._insert("wa_5511999999999_42", "wamid-42")
        self._insert("wa_5511999999999_42", "wamid-42", organization_id=self.other_organization_id)
        self._insert("wa_5511999999999_42", "wamid-42")
        # Mesmo id com outra mensagem (instancia anterior) e id que so termina igual.
        self._insert("wa_5511888888888_42", "wamid-outra")
        self._insert("wa_5511999999999_142", "wamid-42")

        reference = prosodia_db.get_audio_deletion_reference(audio_id, 42)

        self.assertEqual(reference["other_interviews"], 2)
        self.assertEqual(reference["whatsapp_message_id"], "wamid-42")

    def test_upload_without_message_matches_by_session(self):
        audio_id = self._insert("wa_upload_17")
        self._insert("wa_upload_17", organization_id=self.other_organization_id)
        self._insert("wa_5511999999999_17")

        reference = prosodia_db.get_audio_deletion_reference(audio_id, 17)

        self.assertEqual(reference["other_interviews"], 1)

    def test_interview_of_another_organization_is_not_returned(self):
        audio_id = self._insert("wa_5511999999999_42", "wamid-42", organization_id=self.other_organization_id)

        self.assertIsNone(prosodia_db.get_audio_deletion_reference(audio_id, 42))


if __name__ == "__main__":
    unittest.main()
