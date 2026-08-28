from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder
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


class DiscoveryMergeGroupTests(TestCase):
    """Folder discovery must not undo a user's merge."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="syncmerge", email="syncmerge@test.com", password="pass123"
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.envoyes = MailFolder.objects.create(
            account=self.account,
            name="Envoyes",
            display_name="Envoyes",
            folder_type="sent",
        )
        self.sent = MailFolder.objects.create(
            account=self.account,
            name="Sent",
            display_name="Sent",
            folder_type="sent",
            alias_of=self.envoyes,
        )

    def _sync(self, remote):
        with (
            patch("workspace.mail.services.imap_sync.connect_imap"),
            patch(
                "workspace.mail.services.imap_sync.list_folders", return_value=remote
            ),
        ):
            from workspace.mail.services.imap_sync import sync_folders

            sync_folders(self.account)

    def test_canonical_keeps_its_promoted_type(self):
        """_detect_folder_type("Envoyes") is "other"; the merge said "sent"."""
        self._sync([("", "/", "Envoyes"), ("", "/", "Sent")])

        self.envoyes.refresh_from_db()
        self.assertEqual(self.envoyes.folder_type, "sent")

    def test_alias_keeps_the_inherited_type(self):
        self._sync([("", "/", "Envoyes"), ("", "/", "Sent")])

        self.sent.refresh_from_db()
        self.assertEqual(self.sent.folder_type, "sent")
        self.assertEqual(self.sent.alias_of_id, self.envoyes.pk)

    def test_ungrouped_folder_still_gets_its_type_detected(self):
        plain = MailFolder.objects.create(
            account=self.account,
            name="Trash",
            display_name="Trash",
            folder_type="other",
        )

        self._sync([("", "/", "Envoyes"), ("", "/", "Sent"), ("", "/", "Trash")])

        plain.refresh_from_db()
        self.assertEqual(plain.folder_type, "trash")

    def test_vanished_canonical_promotes_its_alias(self):
        self._sync([("", "/", "Sent")])

        self.sent.refresh_from_db()
        self.assertIsNone(self.sent.alias_of_id)
        self.assertFalse(MailFolder.objects.filter(pk=self.envoyes.pk).exists())
