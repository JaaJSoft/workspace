"""The shared composition and delivery services.

These are what the compose dialog's endpoints and the assistant's tools both
call, so the invariants that used to live inside a view are pinned here once.
"""

import uuid as uuid_mod
from email import message_from_bytes
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.drafts import save_composed_draft
from workspace.mail.services.sending import deliver_email
from workspace.mail.services.smtp import SentMessage

User = get_user_model()


class ComposedDraftTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="drafter", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="me@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.drafts = MailFolder.objects.create(
            account=self.account,
            name="Drafts",
            display_name="Drafts",
            folder_type="drafts",
        )

    def test_bcc_goes_into_the_header(self):
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            save_composed_draft(
                self.account,
                to=["bob@example.com"],
                subject="Hi",
                body_text="hello",
                bcc=["hidden@example.com"],
            )
        raw = save.call_args.args[1]
        self.assertEqual(
            message_from_bytes(raw)["Bcc"],
            "hidden@example.com",
            "a draft's Bcc has no home but the header - it is re-parsed from "
            "IMAP when reopened",
        )

    def test_threading_headers_come_from_the_stored_parent(self):
        parent = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=3,
            message_id="<parent@example.com>",
            references="<root@example.com>",
        )
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            save_composed_draft(
                self.account,
                to=["bob@example.com"],
                body_text="ok",
                reply_message_id=parent.uuid,
            )
        parsed = message_from_bytes(save.call_args.args[1])
        self.assertEqual(parsed["In-Reply-To"], "<parent@example.com>")
        self.assertEqual(
            parsed["References"], "<root@example.com> <parent@example.com>"
        )

    def test_a_parent_on_another_account_is_ignored(self):
        stranger = User.objects.create_user(username="stranger", password="pw")
        other = MailAccount.objects.create(
            owner=stranger,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        folder = MailFolder.objects.create(
            account=other, name="INBOX", display_name="Inbox", folder_type="inbox"
        )
        theirs = MailMessage.objects.create(
            account=other, folder=folder, imap_uid=1, message_id="<theirs@example.com>"
        )
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            save_composed_draft(
                self.account, to=["bob@example.com"], reply_message_id=theirs.uuid
            )
        self.assertIsNone(message_from_bytes(save.call_args.args[1])["In-Reply-To"])

    def test_replacing_a_draft_passes_its_imap_uid(self):
        previous = MailMessage.objects.create(
            account=self.account, folder=self.drafts, imap_uid=77, is_draft=True
        )
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            save_composed_draft(
                self.account, to=["bob@example.com"], replace_draft_uuid=previous.uuid
            )
        self.assertEqual(save.call_args.kwargs["old_uid"], 77)

    def test_a_draft_that_vanished_still_saves_a_fresh_one(self):
        # Deleted from another device between two autosaves: replacing
        # nothing is not a reason to lose what the user just typed.
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            save_composed_draft(
                self.account,
                to=["bob@example.com"],
                replace_draft_uuid=uuid_mod.uuid4(),
            )
        self.assertIsNone(save.call_args.kwargs["old_uid"])


class DeliverEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sender", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="me@example.com",
        )
        self.sent = MailFolder.objects.create(
            account=self.account,
            name="Sent",
            display_name="Sent",
            folder_type="sent",
        )
        self.delivery = SentMessage(outgoing=b"out", archived=b"archived")

    def test_the_archived_variant_is_what_reaches_the_sent_folder(self):
        with (
            patch(
                "workspace.mail.services.smtp.send_email", return_value=self.delivery
            ),
            patch("workspace.mail.services.imap_messages.append_to_sent") as append,
            patch("workspace.mail.services.imap_sync.sync_folder_messages") as sync,
        ):
            result = deliver_email(self.account, to=["bob@example.com"], subject="Hi")

        self.assertTrue(result.archived)
        self.assertEqual(append.call_args.args[1], b"archived")
        sync.assert_called_once()

    def test_a_failed_archive_is_reported_but_the_send_still_stands(self):
        with (
            patch(
                "workspace.mail.services.smtp.send_email", return_value=self.delivery
            ),
            patch(
                "workspace.mail.services.imap_messages.append_to_sent",
                side_effect=OSError("connection reset"),
            ),
            patch("workspace.mail.services.imap_sync.sync_folder_messages") as sync,
        ):
            result = deliver_email(self.account, to=["bob@example.com"], subject="Hi")

        self.assertFalse(result.archived)
        self.assertEqual(result.sent, self.delivery)
        sync.assert_not_called()

    def test_an_smtp_failure_propagates(self):
        with (
            patch(
                "workspace.mail.services.smtp.send_email",
                side_effect=OSError("refused"),
            ),
            patch("workspace.mail.services.imap_messages.append_to_sent") as append,
        ):
            with self.assertRaises(OSError):
                deliver_email(self.account, to=["bob@example.com"], subject="Hi")
        append.assert_not_called()
