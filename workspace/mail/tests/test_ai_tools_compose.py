import email
import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.ai.models import BotProfile
from workspace.mail.ai_tools import (
    DraftEmailParams,
    MailToolProvider,
    ReplyToEmailParams,
    SendEmailParams,
    _pending_send_key,
)
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.smtp import SentMessage

User = get_user_model()


def plain_body(raw_message):
    """The text/plain part of a built MIME message, decoded."""
    parsed = email.message_from_bytes(raw_message)
    for part in parsed.walk():
        if part.get_content_type() == "text/plain":
            return part.get_payload(decode=True).decode()
    return ""


class MailComposeToolsTestCase(TestCase):
    """Fixtures shared by the draft, reply and send tool tests."""

    def setUp(self):
        self.user = User.objects.create_user(username="composer", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            display_name="Me",
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
        self.sent = MailFolder.objects.create(
            account=self.account,
            name="Sent",
            display_name="Sent",
            folder_type="sent",
        )
        self.tools = MailToolProvider()

    def tearDown(self):
        cache.clear()

    def _bot(self, can_send_email=False):
        bot_user = User.objects.create_user(
            username=f"bot-{can_send_email}", password="pw"
        )
        BotProfile.objects.create(user=bot_user, can_send_email=can_send_email)
        # bot_profile is a reverse OneToOne, cached on first access.
        bot_user.refresh_from_db()
        return bot_user

    def _draft_row(self, uid=99):
        return MailMessage.objects.create(
            account=self.account,
            folder=self.drafts,
            imap_uid=uid,
            subject="Lunch",
            to_addresses=[{"name": "", "email": "alice@example.com"}],
            is_draft=True,
        )

    def _call(self, tool, params, bot=None):
        return tool(
            params,
            user=self.user,
            bot=bot,
            conversation_id=None,
            context={},
        )


class DraftEmailTests(MailComposeToolsTestCase):
    def test_draft_is_appended_and_described(self):
        draft = self._draft_row()
        with patch(
            "workspace.mail.services.imap_messages.save_draft", return_value=draft
        ) as save:
            result = self._call(
                self.tools.draft_email,
                DraftEmailParams(
                    to=["alice@example.com"], subject="Lunch", body="Friday?"
                ),
            )

        payload = json.loads(result)
        self.assertEqual(payload["status"], "draft saved")
        self.assertEqual(payload["uuid"], str(draft.uuid))
        self.assertEqual(payload["folder"], "Drafts")
        account_arg, raw = save.call_args.args
        self.assertEqual(account_arg, self.account)
        self.assertIn(b"To: alice@example.com", raw)
        self.assertIn(b"Subject: Lunch", raw)

    def test_bcc_is_written_into_the_draft_headers(self):
        # Drafts are re-parsed from IMAP on open, and the header is the only
        # place the Bcc list survives that round-trip.
        with patch(
            "workspace.mail.services.imap_messages.save_draft",
            return_value=self._draft_row(),
        ) as save:
            self._call(
                self.tools.draft_email,
                DraftEmailParams(
                    to=["alice@example.com"],
                    subject="Lunch",
                    body="Friday?",
                    bcc=["hidden@example.com"],
                ),
            )
        raw = save.call_args.args[1]
        self.assertIn(b"Bcc: hidden@example.com", raw)

    def test_rejects_a_recipient_that_is_not_an_address(self):
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            result = self._call(
                self.tools.draft_email,
                DraftEmailParams(to=["Alice"], subject="Lunch", body="Friday?"),
            )
        self.assertIn("not a usable email address", result)
        save.assert_not_called()

    def test_missing_drafts_folder_is_reported(self):
        self.drafts.delete()
        with patch(
            "workspace.mail.services.imap_messages.save_draft", return_value=None
        ):
            result = self._call(
                self.tools.draft_email,
                DraftEmailParams(
                    to=["alice@example.com"], subject="Lunch", body="Friday?"
                ),
            )
        self.assertIn("no Drafts folder", result)

    def test_imap_failure_comes_back_as_a_readable_result(self):
        with patch(
            "workspace.mail.services.imap_messages.save_draft",
            side_effect=TimeoutError("timed out"),
        ):
            result = self._call(
                self.tools.draft_email,
                DraftEmailParams(
                    to=["alice@example.com"], subject="Lunch", body="Friday?"
                ),
            )
        self.assertIn("did not complete the draft", result)

    def test_second_account_must_be_named(self):
        MailAccount.objects.create(
            owner=self.user,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            result = self._call(
                self.tools.draft_email,
                DraftEmailParams(
                    to=["alice@example.com"], subject="Lunch", body="Friday?"
                ),
            )
        self.assertIn("account argument is required", result)
        self.assertIn("other@example.com", result)
        save.assert_not_called()

    def test_named_account_is_honoured(self):
        other = MailAccount.objects.create(
            owner=self.user,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        MailFolder.objects.create(
            account=other, name="Drafts", display_name="Drafts", folder_type="drafts"
        )
        with patch(
            "workspace.mail.services.imap_messages.save_draft",
            return_value=self._draft_row(),
        ) as save:
            self._call(
                self.tools.draft_email,
                DraftEmailParams(
                    to=["alice@example.com"],
                    subject="Lunch",
                    body="Friday?",
                    account="other@example.com",
                ),
            )
        self.assertEqual(save.call_args.args[0], other)


class ReplyToEmailTests(MailComposeToolsTestCase):
    def setUp(self):
        super().setUp()
        self.parent = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=7,
            message_id="<parent@example.com>",
            references="<root@example.com>",
            subject="Quarterly review",
            from_email="alice@example.com",
            from_name="Alice",
            date=datetime(2026, 3, 4, 9, 15, tzinfo=UTC),
            body_text="Are we still on for Friday?",
            to_addresses=[
                {"email": "me@example.com"},
                {"email": "bob@example.com"},
            ],
        )

    def test_reply_threads_and_quotes(self):
        with patch(
            "workspace.mail.services.imap_messages.save_draft",
            return_value=self._draft_row(),
        ) as save:
            result = self._call(
                self.tools.reply_to_email,
                ReplyToEmailParams(uuid=self.parent.uuid, body="Yes, Friday works."),
            )

        self.assertEqual(json.loads(result)["status"], "draft saved")
        raw = save.call_args.args[1]
        self.assertIn(b"In-Reply-To: <parent@example.com>", raw)
        self.assertIn(b"References: <root@example.com> <parent@example.com>", raw)
        self.assertIn(b"Subject: Re: Quarterly review", raw)
        self.assertIn(b"To: alice@example.com", raw)
        body = plain_body(raw)
        self.assertTrue(body.startswith("Yes, Friday works."))
        self.assertIn("On 2026-03-04 09:15, Alice wrote:", body)
        self.assertIn("> Are we still on for Friday?", body)

    def test_reply_all_widens_the_recipients(self):
        with patch(
            "workspace.mail.services.imap_messages.save_draft",
            return_value=self._draft_row(),
        ) as save:
            self._call(
                self.tools.reply_to_email,
                ReplyToEmailParams(uuid=self.parent.uuid, body="Yes.", reply_all=True),
            )
        raw = save.call_args.args[1]
        self.assertIn(b"To: alice@example.com, bob@example.com", raw)

    def test_another_users_message_is_not_reachable(self):
        stranger = User.objects.create_user(username="stranger", password="pw")
        other_account = MailAccount.objects.create(
            owner=stranger,
            email="stranger@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="stranger@example.com",
        )
        other_folder = MailFolder.objects.create(
            account=other_account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        theirs = MailMessage.objects.create(
            account=other_account, folder=other_folder, imap_uid=1, subject="Private"
        )
        with patch("workspace.mail.services.imap_messages.save_draft") as save:
            result = self._call(
                self.tools.reply_to_email,
                ReplyToEmailParams(uuid=theirs.uuid, body="Hi"),
            )
        self.assertEqual(result, "Email not found or access denied.")
        save.assert_not_called()


class SendEmailTests(MailComposeToolsTestCase):
    def _send(self, params, bot, context):
        return self.tools.send_email(
            params, user=self.user, bot=bot, conversation_id=None, context=context
        )

    def test_a_bot_without_the_capability_cannot_send(self):
        context = {}
        with patch("workspace.mail.services.smtp.send_email") as smtp:
            result = self._send(
                SendEmailParams(to=["alice@example.com"], subject="Hi", body="Hello"),
                self._bot(can_send_email=False),
                context,
            )
        self.assertIn("not allowed to send email", result)
        self.assertNotIn("stop_after_round", context)
        smtp.assert_not_called()

    def test_first_call_asks_and_sends_nothing(self):
        context = {}
        with patch("workspace.mail.services.smtp.send_email") as smtp:
            result = self._send(
                SendEmailParams(to=["alice@example.com"], subject="Hi", body="Hello"),
                self._bot(can_send_email=True),
                context,
            )

        payload = json.loads(result)
        self.assertEqual(payload["status"], "awaiting confirmation")
        self.assertTrue(context["stop_after_round"])
        self.assertIn("alice@example.com", context["question"]["question"])
        self.assertGreaterEqual(len(context["question"]["options"]), 2)
        smtp.assert_not_called()
        self.assertIsNotNone(
            cache.get(_pending_send_key(payload["confirmation_token"]))
        )

    def test_confirmed_call_sends_what_the_user_was_shown(self):
        bot = self._bot(can_send_email=True)
        context = {}
        token = json.loads(
            self._send(
                SendEmailParams(
                    to=["alice@example.com"],
                    subject="Hi",
                    body="Hello",
                    bcc=["hidden@example.com"],
                ),
                bot,
                context,
            )
        )["confirmation_token"]

        sent = SentMessage(outgoing=b"out", archived=b"archived")
        with (
            patch("workspace.mail.services.smtp.send_email", return_value=sent) as smtp,
            patch("workspace.mail.services.imap_messages.append_to_sent") as append,
            patch("workspace.mail.services.imap_sync.sync_folder_messages"),
        ):
            result = self._send(
                # Tampered arguments on the confirming call: the payload comes
                # from the cache, so they must not reach SMTP.
                SendEmailParams(
                    to=["mallory@example.com"],
                    subject="Something else",
                    body="Not this",
                    confirmation_token=token,
                ),
                bot,
                {},
            )

        self.assertEqual(json.loads(result)["status"], "sent")
        kwargs = smtp.call_args.kwargs
        self.assertEqual(kwargs["to"], ["alice@example.com"])
        self.assertEqual(kwargs["subject"], "Hi")
        self.assertEqual(kwargs["body_text"], "Hello")
        self.assertEqual(kwargs["bcc"], ["hidden@example.com"])
        # Bcc belongs in the SMTP envelope only - the header would leak the
        # hidden recipients to everyone else.
        self.assertNotIn("include_bcc", kwargs)
        self.assertEqual(append.call_args.args[1], b"archived")

    def test_a_token_can_only_be_redeemed_once(self):
        bot = self._bot(can_send_email=True)
        token = json.loads(
            self._send(
                SendEmailParams(to=["alice@example.com"], subject="Hi", body="Hello"),
                bot,
                {},
            )
        )["confirmation_token"]

        sent = SentMessage(outgoing=b"out", archived=b"archived")
        with (
            patch("workspace.mail.services.smtp.send_email", return_value=sent),
            patch("workspace.mail.services.imap_messages.append_to_sent"),
            patch("workspace.mail.services.imap_sync.sync_folder_messages"),
        ):
            self._send(SendEmailParams(confirmation_token=token), bot, {})

        with patch("workspace.mail.services.smtp.send_email") as smtp:
            result = self._send(SendEmailParams(confirmation_token=token), bot, {})
        self.assertIn("unknown or has expired", result)
        smtp.assert_not_called()

    def test_a_token_belonging_to_someone_else_is_unknown(self):
        cache.set(
            _pending_send_key("borrowed"),
            {
                "user_id": self.user.pk + 1000,
                "account_id": str(self.account.uuid),
                "to": ["alice@example.com"],
                "cc": [],
                "bcc": [],
                "subject": "Hi",
                "body": "Hello",
            },
            60,
        )
        with patch("workspace.mail.services.smtp.send_email") as smtp:
            result = self._send(
                SendEmailParams(confirmation_token="borrowed"),
                self._bot(can_send_email=True),
                {},
            )
        self.assertIn("unknown or has expired", result)
        smtp.assert_not_called()

    def test_smtp_failure_is_reported_rather_than_raised(self):
        bot = self._bot(can_send_email=True)
        token = json.loads(
            self._send(
                SendEmailParams(to=["alice@example.com"], subject="Hi", body="Hello"),
                bot,
                {},
            )
        )["confirmation_token"]
        with patch(
            "workspace.mail.services.smtp.send_email",
            side_effect=TimeoutError("timed out"),
        ):
            result = self._send(SendEmailParams(confirmation_token=token), bot, {})
        self.assertIn("did not complete the send", result)

    def test_a_pending_question_defers_the_confirmation(self):
        context = {"question": {"question": "Something else?", "options": ["a", "b"]}}
        result = self._send(
            SendEmailParams(to=["alice@example.com"], subject="Hi", body="Hello"),
            self._bot(can_send_email=True),
            context,
        )
        self.assertIn("Another question is already waiting", result)
        self.assertNotIn("stop_after_round", context)
