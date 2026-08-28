from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder

User = get_user_model()


class FolderMergeTestMixin:
    """Two accounts, each with the folders a merge test needs."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="mergeuser", email="merge@test.com", password="pass123"
        )
        self.other_user = User.objects.create_user(
            username="mergeother", email="mergeother@test.com", password="pass123"
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.other_account = MailAccount.objects.create(
            owner=self.other_user,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
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
            folder_type="other",
        )
        self.sent = MailFolder.objects.create(
            account=self.account,
            name="Sent",
            display_name="Sent",
            folder_type="sent",
        )
        self.envoyes = MailFolder.objects.create(
            account=self.account,
            name="Envoyes",
            display_name="Envoyes",
            folder_type="other",
        )
        self.foreign = MailFolder.objects.create(
            account=self.other_account,
            name="Trash",
            display_name="Trash",
            folder_type="trash",
        )


class AliasOfFieldTests(FolderMergeTestMixin, TestCase):
    def test_alias_of_defaults_to_none(self):
        self.assertIsNone(self.trash.alias_of)

    def test_alias_of_exposes_reverse_aliases(self):
        self.corbeille.alias_of = self.trash
        self.corbeille.save(update_fields=["alias_of"])
        self.assertEqual(
            list(self.trash.aliases.values_list("uuid", flat=True)),
            [self.corbeille.uuid],
        )

    def test_folder_cannot_alias_itself(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MailFolder.objects.filter(pk=self.trash.pk).update(
                    alias_of=self.trash.pk
                )
