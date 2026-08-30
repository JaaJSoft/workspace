from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.search import search_mail

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


class MergeFolderTests(FolderMergeTestMixin, TestCase):
    def test_merge_points_the_alias_at_the_canonical(self):
        from workspace.mail.services.folder_merge import merge_folder

        canonical = merge_folder(self.corbeille, self.trash)

        self.corbeille.refresh_from_db()
        self.assertEqual(canonical, self.trash)
        self.assertEqual(self.corbeille.alias_of_id, self.trash.pk)

    def test_alias_adopts_the_canonical_type(self):
        from workspace.mail.services.folder_merge import merge_folder

        merge_folder(self.corbeille, self.trash)

        self.corbeille.refresh_from_db()
        self.assertEqual(self.corbeille.folder_type, "trash")

    def test_other_typed_canonical_is_promoted_to_the_alias_type(self):
        """Merging Sent into a user-created Envoyes must leave a sent folder."""
        from workspace.mail.services.folder_merge import merge_folder

        merge_folder(self.sent, self.envoyes)

        self.sent.refresh_from_db()
        self.envoyes.refresh_from_db()
        self.assertEqual(self.envoyes.folder_type, "sent")
        self.assertEqual(self.sent.folder_type, "sent")

    def test_two_special_types_leave_the_canonical_alone(self):
        from workspace.mail.services.folder_merge import merge_folder

        merge_folder(self.sent, self.trash)

        self.sent.refresh_from_db()
        self.trash.refresh_from_db()
        self.assertEqual(self.trash.folder_type, "trash")
        self.assertEqual(self.sent.folder_type, "trash")

    def test_merge_into_a_visible_canonical_unhides_the_alias(self):
        """A hidden alias would drop out of search while its canonical stays in."""
        from workspace.mail.services.folder_merge import merge_folder

        self.corbeille.is_hidden = True
        self.corbeille.save(update_fields=["is_hidden"])

        merge_folder(self.corbeille, self.trash)

        self.corbeille.refresh_from_db()
        self.assertFalse(self.corbeille.is_hidden)

    def test_merge_into_a_hidden_canonical_hides_the_alias(self):
        """A visible alias under a hidden canonical would keep notifying and
        surfacing in search, labelled with the folder the user just hid."""
        from workspace.mail.services.folder_merge import merge_folder

        self.trash.is_hidden = True
        self.trash.save(update_fields=["is_hidden"])

        merge_folder(self.corbeille, self.trash)

        self.corbeille.refresh_from_db()
        self.assertTrue(self.corbeille.is_hidden)

        MailMessage.objects.create(
            account=self.account,
            folder=self.corbeille,
            imap_uid=1,
            subject="quarterly invoice",
        )
        results = search_mail("invoice", self.user, 10)
        self.assertEqual(results, [])

    def test_self_merge_rejected(self):
        from workspace.mail.services.folder_merge import MergeError, merge_folder

        with self.assertRaises(MergeError):
            merge_folder(self.trash, self.trash)

    def test_cross_account_merge_rejected(self):
        from workspace.mail.services.folder_merge import MergeError, merge_folder

        with self.assertRaises(MergeError):
            merge_folder(self.corbeille, self.foreign)

    def test_merging_into_an_alias_rejected(self):
        """Groups stay exactly two levels deep."""
        from workspace.mail.services.folder_merge import MergeError, merge_folder

        merge_folder(self.corbeille, self.trash)

        with self.assertRaises(MergeError):
            merge_folder(self.envoyes, self.corbeille)

    def test_a_canonical_with_aliases_cannot_become_one(self):
        from workspace.mail.services.folder_merge import MergeError, merge_folder

        merge_folder(self.corbeille, self.trash)

        with self.assertRaises(MergeError):
            merge_folder(self.trash, self.envoyes)


class UnmergeFolderTests(FolderMergeTestMixin, TestCase):
    def test_unmerge_detaches_the_alias(self):
        from workspace.mail.services.folder_merge import merge_folder, unmerge_folder

        merge_folder(self.corbeille, self.trash)
        unmerge_folder(self.corbeille)

        self.corbeille.refresh_from_db()
        self.assertIsNone(self.corbeille.alias_of_id)

    def test_unmerge_redetects_the_type_from_the_name(self):
        """Keeping the inherited type would put a second trash-typed folder
        next to the real Trash the moment the user undoes a merge."""
        from workspace.mail.services.folder_merge import merge_folder, unmerge_folder

        merge_folder(self.corbeille, self.trash)
        self.corbeille.refresh_from_db()
        self.assertEqual(self.corbeille.folder_type, "trash")

        unmerge_folder(self.corbeille)

        self.corbeille.refresh_from_db()
        self.assertEqual(self.corbeille.folder_type, "other")

    def test_unmerge_of_a_plain_folder_is_a_noop(self):
        from workspace.mail.services.folder_merge import unmerge_folder

        unmerge_folder(self.trash)

        self.trash.refresh_from_db()
        self.assertIsNone(self.trash.alias_of_id)
        self.assertEqual(self.trash.folder_type, "trash")

    def test_unmerge_inherits_the_canonicals_hidden_state(self):
        """The user hid the group; detaching a member must not resurface it."""
        from workspace.mail.services.folder_merge import merge_folder, unmerge_folder

        merge_folder(self.corbeille, self.trash)
        # Simulate the canonical being hidden after the merge (the write
        # that hides a canonical does not touch this row directly).
        self.trash.is_hidden = True
        self.trash.save(update_fields=["is_hidden"])
        self.corbeille.refresh_from_db()
        self.assertFalse(self.corbeille.is_hidden)

        unmerge_folder(self.corbeille)

        self.corbeille.refresh_from_db()
        self.assertTrue(self.corbeille.is_hidden)


class SetGroupHiddenTests(FolderMergeTestMixin, TestCase):
    def test_hiding_the_canonical_hides_every_alias(self):
        from workspace.mail.services.folder_merge import merge_folder, set_group_hidden

        merge_folder(self.corbeille, self.trash)

        set_group_hidden(self.trash, True)

        self.corbeille.refresh_from_db()
        self.assertTrue(self.corbeille.is_hidden)

    def test_hiding_a_standalone_folder_does_not_touch_other_folders(self):
        from workspace.mail.services.folder_merge import set_group_hidden

        set_group_hidden(self.envoyes, True)

        self.envoyes.refresh_from_db()
        self.assertTrue(self.envoyes.is_hidden)
        self.trash.refresh_from_db()
        self.assertFalse(self.trash.is_hidden)


class PromoteAliasTests(FolderMergeTestMixin, TestCase):
    def test_promotes_the_oldest_alias_and_repoints_the_rest(self):
        from workspace.mail.services.folder_merge import merge_folder, promote_alias

        merge_folder(self.corbeille, self.trash)
        merge_folder(self.envoyes, self.trash)

        heir = promote_alias(self.trash)

        self.corbeille.refresh_from_db()
        self.envoyes.refresh_from_db()
        self.assertEqual(heir, self.corbeille)
        self.assertIsNone(self.corbeille.alias_of_id)
        self.assertEqual(self.envoyes.alias_of_id, self.corbeille.pk)

    def test_promoted_alias_keeps_the_group_type(self):
        from workspace.mail.services.folder_merge import merge_folder, promote_alias

        merge_folder(self.corbeille, self.trash)

        heir = promote_alias(self.trash)

        self.assertEqual(heir.folder_type, "trash")

    def test_returns_none_without_aliases(self):
        from workspace.mail.services.folder_merge import promote_alias

        self.assertIsNone(promote_alias(self.trash))

    def test_excluded_candidates_are_not_promoted(self):
        """Discovery excludes folders that vanished in the same pass."""
        from workspace.mail.services.folder_merge import merge_folder, promote_alias

        merge_folder(self.corbeille, self.trash)
        merge_folder(self.envoyes, self.trash)

        heir = promote_alias(self.trash, exclude_ids=[self.corbeille.pk])

        self.assertEqual(heir, self.envoyes)


class SentCopyTargetTests(FolderMergeTestMixin, TestCase):
    """The sent copy must land in the folder the user kept.

    Regression for the pre-existing bug: with `Envoyes` and `Sent` both typed
    sent, `filter(folder_type=SENT).first()` resolves through
    Meta.ordering (["account", "name"]) and picks Envoyes because E < S.
    """

    def test_append_to_sent_targets_the_canonical(self):
        from workspace.mail.services.folder_merge import merge_folder

        merge_folder(self.envoyes, self.sent)

        with patch("workspace.mail.services.imap_messages.connect_imap") as connect:
            conn = connect.return_value
            conn.select.return_value = ("OK", [b""])
            conn.uid.return_value = ("OK", [b""])
            conn.append.return_value = ("OK", [b""])
            from workspace.mail.services.imap_messages import append_to_sent

            append_to_sent(self.account, b"Message-ID: <x@example.com>\r\n\r\nbody")

        selected = conn.select.call_args[0][0]
        self.assertIn("Sent", selected)
        self.assertNotIn("Envoyes", selected)
