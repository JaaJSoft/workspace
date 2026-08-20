from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount
from workspace.mail.services.imap_sync import (
    accounts_with_sync_errors,
    queue_account_syncs,
)

User = get_user_model()


def _make_account(owner, email, **overrides):
    fields = {
        "owner": owner,
        "email": email,
        "imap_host": "imap.test",
        "smtp_host": "smtp.test",
        "username": email,
    }
    fields.update(overrides)
    return MailAccount.objects.create(**fields)


class SyncStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="syncer", password="pw")
        cls.ok = _make_account(cls.user, "ok@test.com", last_sync_at=timezone.now())
        cls.broken = _make_account(cls.user, "bad@test.com", last_sync_error="boom")
        cls.inactive = _make_account(
            cls.user, "off@test.com", is_active=False, last_sync_error="boom"
        )

    def test_accounts_with_sync_errors_ignores_inactive_accounts(self):
        self.assertQuerySetEqual(accounts_with_sync_errors(), [self.broken])

    def test_queue_account_syncs_dispatches_active_accounts_only(self):
        with patch("workspace.mail.tasks.sync_single_account.delay") as delay:
            count = queue_account_syncs(MailAccount.objects.all())

        self.assertEqual(count, 2)
        queued = {call.args[0] for call in delay.call_args_list}
        self.assertEqual(queued, {str(self.ok.uuid), str(self.broken.uuid)})
