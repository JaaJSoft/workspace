from datetime import UTC, datetime, timedelta
from itertools import count
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)
from workspace.mail.today import MAIL_LIMIT, important_mail, is_urgent_mail

User = get_user_model()

NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


class ImportantMailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="pw")
        self.account = self._account(self.user)
        self.inbox = self._folder(self.account, "INBOX", "inbox")
        # Seeded with every new account; the default "Urgent" one notifies.
        self.urgent = MailLabel.objects.get(account=self.account, name="Urgent")
        self.fyi = MailLabel.objects.get(account=self.account, name="FYI")
        self.uids = count(1)

    def _account(self, owner):
        return MailAccount.objects.create(
            owner=owner,
            email=f"{owner.username}@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username=owner.username,
        )

    def _folder(self, account, name, folder_type):
        return MailFolder.objects.create(
            account=account, name=name, display_name=name, folder_type=folder_type
        )

    def _message(self, subject, folder=None, labels=(), age=0, **fields):
        folder = folder or self.inbox
        message = MailMessage.objects.create(
            account=folder.account,
            folder=folder,
            imap_uid=next(self.uids),
            subject=subject,
            date=NOW - timedelta(minutes=age),
            **fields,
        )
        for label in labels:
            MailMessageLabel.objects.create(message=message, label=label)
        return message

    def _subjects(self, user=None):
        tz = timezone.get_current_timezone()
        return [m.subject for m in important_mail(user or self.user, NOW, tz).items]

    def test_unread_mail_with_a_notifying_label_is_listed(self):
        message = self._message("Server down", labels=[self.urgent])

        rows = important_mail(self.user, NOW, timezone.get_current_timezone()).items

        self.assertEqual(rows, [message])
        self.assertEqual([link.label for link in rows[0].notify_labels], [self.urgent])

    def test_read_mail_with_a_notifying_label_is_not(self):
        self._message("Handled", labels=[self.urgent], is_read=True)

        self.assertEqual(self._subjects(), [])

    def test_unread_mail_with_a_quiet_label_is_not(self):
        self._message("Weekly update", labels=[self.fyi])

        self.assertEqual(self._subjects(), [])

    def test_starred_mail_is_listed_read_or_not(self):
        self._message("Follow up", is_starred=True, is_read=True)

        self.assertEqual(self._subjects(), ["Follow up"])

    def test_only_the_notifying_labels_ride_along(self):
        self._message("Both", labels=[self.urgent, self.fyi])

        rows = important_mail(self.user, NOW, timezone.get_current_timezone()).items

        self.assertEqual([link.label for link in rows[0].notify_labels], [self.urgent])

    def test_trash_and_spam_are_left_out(self):
        trash = self._folder(self.account, "Trash", MailFolder.FolderType.TRASH)
        spam = self._folder(self.account, "Junk", MailFolder.FolderType.SPAM)
        self._message("Binned", folder=trash, labels=[self.urgent])
        self._message("Scam", folder=spam, is_starred=True)

        self.assertEqual(self._subjects(), [])

    def test_deleted_and_draft_mail_is_left_out(self):
        self._message("Gone", is_starred=True, deleted_at=NOW)
        self._message("Unsent", is_starred=True, is_draft=True)

        self.assertEqual(self._subjects(), [])

    def test_another_users_mail_is_never_listed(self):
        other = User.objects.create_user(username="other", password="pw")
        other_inbox = self._folder(self._account(other), "INBOX", "inbox")
        self._message("Theirs", folder=other_inbox, is_starred=True)

        self.assertEqual(self._subjects(), [])
        self.assertEqual(self._subjects(other), ["Theirs"])

    def test_newest_first_and_capped(self):
        for age in range(MAIL_LIMIT + 2):
            self._message(f"m{age}", is_starred=True, age=age)

        rows = important_mail(self.user, NOW, timezone.get_current_timezone())

        self.assertEqual(
            [m.subject for m in rows.items], [f"m{age}" for age in range(MAIL_LIMIT)]
        )
        self.assertEqual(rows.total, MAIL_LIMIT + 2)

    def test_rows_leave_the_message_bodies_in_the_database(self):
        self._message("Heavy", is_starred=True, body_html="<p>" + "x" * 1000)

        with CaptureQueriesContext(connection) as queries:
            important_mail(self.user, NOW, timezone.get_current_timezone())

        self.assertNotIn("body_html", queries.captured_queries[0]["sql"])

    def test_unread_mail_with_a_notifying_label_is_urgent_starred_mail_is_not(self):
        self._message("Server down", labels=[self.urgent], age=1)
        self._message("Follow up", is_starred=True, is_read=True, age=2)

        rows = important_mail(self.user, NOW, timezone.get_current_timezone())

        self.assertEqual(
            {m.subject: is_urgent_mail(m) for m in rows.items},
            {"Server down": True, "Follow up": False},
        )

    def test_received_today_is_the_users_local_day(self):
        tokyo = ZoneInfo("Asia/Tokyo")
        # 20:00 UTC on Nov 20 is already Nov 21 in Tokyo, NOW's local day.
        self._message("Overnight", is_starred=True, age=13 * 60)

        (message,) = important_mail(self.user, NOW, tokyo).items

        self.assertTrue(message.received_today)

    def test_a_row_shows_its_notifying_label(self):
        self._message("Server down", labels=[self.urgent, self.fyi])

        (message,) = important_mail(
            self.user, NOW, timezone.get_current_timezone()
        ).items
        html = render_to_string(
            "mail/ui/partials/today_mail.html", {"item": message, "urgent": True}
        )

        self.assertIn(">Urgent</span>", html)
        self.assertNotIn("FYI", html)

    def test_a_short_list_is_its_own_count(self):
        self._message("Only one", is_starred=True)

        # The page and its labels; no COUNT for a page that is not full.
        with self.assertNumQueries(2):
            rows = important_mail(self.user, NOW, timezone.get_current_timezone())

        self.assertEqual(rows.total, 1)
