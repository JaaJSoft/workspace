from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.compose import (
    QUOTE_MAX_CHARS,
    build_reply,
    reply_subject,
)

User = get_user_model()
PARIS = ZoneInfo("Europe/Paris")


class ReplySubjectTests(TestCase):
    def test_prefixes_once(self):
        self.assertEqual(reply_subject("Lunch"), "Re: Lunch")
        self.assertEqual(reply_subject("Re: Lunch"), "Re: Lunch")
        self.assertEqual(reply_subject("re: Lunch"), "re: Lunch")
        self.assertEqual(reply_subject("RE: Lunch"), "RE: Lunch")

    def test_empty_subject(self):
        self.assertEqual(reply_subject(""), "Re:")
        self.assertEqual(reply_subject(None), "Re:")


class BuildReplyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="replier", password="pw")
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

    def _message(self, **kwargs):
        defaults = {
            "account": self.account,
            "folder": self.inbox,
            "imap_uid": 1,
            "subject": "Quarterly review",
            "from_email": "alice@example.com",
            "from_name": "Alice",
            "date": datetime(2026, 3, 4, 9, 15, tzinfo=UTC),
            "body_text": "Are we still on for Friday?",
            "to_addresses": [
                {"name": "Me", "email": "me@example.com"},
                {"name": "Bob", "email": "bob@example.com"},
            ],
            "cc_addresses": [{"name": "Carol", "email": "carol@example.com"}],
        }
        return MailMessage.objects.create(**{**defaults, **kwargs})

    def test_reply_targets_the_sender_only(self):
        reply = build_reply(self._message(), self.account, "Yes.", PARIS)
        self.assertEqual(reply.to, ["alice@example.com"])
        self.assertEqual(reply.cc, [])
        self.assertEqual(reply.subject, "Re: Quarterly review")

    def test_reply_all_keeps_everyone_but_the_account(self):
        reply = build_reply(
            self._message(), self.account, "Yes.", PARIS, reply_all=True
        )
        self.assertEqual(reply.to, ["alice@example.com", "bob@example.com"])
        self.assertEqual(reply.cc, ["carol@example.com"])

    def test_reply_all_drops_own_address_whatever_its_case(self):
        message = self._message(
            to_addresses=[{"email": "ME@Example.com"}],
            cc_addresses=[{"email": "Me@example.COM"}],
        )
        reply = build_reply(message, self.account, "Yes.", PARIS, reply_all=True)
        self.assertEqual(reply.to, ["alice@example.com"])
        self.assertEqual(reply.cc, [])

    def test_reply_all_never_repeats_an_address_across_to_and_cc(self):
        message = self._message(
            to_addresses=[{"email": "bob@example.com"}],
            cc_addresses=[{"email": "BOB@example.com"}],
        )
        reply = build_reply(message, self.account, "Yes.", PARIS, reply_all=True)
        self.assertEqual(reply.to, ["alice@example.com", "bob@example.com"])
        self.assertEqual(reply.cc, [])

    def test_body_carries_the_quoted_original_in_the_user_timezone(self):
        reply = build_reply(self._message(), self.account, "Yes, Friday works.", PARIS)
        self.assertTrue(reply.body_text.startswith("Yes, Friday works.\n\n---\n"))
        # 09:15 UTC is 10:15 in Paris in March.
        self.assertIn("On 2026-03-04 10:15, Alice wrote:", reply.body_text)
        self.assertIn("> Are we still on for Friday?", reply.body_text)

    def test_long_original_is_trimmed(self):
        message = self._message(body_text="x" * (QUOTE_MAX_CHARS + 500))
        reply = build_reply(message, self.account, "Ok.", PARIS)
        self.assertIn("[…]", reply.body_text)
        self.assertLess(len(reply.body_text), QUOTE_MAX_CHARS + 400)

    def test_message_from_the_account_itself_has_no_recipient(self):
        message = self._message(from_email="me@example.com", from_name="Me")
        reply = build_reply(message, self.account, "Ok.", PARIS)
        self.assertEqual(reply.to, [])
