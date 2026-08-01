from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.ai_tools import MailToolProvider, ReadEmailParams
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
