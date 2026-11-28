import logging
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.common.url_guard import UnsafeUrl
from workspace.imports.models import ImportConnection, ImportLoginFlow
from workspace.imports.providers.base import (
    ConnectionFailed,
    LoginFlowGrant,
    ProviderError,
)
from workspace.imports.services import login_flows as svc

from .fakes import fake_provider

User = get_user_model()

TOKEN = "poll-token-never-shown"
APP_PASSWORD = "good"


@contextmanager
def captured_logs():
    """Every record any logger handles, whether it propagates to the root or not."""
    records = []
    original = logging.Logger.handle

    def handle(logger, record):
        records.append(record.getMessage())
        return original(logger, record)

    with patch.object(logging.Logger, "handle", handle):
        yield records


@override_settings(IMPORTS_ALLOWED_HOSTS=["cloud.example.org"])
class LoginFlowServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.provider = fake_provider()

    def _start(self, **overrides):
        kwargs = {
            "provider": "fake",
            "label": "My cloud",
            "base_url": "https://cloud.example.org/",
        }
        kwargs.update(overrides)
        return svc.start_login_flow(self.user, **kwargs)

    def _later(self, flow, seconds):
        return patch(
            "django.utils.timezone.now",
            return_value=flow.created_at + timedelta(seconds=seconds),
        )

    # start

    def test_start_records_a_pending_flow_with_its_token_encrypted(self):
        flow = self._start()

        flow.refresh_from_db()
        self.assertEqual(flow.status, ImportLoginFlow.Status.PENDING)
        self.assertEqual(flow.base_url, "https://cloud.example.org")
        self.assertEqual(
            flow.login_url, "https://cloud.example.org/login/v2/flow/approve-me"
        )
        self.assertEqual(flow.get_poll_token(), TOKEN)
        self.assertNotIn(TOKEN.encode(), bytes(flow.poll_token_encrypted))
        self.assertAlmostEqual(
            flow.expires_at - flow.created_at, svc.LIFETIME, delta=timedelta(seconds=1)
        )
        self.assertEqual(self.provider.login_flow_starts, ["https://cloud.example.org"])

    def test_start_refuses_a_provider_without_a_sign_in_flow(self):
        with self.assertRaises(svc.LoginFlowUnsupported):
            self._start(provider="webdav")
        self.assertFalse(ImportLoginFlow.objects.exists())

    @override_settings(IMPORTS_ALLOWED_HOSTS=[])
    def test_start_refuses_an_address_the_guard_refuses(self):
        with self.assertRaises(UnsafeUrl):
            self._start(base_url="http://127.0.0.1")
        self.assertEqual(self.provider.login_flow_starts, [])
        self.assertFalse(ImportLoginFlow.objects.exists())

    def test_start_failing_on_the_remote_records_nothing(self):
        self.provider.login_flow_error = ProviderError("no flow here")
        with self.assertRaises(ProviderError):
            self._start()
        self.assertFalse(ImportLoginFlow.objects.exists())

    # advance

    def test_pending_until_the_user_approves(self):
        flow = svc.advance_login_flow(self._start())

        self.assertEqual(flow.status, ImportLoginFlow.Status.PENDING)
        self.assertEqual(
            self.provider.login_flow_polls,
            [
                (
                    "https://cloud.example.org",
                    "https://cloud.example.org/login/v2/poll",
                    TOKEN,
                )
            ],
        )
        self.assertFalse(ImportConnection.objects.exists())

    def test_approval_saves_the_connection_and_drops_the_token(self):
        flow = self._start()
        self.provider.login_flow_grant = LoginFlowGrant(
            username="alice@example.org", secret=APP_PASSWORD
        )

        flow = svc.advance_login_flow(flow)

        self.assertEqual(flow.status, ImportLoginFlow.Status.COMPLETED)
        connection = ImportConnection.objects.get()
        self.assertEqual(flow.connection, connection)
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(connection.label, "My cloud")
        self.assertEqual(connection.username, "alice@example.org")
        self.assertEqual(connection.get_secret(), APP_PASSWORD)
        self.assertEqual(connection.base_url, "https://cloud.example.org/dav")
        stored = ImportLoginFlow.objects.get(pk=flow.pk)
        self.assertEqual(stored.status, ImportLoginFlow.Status.COMPLETED)
        self.assertEqual(stored.connection, connection)
        self.assertIsNone(stored.poll_token_encrypted)

    def test_a_finished_flow_is_not_polled_again(self):
        flow = self._start()
        self.provider.login_flow_grant = LoginFlowGrant(
            username="alice", secret=APP_PASSWORD
        )
        svc.advance_login_flow(flow)

        with self._later(flow, 10):
            svc.advance_login_flow(ImportLoginFlow.objects.get(pk=flow.pk))

        self.assertEqual(len(self.provider.login_flow_polls), 1)
        self.assertEqual(ImportConnection.objects.count(), 1)

    def test_reads_closer_than_the_interval_ask_the_remote_once(self):
        flow = self._start()
        with self._later(flow, 1):
            svc.advance_login_flow(flow)
        with self._later(flow, 2):
            svc.advance_login_flow(ImportLoginFlow.objects.get(pk=flow.pk))
        self.assertEqual(len(self.provider.login_flow_polls), 1)

        with self._later(flow, 3):
            svc.advance_login_flow(ImportLoginFlow.objects.get(pk=flow.pk))
        self.assertEqual(len(self.provider.login_flow_polls), 2)

    def test_an_expired_flow_fails_without_asking_the_remote(self):
        flow = self._start()

        with self._later(flow, svc.LIFETIME.total_seconds()):
            flow = svc.advance_login_flow(flow)

        self.assertEqual(flow.status, ImportLoginFlow.Status.FAILED)
        self.assertEqual(flow.error, "The sign-in expired. Start it again.")
        self.assertEqual(self.provider.login_flow_polls, [])
        self.assertIsNone(ImportLoginFlow.objects.get(pk=flow.pk).poll_token_encrypted)

    def test_an_unreachable_remote_leaves_the_flow_open(self):
        flow = self._start()
        self.provider.login_flow_error = ConnectionFailed("cloud is away")

        flow = svc.advance_login_flow(flow)

        self.assertEqual(flow.status, ImportLoginFlow.Status.PENDING)
        self.assertEqual(
            ImportLoginFlow.objects.get(pk=flow.pk).get_poll_token(), TOKEN
        )

    def test_an_unusable_answer_fails_the_flow(self):
        flow = self._start()
        self.provider.login_flow_error = ProviderError("not a sign-in answer")

        flow = svc.advance_login_flow(flow)

        self.assertEqual(flow.status, ImportLoginFlow.Status.FAILED)
        self.assertEqual(flow.error, "not a sign-in answer")

    def test_credentials_the_remote_then_refuses_fail_the_flow(self):
        flow = self._start()
        self.provider.login_flow_grant = LoginFlowGrant(username="alice", secret="bad")

        flow = svc.advance_login_flow(flow)

        self.assertEqual(flow.status, ImportLoginFlow.Status.FAILED)
        self.assertEqual(flow.error, "bad secret")
        self.assertFalse(ImportConnection.objects.exists())

    def test_neither_the_token_nor_the_password_is_logged(self):
        with captured_logs() as records:
            flow = self._start()
            self.provider.login_flow_error = ConnectionFailed("cloud is away")
            svc.advance_login_flow(flow)
            self.provider.login_flow_error = None
            self.provider.login_flow_grant = LoginFlowGrant(
                username="alice", secret=APP_PASSWORD + "-secret"
            )
            self.provider.valid_secret = APP_PASSWORD + "-secret"
            with self._later(flow, 5):
                svc.advance_login_flow(ImportLoginFlow.objects.get(pk=flow.pk))

        self.assertTrue(records)
        for message in records:
            self.assertNotIn(TOKEN, message)
            self.assertNotIn(APP_PASSWORD + "-secret", message)

    # discard and purge

    def test_discard_forgets_the_flow(self):
        flow = self._start()
        svc.discard_login_flow(flow)
        self.assertFalse(ImportLoginFlow.objects.exists())

    def test_purge_drops_flows_expired_past_the_margin(self):
        stale = self._start()
        fresh = self._start()
        ImportLoginFlow.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - svc.PURGE_AFTER_EXPIRY - timedelta(minutes=1)
        )
        ImportLoginFlow.objects.filter(pk=fresh.pk).update(
            expires_at=timezone.now() - svc.PURGE_AFTER_EXPIRY + timedelta(minutes=1)
        )

        self.assertEqual(svc.purge_expired_login_flows(), 1)
        self.assertEqual(
            list(ImportLoginFlow.objects.values_list("pk", flat=True)), [fresh.pk]
        )

    def test_purge_drops_the_token_of_a_flow_nobody_reads_once_it_expired(self):
        abandoned = self._start()
        open_flow = self._start()
        ImportLoginFlow.objects.filter(pk=abandoned.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        svc.purge_expired_login_flows()

        abandoned.refresh_from_db()
        self.assertIsNone(abandoned.poll_token_encrypted)
        self.assertEqual(abandoned.status, ImportLoginFlow.Status.PENDING)
        self.assertEqual(
            ImportLoginFlow.objects.get(pk=open_flow.pk).get_poll_token(), TOKEN
        )
        # A late read still reports the expiry, without asking the remote.
        abandoned = svc.advance_login_flow(abandoned)
        self.assertEqual(abandoned.status, ImportLoginFlow.Status.FAILED)
        self.assertEqual(self.provider.login_flow_polls, [])
