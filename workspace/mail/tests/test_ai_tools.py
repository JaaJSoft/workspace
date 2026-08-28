import json
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.ai_tools import (
    ListMailAccountScopedParams,
    MailToolProvider,
    ReadEmailParams,
)
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.users.services.settings import set_setting

User = get_user_model()


class ReadEmailTimezoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tzmail", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="tz@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="tz@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def tearDown(self):
        cache.clear()

    def test_date_rendered_in_user_timezone(self):
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        msg = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=1,
            subject="Boundary",
            date=datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
            from_name="Ext",
            from_email="ext@example.com",
        )
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        result = MailToolProvider().read_email(
            ReadEmailParams(uuid=str(msg.uuid)),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )
        self.assertIn("Date: 2026-02-01 00:30", result)


class ListFoldersMergeTests(TestCase):
    """The AI must not be offered an alias as a move target."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="aimerge", email="aimerge@test.com", password="pass123"
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.trash = MailFolder.objects.create(
            account=self.account,
            name="Trash",
            display_name="Trash",
            folder_type="trash",
            message_count=3,
            unread_count=1,
        )
        self.corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="trash",
            alias_of=self.trash,
            message_count=4,
            unread_count=2,
        )

    def tearDown(self):
        cache.clear()

    def test_listing_skips_aliases_and_sums_the_group(self):
        raw = MailToolProvider().list_folders(
            ListMailAccountScopedParams(account="user@example.com"),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )
        folders = json.loads(raw)

        names = {f["name"] for f in folders}
        self.assertNotIn("Corbeille", names)
        trash = next(f for f in folders if f["name"] == "Trash")
        self.assertEqual(trash["messages"], 7)
        self.assertEqual(trash["unread"], 3)

    def test_search_emails_reports_the_canonical_folder_name(self):
        from workspace.mail.ai_tools import SearchEmailsParams

        MailMessage.objects.create(
            account=self.account,
            folder=self.corbeille,
            imap_uid=1,
            subject="quarterly invoice",
        )
        raw = MailToolProvider().search_emails(
            SearchEmailsParams(query="invoice"),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )
        results = json.loads(raw)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["folder"], "Trash")

    def test_read_email_reports_the_canonical_folder_name(self):
        msg = MailMessage.objects.create(
            account=self.account,
            folder=self.corbeille,
            imap_uid=2,
            subject="hi",
            from_name="Ext",
            from_email="ext@example.com",
        )
        result = MailToolProvider().read_email(
            ReadEmailParams(uuid=str(msg.uuid)),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )
        self.assertIn("Folder: Trash", result)

    def test_resolve_folder_does_not_offer_the_alias_as_a_target(self):
        from workspace.mail.ai_tools import _resolve_folder

        folder, error = _resolve_folder(self.account, "Corbeille")
        self.assertIsNone(folder)
        self.assertIn("No folder named", error)
