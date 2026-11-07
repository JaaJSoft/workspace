from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.folder_merge import promote_alias
from workspace.mail.services.imap_sync import (
    accounts_with_sync_errors,
    queue_account_syncs,
    sync_folder_messages,
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
        """_detect_folder_type("Corbeille") is "other"; the group says "sent".

        A discriminating alias needs a name that would detect to something
        other than the group's type - "Sent" alone detects to "sent" and
        would pass even without the guard.
        """
        corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="sent",
            alias_of=self.envoyes,
        )

        self._sync([("", "/", "Envoyes"), ("", "/", "Sent"), ("", "/", "Corbeille")])

        self.sent.refresh_from_db()
        corbeille.refresh_from_db()
        self.assertEqual(self.sent.folder_type, "sent")
        self.assertEqual(self.sent.alias_of_id, self.envoyes.pk)
        self.assertEqual(corbeille.folder_type, "sent")
        self.assertEqual(corbeille.alias_of_id, self.envoyes.pk)

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
        """A single-alias group can't tell promote_alias from SET_NULL alone.

        With only one alias, the FK's own on_delete=SET_NULL already leaves it
        with alias_of_id=None once the canonical row is gone - deleting the
        promote_alias() call from discovery would still pass. A second alias
        makes the two diverge: SET_NULL scatters both into standalone rows,
        while promote_alias crowns the oldest and re-points the other at it.
        """
        second_alias = MailFolder.objects.create(
            account=self.account,
            name="SentBox",
            display_name="SentBox",
            folder_type="sent",
            alias_of=self.envoyes,
        )

        self._sync([("", "/", "Sent"), ("", "/", "SentBox")])

        self.sent.refresh_from_db()
        second_alias.refresh_from_db()
        self.assertIsNone(self.sent.alias_of_id)
        self.assertEqual(second_alias.alias_of_id, self.sent.pk)
        self.assertFalse(MailFolder.objects.filter(pk=self.envoyes.pk).exists())

    def test_exclude_ids_covers_every_vanishing_folder_in_the_pass(self):
        """The canonical and its oldest alias vanish together.

        Discovery must exclude both from heir candidacy - not just the
        canonical - so the next-oldest surviving alias is promoted instead of
        a folder that is disappearing in the same pass. Wrapping the real
        promote_alias also pins that discovery threads exclude_ids through at
        all: this branch had zero coverage before this test.
        """
        survivor = MailFolder.objects.create(
            account=self.account,
            name="SentBox",
            display_name="SentBox",
            folder_type="sent",
            alias_of=self.envoyes,
        )

        with patch(
            "workspace.mail.services.folder_merge.promote_alias",
            wraps=promote_alias,
        ) as mock_promote_alias:
            self._sync([("", "/", "SentBox")])

        self.assertEqual(mock_promote_alias.call_count, 2)
        expected_excluded = {self.envoyes.pk, self.sent.pk}
        for call in mock_promote_alias.call_args_list:
            self.assertEqual(
                set(call.kwargs.get("exclude_ids") or ()), expected_excluded
            )

        survivor.refresh_from_db()
        self.assertIsNone(survivor.alias_of_id)
        self.assertFalse(MailFolder.objects.filter(pk=self.envoyes.pk).exists())
        self.assertFalse(MailFolder.objects.filter(pk=self.sent.pk).exists())


class UidValidityResetTests(TestCase):
    """A UIDVALIDITY change resets the cursor in the same write as the validity."""

    def setUp(self):
        self.user = User.objects.create_user(username="uidreset", password="pw")
        self.account = _make_account(self.user, "reset@test.com")
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
            uid_validity=111,
            last_sync_uid=500,
        )
        MailMessage.objects.create(
            account=self.account, folder=self.folder, imap_uid=500
        )

    def _connection(self, uid):
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"3"])
        conn.untagged_responses = {"OK": [b"[UIDVALIDITY 222]"]}
        conn.uid.side_effect = uid
        return conn

    def _sync_dying_after_select(self):
        conn = self._connection(ConnectionResetError("connection dropped"))
        with (
            patch("workspace.mail.services.imap_sync.connect_imap", return_value=conn),
            self.assertRaises(ConnectionResetError),
        ):
            sync_folder_messages(self.account, self.folder)

    def test_a_sync_dying_after_the_reset_leaves_the_cursor_at_zero(self):
        self._sync_dying_after_select()

        stored = MailFolder.objects.get(pk=self.folder.pk)
        self.assertEqual(stored.uid_validity, 222)
        self.assertEqual(stored.last_sync_uid, 0)
        self.assertFalse(MailMessage.objects.filter(folder=self.folder).exists())

    def test_the_next_sync_searches_the_whole_folder_again(self):
        self._sync_dying_after_select()
        searches = []

        def uid(command, *args):
            if command == "SEARCH":
                searches.append(args[1])
            return ("OK", [b""])

        folder = MailFolder.objects.get(pk=self.folder.pk)
        conn = self._connection(uid)
        with patch("workspace.mail.services.imap_sync.connect_imap", return_value=conn):
            sync_folder_messages(self.account, folder)

        self.assertEqual(searches[0], "ALL")
