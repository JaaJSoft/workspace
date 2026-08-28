from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.search import _format_date

User = get_user_model()


class FormatDateTimezoneTests(TestCase):
    def tearDown(self):
        dj_timezone.deactivate()

    def test_old_date_formats_in_active_timezone(self):
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        dt = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        dj_timezone.activate("Europe/Paris")
        self.assertEqual(_format_date(dt), "01 Feb")


class MergedFolderSearchTests(TestCase):
    """A hit living in an alias is tagged with the folder the user sees."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="searchmerge", email="searchmerge@test.com", password="pass123"
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
        )
        self.corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="trash",
            alias_of=self.trash,
        )
        MailMessage.objects.create(
            account=self.account,
            folder=self.corbeille,
            imap_uid=1,
            subject="quarterly invoice",
        )

    def test_result_is_tagged_with_the_canonical_name(self):
        from workspace.mail.search import search_mail

        results = search_mail("invoice", self.user, 10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].tags[0].label, "Trash")
