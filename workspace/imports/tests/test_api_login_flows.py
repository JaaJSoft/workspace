import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from workspace.imports.models import ImportLoginFlow
from workspace.imports.providers.base import LoginFlowGrant, ProviderError

from .fakes import fake_provider

User = get_user_model()

BASE = "/api/v1/imports/login-flows"
TOKEN = "poll-token-never-shown"
APP_PASSWORD = "app-password-from-the-remote"


@override_settings(IMPORTS_ALLOWED_HOSTS=["cloud.example.org"])
class LoginFlowApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.provider = fake_provider()
        self.provider.valid_secret = APP_PASSWORD
        self.client.force_authenticate(self.user)

    def _start(self, **overrides):
        payload = {
            "provider": "fake",
            "label": "My cloud",
            "base_url": "https://cloud.example.org",
        }
        payload.update(overrides)
        return self.client.post(BASE, payload, format="json")

    def test_start_answers_the_page_to_open_and_never_the_token(self):
        response = self._start()

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "uuid",
                "provider",
                "label",
                "base_url",
                "login_url",
                "status",
                "error",
                "connection",
                "expires_at",
                "created_at",
            },
        )
        self.assertEqual(body["status"], "pending")
        self.assertEqual(
            body["login_url"], "https://cloud.example.org/login/v2/flow/approve-me"
        )
        self.assertIsNone(body["connection"])
        self.assertTrue(body["expires_at"].endswith("Z"))
        self.assertNotIn(TOKEN, response.content.decode())
        self.assertNotIn("/login/v2/poll", response.content.decode())

    def test_reading_the_flow_saves_the_connection_once_approved(self):
        flow_uuid = self._start().json()["uuid"]
        pending = self.client.get(f"{BASE}/{flow_uuid}")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["status"], "pending")

        self.provider.login_flow_grant = LoginFlowGrant(
            username="alice", secret=APP_PASSWORD
        )
        ImportLoginFlow.objects.filter(pk=flow_uuid).update(polled_at=None)
        response = self.client.get(f"{BASE}/{flow_uuid}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["connection"]["username"], "alice")
        self.assertTrue(body["connection"]["has_secret"])
        self.assertNotIn(APP_PASSWORD, response.content.decode())
        self.assertNotIn(TOKEN, response.content.decode())
        connections = self.client.get("/api/v1/imports/connections").json()
        self.assertEqual([c["uuid"] for c in connections], [body["connection"]["uuid"]])

    def test_a_failed_flow_says_why(self):
        flow_uuid = self._start().json()["uuid"]
        self.provider.login_flow_error = ProviderError("not a sign-in answer")

        body = self.client.get(f"{BASE}/{flow_uuid}").json()

        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"], "not a sign-in answer")

    def test_a_remote_that_cannot_start_the_flow_is_a_400(self):
        self.provider.login_flow_error = ProviderError("no sign-in here")

        response = self._start()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "no sign-in here"})

    def test_a_provider_without_a_sign_in_flow_is_refused(self):
        response = self._start(provider="webdav")

        self.assertEqual(response.status_code, 400)
        self.assertIn("provider", response.json())
        self.assertFalse(ImportLoginFlow.objects.exists())

    def test_someone_elses_flow_answers_like_a_missing_one(self):
        flow_uuid = self._start().json()["uuid"]
        self.client.force_authenticate(self.other)

        theirs = self.client.get(f"{BASE}/{flow_uuid}")
        missing = self.client.get(f"{BASE}/{uuid.uuid4()}")

        self.assertEqual(
            (theirs.status_code, theirs.content),
            (missing.status_code, missing.content),
        )
        self.assertEqual(theirs.status_code, 404)
        self.assertEqual(self.client.delete(f"{BASE}/{flow_uuid}").status_code, 404)
        self.assertEqual(self.provider.login_flow_polls, [])
        self.assertTrue(ImportLoginFlow.objects.filter(pk=flow_uuid).exists())

    def test_delete_gives_up_on_the_flow(self):
        flow_uuid = self._start().json()["uuid"]

        self.assertEqual(self.client.delete(f"{BASE}/{flow_uuid}").status_code, 204)
        self.assertFalse(ImportLoginFlow.objects.exists())

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertEqual(self._start().status_code, 403)
