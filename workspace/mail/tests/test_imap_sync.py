import contextlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone
from imapclient.exceptions import IMAPClientError

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services import imap_sync
from workspace.mail.services.folder_merge import promote_alias
from workspace.mail.services.imap_sync import (
    FULL_READ_INTERVAL,
    accounts_with_sync_errors,
    forget_remote_state,
    queue_account_syncs,
    sync_folder_messages,
    sync_folders,
)
from workspace.mail.tests.imap_fake import (
    BARE,
    FakeImapClient,
    FakeImapServer,
    fake_imap,
)
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

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

    def _sync(self, *mailboxes):
        """Discover the folders of a server holding `mailboxes` and INBOX."""
        server = FakeImapServer()
        for name in mailboxes:
            server.mailbox(name)
        with fake_imap(server):
            sync_folders(self.account)

    def test_canonical_keeps_its_promoted_type(self):
        """_detect_folder_type("Envoyes") is "other"; the merge said "sent"."""
        self._sync("Envoyes", "Sent")

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

        self._sync("Envoyes", "Sent", "Corbeille")

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

        self._sync("Envoyes", "Sent", "Trash")

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

        self._sync("Sent", "SentBox")

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
            self._sync("SentBox")

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


class HierarchyDelimiterTests(TestCase):
    """Only the server's own delimiter separates the levels of a name."""

    def setUp(self):
        self.user = User.objects.create_user(username="delims", password="pw")
        self.account = _make_account(self.user, "delims@test.com")

    def _sync(self, delimiter, *mailboxes):
        server = FakeImapServer(delimiter=delimiter)
        for name in mailboxes:
            server.mailbox(name)
        with fake_imap(server):
            sync_folders(self.account)

    def _folder(self, name):
        return MailFolder.objects.get(account=self.account, name=name)

    def test_a_dot_in_a_folder_name_is_not_a_level_on_a_slash_server(self):
        self._sync("/", "Clients", "Clients/acme.com")

        self.assertEqual(self._folder("Clients/acme.com").display_name, "acme.com")

    def test_a_slash_in_a_folder_name_is_not_a_level_on_a_dot_server(self):
        self._sync(".", "INBOX.Clients", "INBOX.Clients.a/b testing")

        folder = self._folder("INBOX.Clients.a/b testing")
        self.assertEqual(folder.display_name, "a/b testing")

    def test_a_dotted_leaf_is_not_typed_from_its_suffix(self):
        self._sync("/", "Projects", "Projects/site.archive")

        self.assertEqual(self._folder("Projects/site.archive").folder_type, "other")

    def test_a_nested_special_name_is_still_detected(self):
        self._sync(".", "INBOX.Trash")

        folder = self._folder("INBOX.Trash")
        self.assertEqual(folder.display_name, "Trash")
        self.assertEqual(folder.folder_type, "trash")


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

        self.server = FakeImapServer()
        self.server.mailbox("INBOX").uidvalidity = 222
        # All below the stale cursor: a search from it up finds none of them.
        self.uids = [self.server.deliver("INBOX") for _ in range(3)]

    def _sync_dying_after_select(self):
        self.server.fail("search", error=ConnectionResetError("connection dropped"))
        with fake_imap(self.server), self.assertRaises(ConnectionResetError):
            sync_folder_messages(self.account, self.folder)
        self.server.recover("search")

    def test_a_sync_dying_after_the_reset_leaves_the_cursor_at_zero(self):
        self._sync_dying_after_select()

        stored = MailFolder.objects.get(pk=self.folder.pk)
        self.assertEqual(stored.uid_validity, 222)
        self.assertEqual(stored.last_sync_uid, 0)
        self.assertFalse(MailMessage.objects.filter(folder=self.folder).exists())

    def test_the_next_sync_searches_the_whole_folder_again(self):
        self._sync_dying_after_select()

        folder = MailFolder.objects.get(pk=self.folder.pk)
        with fake_imap(self.server):
            sync_folder_messages(self.account, folder)

        self.assertEqual(self.server.named("search")[-1], ("search", "ALL"))
        imported = MailMessage.objects.filter(folder=folder).values_list(
            "imap_uid", flat=True
        )
        self.assertEqual(sorted(imported), self.uids)


class InboxTestCase(TestCase):
    """An account whose inbox has not been synced yet."""

    def setUp(self):
        self.user = User.objects.create_user(username="syncinbox", password="pw")
        self.account = _make_account(self.user, "inbox@test.com")
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def _sync(self):
        sync_folder_messages(self.account, self.folder)

    def _message(self, uid):
        return MailMessage.objects.get(folder=self.folder, imap_uid=uid)


def _flag_reads(commands):
    """The FETCH commands among `commands` that ask for flags alone."""
    return [
        command
        for command in commands
        if command[0] == "fetch" and command[2] == ["FLAGS"]
    ]


@contextlib.contextmanager
def fetch_answers():
    """What the server answers to each FETCH, in order."""
    answers = []
    answer = FakeImapClient.fetch

    def fetch(client, messages, data, modifiers=None):
        answers.append(answer(client, messages, data, modifiers))
        return answers[-1]

    with patch.object(FakeImapClient, "fetch", fetch):
        yield answers


def refused_flag_reads():
    """Have the server refuse every FETCH asking for flags alone, which
    leaves the import of new messages working."""
    answer = FakeImapClient.fetch

    def fetch(client, messages, data, modifiers=None):
        if list(data) == ["FLAGS"]:
            raise IMAPClientError("fetch failed: NO")
        return answer(client, messages, data, modifiers)

    return patch.object(FakeImapClient, "fetch", fetch)


class RefusedSelectTests(InboxTestCase):
    def test_a_folder_that_cannot_be_selected_is_left_alone(self):
        MailMessage.objects.create(account=self.account, folder=self.folder, imap_uid=7)

        with fake_imap() as server:
            server.fail("select")
            with self.assertLogs("workspace.mail.services.imap_sync", level="WARNING"):
                self._sync()

        self.assertEqual(server.named("search", "fetch"), [])
        self.assertIsNone(self._message(7).deleted_at)


class UnchangedMailboxTests(InboxTestCase):
    """What a pass sends for a mailbox it has already read.

    A CONDSTORE server says in its SELECT answer whether anything moved, so
    an untouched mailbox costs one command. Any other server has to be asked
    for its flags every time.
    """

    def test_an_untouched_mailbox_is_selected_and_nothing_else(self):
        with fake_imap() as server:
            server.deliver("INBOX")
            self._sync()
            first_pass = len(server.commands)

            self._sync()

        self.assertEqual(
            server.commands[first_pass:], [("select", "INBOX", True), ("logout",)]
        )

    def test_once_a_day_the_flags_are_read_in_full_all_the_same(self):
        """What the server says only covers what the server changed. A row
        that drifted for any other reason is put right by the full read."""
        with fake_imap() as server:
            uid = server.deliver("INBOX", flags=[r"\Seen"])
            self._sync()
            MailMessage.objects.filter(folder=self.folder).update(is_read=False)
            first_pass = len(server.commands)

            self._sync()
            self.assertFalse(self._message(uid).is_read, "skipped: nothing moved")

            later = timezone.now() + FULL_READ_INTERVAL + timedelta(minutes=1)
            with patch("django.utils.timezone.now", return_value=later):
                self._sync()

        self.assertTrue(self._message(uid).is_read)
        self.assertEqual(
            _flag_reads(server.commands[first_pass:]),
            [("fetch", [uid], ["FLAGS"], None)],
        )

    def test_without_condstore_the_flags_are_read_on_every_pass(self):
        with fake_imap(capabilities=BARE) as server:
            uid = server.deliver("INBOX")
            self._sync()
            first_pass = len(server.commands)
            server.set_flags("INBOX", uid, r"\Flagged")

            self._sync()

        self.assertIsNone(self.folder.remote_state)
        commands = server.commands[first_pass:]
        self.assertEqual(
            [command[0] for command in commands],
            ["select", "search", "fetch", "logout"],
        )
        self.assertEqual(_flag_reads(commands), [("fetch", [uid], ["FLAGS"], None)])
        self.assertTrue(self._message(uid).is_starred)

    def test_a_flag_set_by_another_client_is_the_only_one_read(self):
        """One SEARCH names what moved since the last pass, whatever the
        size of the folder, and only that is fetched."""
        with fake_imap() as server:
            untouched = server.deliver("INBOX")
            read_elsewhere = server.deliver("INBOX")
            self._sync()
            first_pass = len(server.commands)
            known_modseq = self.folder.remote_state["highestmodseq"]
            server.set_flags("INBOX", read_elsewhere, r"\Seen")

            self._sync()

        commands = server.commands[first_pass:]
        self.assertEqual(
            [command for command in commands if command[0] == "search"],
            [
                ("search", ["UID", f"{untouched}:*"]),
                ("search", ["UID", f"{untouched}:*", "MODSEQ", known_modseq + 1]),
            ],
        )
        self.assertEqual(
            _flag_reads(commands), [("fetch", [read_elsewhere], ["FLAGS"], None)]
        )
        self.assertTrue(self._message(read_elsewhere).is_read)
        self.assertFalse(self._message(untouched).is_read)

    def test_a_large_folder_with_nothing_changed_costs_two_searches(self):
        with (
            fake_imap() as server,
            patch("workspace.mail.services.imap_sync.UID_BATCH_SIZE", 3),
        ):
            for _ in range(8):
                server.deliver("INBOX")
            self._sync()
            first_pass = len(server.commands)
            # Something moved, but no flag of a stored message.
            server.deliver("INBOX")

            self._sync()

        commands = [command[0] for command in server.commands[first_pass:]]
        self.assertEqual(commands.count("search"), 2)
        self.assertEqual(_flag_reads(server.commands[first_pass:]), [])

    def test_a_message_delivered_since_the_last_pass_is_imported(self):
        with fake_imap() as server:
            server.deliver("INBOX")
            self._sync()
            new = server.deliver("INBOX", subject="Later")

            self._sync()

        self.assertEqual(self._message(new).subject, "Later")

    def test_a_message_expunged_since_the_last_pass_is_removed(self):
        with fake_imap() as server:
            kept = server.deliver("INBOX")
            gone = server.deliver("INBOX")
            self._sync()
            server.remove("INBOX", gone)

            self._sync()

        self.assertIsNotNone(self._message(gone).deleted_at)
        self.assertIsNone(self._message(kept).deleted_at)


class OtherWritersTests(InboxTestCase):
    """A pass is not alone: a request saves a draft, flags a message or
    files one while it runs, and what the pass read a moment ago no longer
    describes the folder."""

    def test_a_message_stored_while_the_pass_runs_is_not_taken_for_deleted(self):
        """The server's list of UIDs is from before the message: missing
        from it, the message must not be hidden - for good, since its row
        keeps the next pass from importing it again."""
        search = FakeImapClient.search
        stored = []

        def search_then_a_draft_is_saved(client, criteria="ALL"):
            answer = search(client, criteria)
            if not stored:
                stored.append(client.server.deliver("INBOX", subject="Draft"))
                MailMessage.objects.create(
                    account=self.account, folder=self.folder, imap_uid=stored[0]
                )
            return answer

        with fake_imap() as server:
            known = server.deliver("INBOX")
            self._sync()
            server.set_flags("INBOX", known, r"\Seen")

            with patch.object(FakeImapClient, "search", search_then_a_draft_is_saved):
                self._sync()
            self.assertIsNone(self._message(stored[0]).deleted_at)

            self._sync()

        self.assertIsNone(self._message(stored[0]).deleted_at)
        self.assertTrue(self._message(known).is_read)

    def test_a_message_that_could_not_be_stored_is_fetched_again(self):
        """Its failure must not be covered by a stored state: the folder
        would then be skipped, unchanged, with the message still missing."""
        parse = imap_sync._parse_message
        failures = [OperationalError("database is locked")]

        def parse_or_fail(raw, account, folder, uid, flags, known_uids=None):
            if uid == late and failures:
                raise failures.pop()
            return parse(raw, account, folder, uid, flags, known_uids=known_uids)

        with (
            fake_imap() as server,
            patch.object(imap_sync, "_parse_message", parse_or_fail),
        ):
            first = server.deliver("INBOX")
            self._sync()
            state = self.folder.remote_state
            late = server.deliver("INBOX", subject="Late")

            self._sync()
            self.assertFalse(
                MailMessage.objects.filter(folder=self.folder, imap_uid=late).exists()
            )
            self.assertEqual(
                MailFolder.objects.get(pk=self.folder.pk).remote_state, state
            )
            self.assertEqual(self.folder.last_sync_uid, first)

            self._sync()

        self.assertEqual(self._message(late).subject, "Late")

    def _forgotten_during_the_pass(self):
        """A pass during which a flag the server refused is kept on a row."""
        fetch = FakeImapClient.fetch
        forgotten = []

        def fetch_then_a_flag_is_refused(client, messages, data, modifiers=None):
            answer = fetch(client, messages, data, modifiers)
            if not forgotten:
                forget_remote_state([self.folder.pk])
                forgotten.append(True)
            return answer

        with patch.object(FakeImapClient, "fetch", fetch_then_a_flag_is_refused):
            self._sync()
        self.assertTrue(forgotten)

    def test_a_state_forgotten_during_a_first_pass_stays_forgotten(self):
        with fake_imap() as server:
            server.deliver("INBOX")

            self._forgotten_during_the_pass()

        self.assertIsNone(MailFolder.objects.get(pk=self.folder.pk).remote_state)

    def test_a_state_forgotten_during_a_later_pass_stays_forgotten(self):
        with fake_imap() as server:
            server.deliver("INBOX")
            self._sync()
            self.assertIsNotNone(self.folder.remote_state)
            server.deliver("INBOX")

            self._forgotten_during_the_pass()

        self.assertIsNone(MailFolder.objects.get(pk=self.folder.pk).remote_state)

    def test_a_state_forgotten_since_the_folder_was_loaded_is_not_used(self):
        """An account pass loads its folders once and reaches the last one
        minutes later: what that copy remembers may have been forgotten."""
        with fake_imap() as server:
            uid = server.deliver("INBOX", flags=[r"\Seen"])
            self._sync()
            loaded_earlier = MailFolder.objects.get(pk=self.folder.pk)
            MailMessage.objects.filter(folder=self.folder).update(is_read=False)
            forget_remote_state([self.folder.pk])

            sync_folder_messages(self.account, loaded_earlier)

        self.assertTrue(self._message(uid).is_read)
        self.assertIsNotNone(MailFolder.objects.get(pk=self.folder.pk).remote_state)


class FlagReadBatchingTests(InboxTestCase):
    """Regression for #1744: one FETCH naming every UID of a large folder is
    a longer command than a server accepts."""

    def test_the_flags_of_a_large_folder_are_read_in_several_commands(self):
        # Without CONDSTORE every flag is read on every pass.
        with fake_imap(capabilities=BARE) as server:
            uids = [server.deliver("INBOX") for _ in range(8)]
            self._sync()
            first_pass = len(server.commands)
            # One per command, the last one included.
            read_elsewhere = [uids[0], uids[4], uids[7]]
            for uid in read_elsewhere:
                server.set_flags("INBOX", uid, r"\Seen")

            with patch("workspace.mail.services.imap_sync.UID_BATCH_SIZE", 3):
                self._sync()

        self.assertEqual(
            [command[1] for command in _flag_reads(server.commands[first_pass:])],
            [uids[0:3], uids[3:6], uids[6:8]],
        )
        read = MailMessage.objects.filter(folder=self.folder, is_read=True)
        self.assertEqual(
            sorted(read.values_list("imap_uid", flat=True)), read_elsewhere
        )


class ReconcileFailureTests(InboxTestCase):
    """Regression for #1744: flags that cannot be read fail the pass, and
    cost the messages it imported neither their rules nor their notification."""

    def setUp(self):
        super().setUp()
        set_setting(self.user, "mail", "notify_mode", "all")
        self.enterContext(
            patch("workspace.notifications.tasks.send_push_notification.delay")
        )

    def tearDown(self):
        cache.clear()

    def test_new_messages_are_handled_before_the_pass_fails(self):
        with fake_imap() as server:
            known = server.deliver("INBOX")
            self._sync()
            state = self.folder.remote_state
            new = server.deliver("INBOX", subject="News")
            server.set_flags("INBOX", known, r"\Seen")

            with (
                refused_flag_reads(),
                patch(
                    "workspace.mail.services.rules.engine.run_rules_for_messages"
                ) as rules,
                patch("workspace.ai.client.is_ai_enabled", return_value=True),
                patch("workspace.ai.services.dispatch.dispatch") as dispatch,
                self.assertRaises(IMAPClientError),
            ):
                self._sync()

            message = self._message(new)
            rules.assert_called_once_with(self.account, [str(message.uuid)])
            self.assertTrue(dispatch.called)
            self.assertEqual(
                Notification.objects.filter(mail_message=message).count(), 1
            )
            stored = MailFolder.objects.get(pk=self.folder.pk)
            self.assertEqual(stored.last_sync_uid, new)
            # Still the state of the last pass that read everything: the
            # next one cannot take the mailbox for unchanged.
            self.assertEqual(stored.remote_state, state)
            self.assertFalse(self._message(known).is_read)

            self._sync()

        self.assertTrue(self._message(known).is_read)
