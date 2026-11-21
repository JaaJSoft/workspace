from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
)
from workspace.mail.services.compose import (
    build_forward,
    build_reply,
    forward_subject,
    reply_subject,
    signature_block,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


class SubjectTests(TestCase):
    def test_reply_prefixes_once_whatever_the_case(self):
        self.assertEqual(reply_subject("Lunch"), "Re: Lunch")
        self.assertEqual(reply_subject("Re: Lunch"), "Re: Lunch")
        self.assertEqual(reply_subject("re: Lunch"), "re: Lunch")
        self.assertEqual(reply_subject("RE: Lunch"), "RE: Lunch")

    def test_reply_to_an_empty_subject(self):
        self.assertEqual(reply_subject(""), "Re:")
        self.assertEqual(reply_subject(None), "Re:")

    def test_forward_prefixes_once_whatever_the_case(self):
        self.assertEqual(forward_subject("Lunch"), "Fwd: Lunch")
        self.assertEqual(forward_subject("Fwd: Lunch"), "Fwd: Lunch")
        self.assertEqual(forward_subject("FWD: Lunch"), "FWD: Lunch")
        self.assertEqual(forward_subject("Fw: Lunch"), "Fw: Lunch")
        self.assertEqual(forward_subject(""), "Fwd:")


class ComposeFixtures(TestCase):
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

    def tearDown(self):
        cache.clear()

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
            "snippet": "Are we still on for Friday?",
            "to_addresses": [
                {"name": "Me", "email": "me@example.com"},
                {"name": "Bob", "email": "bob@example.com"},
            ],
            "cc_addresses": [{"name": "Carol", "email": "carol@example.com"}],
        }
        return MailMessage.objects.create(**{**defaults, **kwargs})


class BuildReplyRecipientsTests(ComposeFixtures):
    def test_reply_targets_the_sender_only(self):
        reply = build_reply(self._message(), self.user)
        self.assertEqual(reply.to, ["alice@example.com"])
        self.assertEqual(reply.cc, [])
        self.assertEqual(reply.subject, "Re: Quarterly review")
        self.assertEqual(reply.account, self.account)

    def test_reply_all_keeps_everyone_but_the_account(self):
        reply = build_reply(self._message(), self.user, reply_all=True)
        self.assertEqual(reply.to, ["alice@example.com", "bob@example.com"])
        self.assertEqual(reply.cc, ["carol@example.com"])

    def test_reply_all_drops_own_address_whatever_its_case(self):
        message = self._message(
            to_addresses=[{"email": "ME@Example.com"}],
            cc_addresses=[{"email": "Me@example.COM"}],
        )
        reply = build_reply(message, self.user, reply_all=True)
        self.assertEqual(reply.to, ["alice@example.com"])
        self.assertEqual(reply.cc, [])

    def test_reply_all_never_repeats_an_address_across_to_and_cc(self):
        message = self._message(
            to_addresses=[{"email": "bob@example.com"}],
            cc_addresses=[{"email": "BOB@example.com"}],
        )
        reply = build_reply(message, self.user, reply_all=True)
        self.assertEqual(reply.to, ["alice@example.com", "bob@example.com"])
        self.assertEqual(reply.cc, [])

    def test_reply_to_a_sent_message_writes_to_its_recipients_again(self):
        message = self._message(
            from_email="me@example.com",
            from_name="Me",
            to_addresses=[{"email": "bob@example.com"}],
        )
        reply = build_reply(message, self.user)
        self.assertEqual(reply.to, ["bob@example.com"])
        self.assertEqual(reply.cc, [])

    def test_reply_all_to_a_sent_message_promotes_a_cc_when_to_is_empty(self):
        message = self._message(
            from_email="me@example.com",
            to_addresses=[{"email": "me@example.com"}],
            cc_addresses=[{"email": "carol@example.com"}, {"email": "dan@example.com"}],
        )
        reply = build_reply(message, self.user, reply_all=True)
        self.assertEqual(reply.to, ["carol@example.com"])
        self.assertEqual(reply.cc, ["dan@example.com"])

    def test_reply_to_a_message_sent_only_to_oneself_has_no_recipient(self):
        message = self._message(
            from_email="me@example.com",
            to_addresses=[{"email": "me@example.com"}],
        )
        self.assertEqual(build_reply(message, self.user).to, [])

    def test_reply_threads_under_its_parent(self):
        message = self._message()
        self.assertEqual(build_reply(message, self.user).reply_message_id, message.uuid)


class BuildReplyBodyTests(ComposeFixtures):
    def test_body_then_quote_in_the_user_timezone(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        reply = build_reply(self._message(), self.user, body="Yes, Friday works.")
        # 09:15 UTC is 10:15 in Paris in March.
        self.assertEqual(
            reply.body_text,
            "Yes, Friday works.\n\n---\n"
            "On Wednesday, March 4, 2026, 10:15, Alice wrote:\n"
            "> Are we still on for Friday?",
        )

    def test_quote_header_is_in_the_user_language(self):
        set_setting(self.user, "core", "language", "fr")
        reply = build_reply(self._message(), self.user)
        self.assertIn(
            "Le mercredi 4 mars 2026 à 09:15, Alice a écrit :", reply.body_text
        )

    def test_the_whole_original_is_quoted(self):
        lines = [f"line {n}" for n in range(1000)]
        reply = build_reply(self._message(body_text="\n".join(lines)), self.user)
        self.assertTrue(reply.body_text.endswith("> line 998\n> line 999"))

    def test_quote_falls_back_to_the_snippet(self):
        reply = build_reply(self._message(body_text="", snippet="Short"), self.user)
        self.assertTrue(reply.body_text.endswith("\n> Short"))

    def test_signature_sits_between_the_body_and_the_quote(self):
        self.account.signature = "Alice's colleague\n"
        self.account.save(update_fields=["signature"])
        reply = build_reply(self._message(), self.user, body="Yes.")
        self.assertEqual(reply.signature_block, "\n-- \nAlice's colleague\n")
        self.assertTrue(
            reply.body_text.startswith("Yes.\n-- \nAlice's colleague\n\n\n---\nOn ")
        )

    def test_an_empty_reply_opens_on_its_signature(self):
        self.account.signature = "Me"
        self.account.save(update_fields=["signature"])
        reply = build_reply(self._message(), self.user)
        self.assertTrue(reply.body_text.startswith("\n-- \nMe\n\n\n---\n"))

    def test_no_signature_no_block(self):
        self.assertEqual(signature_block("  "), "")
        reply = build_reply(self._message(), self.user)
        self.assertEqual(reply.signature_block, "")
        self.assertTrue(reply.body_text.startswith("\n\n---\nOn "))


class BuildForwardTests(ComposeFixtures):
    def _attachment(self, message, filename, **kwargs):
        return MailAttachment.objects.create(
            message=message,
            filename=filename,
            content_type=kwargs.pop("content_type", "application/pdf"),
            size=kwargs.pop("size", 10),
            **kwargs,
        )

    def test_forward_has_no_recipient_and_no_parent(self):
        forward = build_forward(self._message(), self.user)
        self.assertEqual(forward.to, [])
        self.assertEqual(forward.cc, [])
        self.assertIsNone(forward.reply_message_id)
        self.assertEqual(forward.subject, "Fwd: Quarterly review")

    def test_forward_carries_the_whole_text_after_the_signature(self):
        self.account.signature = "Me"
        self.account.save(update_fields=["signature"])
        forward = build_forward(self._message(body_text="a\n" * 3000), self.user)
        self.assertEqual(
            forward.body_text,
            "\n-- \nMe\n\n\n---\nForwarded message from Alice:\n\n" + "a\n" * 3000,
        )

    def test_forward_keeps_the_attachments_but_the_pictures_the_body_shows(self):
        message = self._message(
            body_html='<p>Numbers</p><img src="cid:logo@x">',
            has_attachments=True,
        )
        report = self._attachment(message, "report.pdf")
        self._attachment(
            message, "logo.png", content_type="image/png", content_id="logo@x"
        )
        forward = build_forward(message, self.user)
        self.assertEqual([a.uuid for a in forward.attachments], [report.uuid])
