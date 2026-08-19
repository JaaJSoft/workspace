from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from workspace.imports.models import ImportConnection
from workspace.imports.services import connections as svc

from .fakes import fake_provider

User = get_user_model()

BASE = "/api/v1/imports"


@override_settings(IMPORTS_ALLOWED_HOSTS=["cloud.example.org"])
class ConnectionsApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.provider = fake_provider()
        self.client.force_authenticate(self.user)

    def _create(self, **overrides):
        payload = {
            "provider": "fake",
            "label": "My cloud",
            "base_url": "https://cloud.example.org",
            "username": "alice",
            "secret": "good",
        }
        payload.update(overrides)
        return self.client.post(f"{BASE}/connections", payload, format="json")

    # providers

    def test_providers_lists_available_ones(self):
        response = self.client.get(f"{BASE}/providers")
        self.assertEqual(response.status_code, 200)
        slugs = {p["slug"] for p in response.json()}
        self.assertTrue({"webdav", "nextcloud", "fake"} <= slugs)
        fake = next(p for p in response.json() if p["slug"] == "fake")
        self.assertEqual(
            fake,
            {
                "slug": "fake",
                "name": "Fake cloud",
                "auth": "credentials",
                "kinds": ["files"],
            },
        )

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"{BASE}/providers").status_code, 403)
        self.assertEqual(self.client.get(f"{BASE}/connections").status_code, 403)

    # create

    def test_create_returns_the_connection_without_its_secret(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data["provider_name"], "Fake cloud")
        self.assertEqual(data["base_url"], "https://cloud.example.org/dav")
        self.assertTrue(data["has_secret"])
        self.assertNotIn("secret", data)
        self.assertEqual(data["capabilities"]["quota_used"], 42)

    def test_create_with_bad_credentials_is_a_400_with_the_remote_message(self):
        response = self._create(secret="wrong")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "bad secret"})
        self.assertFalse(ImportConnection.objects.exists())

    def test_create_with_unknown_provider_is_a_400(self):
        response = self._create(provider="dropbox")
        self.assertEqual(response.status_code, 400)
        self.assertIn("provider", response.json())

    @override_settings(IMPORTS_ALLOWED_HOSTS=[])
    def test_create_with_unsafe_url_is_a_400(self):
        response = self._create(base_url="http://169.254.169.254/latest")
        self.assertEqual(response.status_code, 400)
        self.assertIn("will not contact", response.json()["detail"])

    def test_create_validates_the_payload(self):
        response = self._create(base_url="not a url", secret="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("base_url", response.json())
        self.assertIn("secret", response.json())

    # read / update / delete

    def test_list_and_detail_are_owner_scoped(self):
        mine = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        theirs = svc.create_connection(
            self.other,
            provider="fake",
            label="theirs",
            base_url="https://cloud.example.org",
            username="b",
            secret="good",
        )

        listed = self.client.get(f"{BASE}/connections").json()
        self.assertEqual([c["uuid"] for c in listed], [str(mine.uuid)])
        self.assertEqual(
            self.client.get(f"{BASE}/connections/{mine.uuid}").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"{BASE}/connections/{theirs.uuid}").status_code, 404
        )
        self.assertEqual(
            self.client.delete(f"{BASE}/connections/{theirs.uuid}").status_code, 404
        )
        self.assertTrue(ImportConnection.objects.filter(pk=theirs.pk).exists())

    def test_patch_label(self):
        conn = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        response = self.client.patch(
            f"{BASE}/connections/{conn.uuid}", {"label": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["label"], "Renamed")

    def test_patch_rejected_secret_is_a_400(self):
        conn = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        response = self.client.patch(
            f"{BASE}/connections/{conn.uuid}", {"secret": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "bad secret")

    def test_delete(self):
        conn = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        self.assertEqual(
            self.client.delete(f"{BASE}/connections/{conn.uuid}").status_code, 204
        )
        self.assertFalse(ImportConnection.objects.exists())

    # test / browse

    def test_test_endpoint_reports_success_and_failure(self):
        conn = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        self.assertEqual(
            self.client.post(f"{BASE}/connections/{conn.uuid}/test").status_code, 200
        )
        self.provider.valid_secret = "rotated"
        response = self.client.post(f"{BASE}/connections/{conn.uuid}/test")
        self.assertEqual(response.status_code, 400)
        conn.refresh_from_db()
        self.assertEqual(conn.last_error, "bad secret")

    def test_browse_root_and_subfolder(self):
        conn = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        response = self.client.get(f"{BASE}/connections/{conn.uuid}/browse")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["path"], "/")
        self.assertEqual(
            [e["name"] for e in body["entries"]], ["alpha", "Zeta", "A.txt", "b.txt"]
        )
        self.assertEqual(body["entries"][2]["size"], 1)

        response = self.client.get(
            f"{BASE}/connections/{conn.uuid}/browse", {"path": "/alpha"}
        )
        self.assertEqual(
            [e["id"] for e in response.json()["entries"]], ["/alpha/deep.txt"]
        )

    def test_browse_unknown_kind_is_a_400(self):
        conn = svc.create_connection(
            self.user,
            provider="fake",
            label="mine",
            base_url="https://cloud.example.org",
            username="a",
            secret="good",
        )
        response = self.client.get(
            f"{BASE}/connections/{conn.uuid}/browse", {"kind": "photos"}
        )
        self.assertEqual(response.status_code, 400)
