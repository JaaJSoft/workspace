from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.search import search_contacts, search_mail
from workspace.users.services.settings import set_setting

User = get_user_model()


class FormatDateTimezoneTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_hit_date_is_the_searchers_day_without_an_active_zone(self):
        # search_everything calls this from a worker, where the active zone
        # is UTC; 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        user = User.objects.create_user(username="paris", password="pass123")
        set_setting(user, "core", "timezone", "Europe/Paris")
        account = MailAccount.objects.create(
            owner=user, email="p@example.com", imap_host="i", smtp_host="s"
        )
        folder = MailFolder.objects.create(account=account, name="INBOX")
        MailMessage.objects.create(
            account=account,
            folder=folder,
            imap_uid=1,
            subject="quarterly invoice",
            date=datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
        )

        with patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 6, 1, tzinfo=UTC),
        ):
            results = search_mail("invoice", user, 10)

        self.assertEqual(results[0].date, "01 Feb")


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
        results = search_mail("invoice", self.user, 10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].tags[0].label, "Trash")


class SearchContactsAccentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="searchaccent", email="searchaccent@test.com", password="pass123"
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=1,
            from_name="François Dupré",
            from_email="f.dupre@example.com",
            to_addresses=[{"name": "Hélène Côté", "email": "hc@example.com"}],
        )

    def _emails(self, query):
        return [r.matched_value for r in search_contacts(query, self.user, 10)]

    def test_sender_name_matches_a_query_typed_without_accents(self):
        self.assertEqual(
            self._emails("francois"), ["François Dupré <f.dupre@example.com>"]
        )

    def test_recipient_name_matches_a_query_typed_without_accents(self):
        self.assertEqual(self._emails("helene"), ["Hélène Côté <hc@example.com>"])

    def test_the_other_addresses_of_the_message_are_left_out(self):
        self.assertEqual(
            self._emails("dupre"), ["François Dupré <f.dupre@example.com>"]
        )
