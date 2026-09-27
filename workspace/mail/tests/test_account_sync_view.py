from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.common.task_priority import INTERACTIVE_PRIORITY
from workspace.mail import tasks as mail_tasks
from workspace.mail.models import MailAccount
from workspace.mail.serializers import MailAccountSerializer

User = get_user_model()


class MailAccountSyncViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sync-view", password="x")
        self.client.force_authenticate(self.user)
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="sync@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="sync@example.com",
        )
        self.url = f"/api/v1/mail/accounts/{self.account.uuid}/sync"

    def test_queues_the_account_sync_at_interactive_priority(self):
        with (
            mock.patch.object(mail_tasks.sync_single_account, "apply_async") as queue,
            mock.patch("workspace.mail.services.imap_sync.sync_account") as sync,
        ):
            resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 202)
        queue.assert_called_once_with(
            args=[str(self.account.uuid)], priority=INTERACTIVE_PRIORITY
        )
        sync.assert_not_called()

    def test_returns_the_updated_at_the_sync_has_to_move_past(self):
        with mock.patch.object(mail_tasks.sync_single_account, "apply_async"):
            resp = self.client.post(self.url)

        self.assertEqual(
            resp.data["updated_at"],
            MailAccountSerializer(self.account).data["updated_at"],
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_updated_at_is_read_before_an_eager_sync_moves_it(self):
        def fake_sync(account):
            account.last_sync_at = timezone.now()
            account.save(update_fields=["last_sync_at", "updated_at"])

        before = MailAccountSerializer(self.account).data["updated_at"]
        with mock.patch(
            "workspace.mail.services.imap_sync.sync_account", side_effect=fake_sync
        ):
            resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["updated_at"], before)
        self.account.refresh_from_db()
        self.assertNotEqual(
            MailAccountSerializer(self.account).data["updated_at"], before
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_a_failed_sync_stores_its_error_on_the_account(self):
        with mock.patch(
            "workspace.mail.services.imap_sync.sync_account",
            side_effect=OSError("connection refused"),
        ):
            resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 202)
        self.account.refresh_from_db()
        self.assertEqual(self.account.last_sync_error, "connection refused")

    def test_a_broker_failure_is_503(self):
        with mock.patch.object(
            mail_tasks.sync_single_account,
            "apply_async",
            side_effect=ConnectionError("broker down"),
        ):
            resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 503)

    def test_an_inactive_account_is_not_queued(self):
        self.account.is_active = False
        self.account.save(update_fields=["is_active"])
        with mock.patch.object(mail_tasks.sync_single_account, "apply_async") as queue:
            resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 409)
        queue.assert_not_called()

    def test_another_users_account_is_404(self):
        other = User.objects.create_user(username="sync-other", password="x")
        self.client.force_authenticate(other)
        with mock.patch.object(mail_tasks.sync_single_account, "apply_async") as queue:
            resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 404)
        queue.assert_not_called()
