from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.queries import user_account_ids

User = get_user_model()


class UserAccountIdsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")

    def _make_account(self, owner, email=None):
        return MailAccount.objects.create(
            owner=owner,
            email=email or f"{owner.username}@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username=owner.username,
        )

    def test_returns_owned_accounts(self):
        acct = self._make_account(self.alice)
        ids = list(user_account_ids(self.alice))
        self.assertIn(acct.pk, ids)

    def test_excludes_other_users_accounts(self):
        self._make_account(self.bob)
        ids = list(user_account_ids(self.alice))
        self.assertEqual(ids, [])

    def test_returns_multiple_accounts(self):
        a1 = self._make_account(self.alice, "alice@work.com")
        a2 = self._make_account(self.alice, "alice@personal.com")
        ids = list(user_account_ids(self.alice))
        self.assertEqual(set(ids), {a1.pk, a2.pk})

    def test_returns_empty_for_user_with_no_accounts(self):
        carol = User.objects.create_user(username="carol", password="pass")
        ids = list(user_account_ids(carol))
        self.assertEqual(ids, [])


class FolderGroupQueryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="groupuser", email="group@test.com", password="pass123"
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

    def test_canonical_folder_is_identity_for_a_plain_folder(self):
        from workspace.mail.queries import canonical_folder

        self.assertEqual(canonical_folder(self.trash), self.trash)

    def test_canonical_folder_resolves_an_alias(self):
        from workspace.mail.queries import canonical_folder

        self.assertEqual(canonical_folder(self.corbeille), self.trash)

    def test_canonical_folder_id_needs_no_extra_query(self):
        from workspace.mail.queries import canonical_folder_id

        alias = MailFolder.objects.get(pk=self.corbeille.pk)
        with self.assertNumQueries(0):
            self.assertEqual(canonical_folder_id(alias), self.trash.pk)

    def test_folder_group_ids_covers_canonical_and_aliases(self):
        from workspace.mail.queries import folder_group_ids

        self.assertEqual(
            set(folder_group_ids(self.trash)), {self.trash.pk, self.corbeille.pk}
        )

    def test_folder_group_ids_from_an_alias_returns_the_same_group(self):
        """Bookmarks, saved rules and AI tool calls carry pre-merge UUIDs."""
        from workspace.mail.queries import folder_group_ids

        self.assertEqual(
            set(folder_group_ids(self.corbeille)),
            set(folder_group_ids(self.trash)),
        )

    def test_special_folder_skips_aliases(self):
        """Both folders are typed trash; only the canonical may be resolved.

        Without the alias_of filter this falls through to Meta.ordering
        (["account", "name"]) and returns Corbeille, purely because C sorts
        before T.
        """
        from workspace.mail.queries import special_folder

        self.assertEqual(
            special_folder(self.account, MailFolder.FolderType.TRASH), self.trash
        )

    def test_special_folder_returns_none_when_absent(self):
        from workspace.mail.queries import special_folder

        self.assertIsNone(special_folder(self.account, MailFolder.FolderType.ARCHIVE))
