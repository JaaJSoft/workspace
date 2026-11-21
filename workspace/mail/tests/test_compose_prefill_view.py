import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.ai_tools import MailToolProvider, ReplyToEmailParams
from workspace.mail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailMessage,
)
from workspace.mail.tests.test_ai_tools_compose import plain_body

User = get_user_model()


class ComposePrefillViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="composer", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="me@example.com",
            signature="Me",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account, name="INBOX", folder_type="inbox"
        )
        self.drafts = MailFolder.objects.create(
            account=self.account, name="Drafts", folder_type="drafts"
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=1,
            subject="Numbers",
            from_email="alice@example.com",
            from_name="Alice",
            date=datetime(2026, 3, 4, 9, 15, tzinfo=UTC),
            body_text="See attached.",
            to_addresses=[
                {"name": "Me", "email": "me@example.com"},
                {"name": "Bob", "email": "bob@example.com"},
            ],
            cc_addresses=[{"name": "Carol", "email": "carol@example.com"}],
        )
        self.client = APIClient()
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _get(self, message_uuid, mode):
        return self.client.get(
            f"/api/v1/mail/messages/{message_uuid}/compose", {"mode": mode}
        )

    def test_reply(self):
        resp = self._get(self.message.uuid, "reply")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["account_id"], str(self.account.uuid))
        self.assertEqual(data["to"], ["alice@example.com"])
        self.assertEqual(data["cc"], [])
        self.assertEqual(data["subject"], "Re: Numbers")
        self.assertEqual(data["signature_block"], "\n-- \nMe\n")
        self.assertEqual(
            data["body_text"],
            "\n-- \nMe\n\n\n---\nOn Wednesday, March 4, 2026, 09:15, Alice wrote:\n"
            "> See attached.",
        )
        self.assertEqual(data["reply_message_id"], str(self.message.uuid))
        self.assertEqual(data["attachments"], [])

    def test_reply_all_keeps_the_cc_recipients(self):
        data = self._get(self.message.uuid, "reply_all").json()
        self.assertEqual(data["to"], ["alice@example.com", "bob@example.com"])
        self.assertEqual(data["cc"], ["carol@example.com"])

    def test_forward_lists_the_attachments(self):
        report = MailAttachment.objects.create(
            message=self.message, filename="report.pdf", size=1200
        )
        data = self._get(self.message.uuid, "forward").json()
        self.assertEqual(data["to"], [])
        self.assertEqual(data["subject"], "Fwd: Numbers")
        self.assertIsNone(data["reply_message_id"])
        self.assertEqual(
            data["attachments"],
            [{"uuid": str(report.uuid), "filename": "report.pdf", "size": 1200}],
        )

    def test_unknown_mode_is_refused(self):
        self.assertEqual(self._get(self.message.uuid, "redirect").status_code, 400)
        self.assertEqual(
            self.client.get(
                f"/api/v1/mail/messages/{self.message.uuid}/compose"
            ).status_code,
            400,
        )

    def test_another_users_message_answers_like_a_missing_one(self):
        other = User.objects.create_user(username="other", password="pw")
        self.client.force_login(other)
        hidden = self._get(self.message.uuid, "reply")
        missing = self._get(uuid.uuid4(), "reply")
        self.assertEqual(
            (hidden.status_code, hidden.content),
            (missing.status_code, missing.content),
        )
        self.assertEqual(hidden.status_code, 404)

    def test_a_deleted_message_is_not_found(self):
        self.message.deleted_at = datetime(2026, 3, 5, tzinfo=UTC)
        self.message.save(update_fields=["deleted_at"])
        self.assertEqual(self._get(self.message.uuid, "reply").status_code, 404)

    def test_the_composer_and_the_assistant_answer_alike(self):
        composer = self._get(self.message.uuid, "reply_all").json()

        draft = MailMessage.objects.create(
            account=self.account, folder=self.drafts, imap_uid=99, is_draft=True
        )
        with patch(
            "workspace.mail.services.imap_messages.save_draft", return_value=draft
        ) as save:
            MailToolProvider().reply_to_email(
                ReplyToEmailParams(
                    uuid=self.message.uuid, body="Thanks.", reply_all=True
                ),
                user=self.user,
                bot=None,
                conversation_id=None,
                context={},
            )
        raw = save.call_args.args[1]

        self.assertIn(b"To: alice@example.com, bob@example.com", raw)
        self.assertIn(b"Cc: carol@example.com", raw)
        self.assertIn(f"Subject: {composer['subject']}".encode(), raw)
        self.assertEqual(
            plain_body(raw).replace("\r\n", "\n").rstrip("\n"),
            "Thanks." + composer["body_text"],
        )
