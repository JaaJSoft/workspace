from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.imports.models import ImportConnection
from workspace.imports.providers.base import AuthenticationFailed
from workspace.imports.services import connections as svc
from workspace.imports.services.url_guard import UnsafeUrl

from .fakes import fake_provider

User = get_user_model()


@override_settings(IMPORTS_ALLOWED_HOSTS=["cloud.example.org"])
class CreateConnectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.provider = fake_provider()

    def _create(self, **overrides):
        kwargs = {
            "provider": "fake",
            "label": "My cloud",
            "base_url": "https://cloud.example.org/",
            "username": "alice",
            "secret": "good",
        }
        kwargs.update(overrides)
        return svc.create_connection(self.user, **kwargs)

    def test_saves_a_verified_connection_with_its_capabilities(self):
        conn = self._create()
        conn.refresh_from_db()
        self.assertEqual(conn.base_url, "https://cloud.example.org/dav")
        self.assertEqual(conn.get_secret(), "good")
        self.assertEqual(conn.capabilities, {"kinds": ["files"], "quota_used": 42})
        self.assertIsNotNone(conn.last_checked_at)
        self.assertEqual(self.provider.test_calls, 1)

    def test_nothing_is_saved_when_the_remote_rejects_the_credentials(self):
        with self.assertRaises(AuthenticationFailed):
            self._create(secret="wrong")
        self.assertFalse(ImportConnection.objects.exists())

    def test_unknown_provider(self):
        with self.assertRaises(svc.UnknownProvider):
            self._create(provider="nope")
        self.assertFalse(ImportConnection.objects.exists())

    @override_settings(IMPORTS_ALLOWED_HOSTS=[])
    def test_unsafe_url_is_refused_before_any_remote_call(self):
        with self.assertRaises(UnsafeUrl):
            self._create(base_url="http://127.0.0.1/")
        self.assertEqual(self.provider.test_calls, 0)
        self.assertFalse(ImportConnection.objects.exists())


@override_settings(IMPORTS_ALLOWED_HOSTS=["cloud.example.org", "other.example.org"])
class UpdateAndTestConnectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.provider = fake_provider()
        self.conn = svc.create_connection(
            self.user,
            provider="fake",
            label="Old",
            base_url="https://cloud.example.org",
            username="alice",
            secret="good",
        )
        self.provider.test_calls = 0

    def test_label_change_does_not_touch_the_remote(self):
        svc.update_connection(self.conn, label="New")
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.label, "New")
        self.assertEqual(self.provider.test_calls, 0)

    def test_secret_change_is_verified(self):
        self.provider.valid_secret = "rotated"
        svc.update_connection(self.conn, secret="rotated")
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.get_secret(), "rotated")
        self.assertEqual(self.provider.test_calls, 1)

    def test_rejected_secret_change_is_not_saved(self):
        with self.assertRaises(AuthenticationFailed):
            svc.update_connection(self.conn, secret="wrong")
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.get_secret(), "good")

    def test_base_url_change_is_normalised_and_verified(self):
        svc.update_connection(self.conn, base_url="https://other.example.org/")
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.base_url, "https://other.example.org/dav")
        self.assertEqual(self.provider.test_calls, 1)

    def test_test_connection_refreshes_capabilities(self):
        self.provider.capabilities = {"kinds": ["files"], "quota_used": 99}
        svc.test_connection(self.conn)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.capabilities["quota_used"], 99)
        self.assertEqual(self.conn.last_error, "")

    def test_test_connection_records_the_error_and_reraises(self):
        self.provider.valid_secret = "changed-remotely"
        with self.assertRaises(AuthenticationFailed):
            svc.test_connection(self.conn)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.last_error, "bad secret")
        # A later success clears it.
        self.provider.valid_secret = "good"
        svc.test_connection(self.conn)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.last_error, "")

    def test_browse_lists_folders_first_then_names_case_insensitively(self):
        entries = svc.browse_files(self.conn, "")
        self.assertEqual([e.name for e in entries], ["alpha", "Zeta", "A.txt", "b.txt"])
        self.assertEqual(
            [e.id for e in svc.browse_files(self.conn, "/alpha")], ["/alpha/deep.txt"]
        )

    def test_browse_closes_the_source(self):
        svc.browse_files(self.conn, "")
        self.assertTrue(self.provider.last_source.closed)

    @override_settings(IMPORTS_ALLOWED_HOSTS=[])
    def test_browse_re_checks_the_url(self):
        with patch(
            "workspace.imports.services.connections.check_remote_url",
            side_effect=UnsafeUrl("no"),
        ):
            with self.assertRaises(UnsafeUrl):
                svc.browse_files(self.conn, "")
