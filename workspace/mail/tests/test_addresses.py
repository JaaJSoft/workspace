"""Tests for the flat address columns replacing the from_address JSON.

The sender lives in the plain ``from_email`` / ``from_name`` columns
(written at parse time), and ``recipients_text`` is a search-only
flattening of the to/cc JSON lists maintained by ``save()``. Search
paths must filter on these columns instead of casting JSON per row.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.mail.ai_tools import MailToolProvider, SearchEmailsParams
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.search import search_contacts, search_mail
from workspace.mail.services.addresses import derive_recipients_text, sender_columns

User = get_user_model()


class SenderColumnsTests(TestCase):
    """Pure derivation of the sender columns from a {name, email} dict."""

    def test_name_and_email_extracted(self):
        cols = sender_columns({"name": "Alice Wonder", "email": "alice@example.com"})
        self.assertEqual(cols["from_email"], "alice@example.com")
        self.assertEqual(cols["from_name"], "Alice Wonder")

    def test_non_dict_input_yields_empty_columns(self):
        cols = sender_columns("garbage")
        self.assertEqual(cols["from_email"], "")
        self.assertEqual(cols["from_name"], "")

    def test_values_truncated_to_column_limits(self):
        cols = sender_columns({"name": "n" * 300, "email": "e" * 300})
        self.assertEqual(len(cols["from_email"]), 254)
        self.assertEqual(len(cols["from_name"]), 255)


class DeriveRecipientsTextTests(TestCase):
    """Pure flattening of the to/cc JSON lists."""

    def test_flattens_to_and_cc(self):
        text = derive_recipients_text(
            [{"name": "Bob", "email": "bob@example.com"}],
            [{"name": "", "email": "carol@example.com"}],
        )
        self.assertEqual(text, "Bob <bob@example.com>, carol@example.com")

    def test_non_list_inputs_are_ignored(self):
        self.assertEqual(derive_recipients_text(None, {"not": "a list"}), "")

    def test_entries_without_email_keep_the_name(self):
        text = derive_recipients_text([{"name": "Undisclosed", "email": ""}], [])
        self.assertEqual(text, "Undisclosed")


class MailFixtureMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username="addruser", email="addr@test.com", password="pass123"
        )
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

    def _create_message(self, imap_uid=1, **kwargs):
        defaults = {
            "from_name": "Alice Wonder",
            "from_email": "alice@example.com",
            "to_addresses": [{"name": "Me", "email": "me@example.com"}],
            "subject": "Quarterly report",
        }
        defaults.update(kwargs)
        return MailMessage.objects.create(
            account=self.account, folder=self.inbox, imap_uid=imap_uid, **defaults
        )


class MailMessageSaveDerivationTests(MailFixtureMixin, TestCase):
    """save() keeps recipients_text in sync with the to/cc JSON."""

    def test_create_populates_recipients_text(self):
        msg = self._create_message(
            cc_addresses=[{"name": "", "email": "cc@example.com"}]
        )
        msg.refresh_from_db()
        self.assertEqual(msg.recipients_text, "Me <me@example.com>, cc@example.com")

    def test_sender_columns_are_plain_fields(self):
        msg = self._create_message()
        msg.refresh_from_db()
        self.assertEqual(msg.from_email, "alice@example.com")
        self.assertEqual(msg.from_name, "Alice Wonder")

    def test_save_recomputes_recipients_after_json_change(self):
        msg = self._create_message()
        msg.to_addresses = [{"name": "Zed", "email": "zed@example.com"}]
        msg.save()
        msg.refresh_from_db()
        self.assertEqual(msg.recipients_text, "Zed <zed@example.com>")


class SenderSearchUsesFlatColumnsTests(MailFixtureMixin, APITestCase):
    """Every sender-search path must match through from_email/from_name."""

    def setUp(self):
        super().setUp()
        self.msg = self._create_message()
        self.client.force_authenticate(user=self.user)

    def test_message_list_search_matches_sender_name(self):
        resp = self.client.get(
            "/api/v1/mail/messages",
            {"folder": str(self.inbox.uuid), "search": "wonder"},
        )
        self.assertEqual(resp.status_code, 200)
        uuids = [m["uuid"] for m in resp.data["results"]]
        self.assertIn(str(self.msg.uuid), uuids)

    def test_search_mail_matches_sender_email(self):
        results = search_mail("alice@example.com", self.user, 10)
        self.assertEqual([r.uuid for r in results], [str(self.msg.uuid)])

    def test_ai_search_emails_matches_sender_name(self):
        out = MailToolProvider().search_emails(
            SearchEmailsParams(query="wonder"), self.user, None, None, {}
        )
        self.assertIn(str(self.msg.uuid), out)
        self.assertIn("Alice Wonder", json.loads(out)[0]["from"])

    def test_contact_autocomplete_matches_sender(self):
        resp = self.client.get("/api/v1/mail/contacts/autocomplete", {"q": "alice"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [(c["name"], c["email"]) for c in resp.data],
            [("Alice Wonder", "alice@example.com")],
        )

    def test_search_contacts_matches_sender(self):
        results = search_contacts("wonder", self.user, 10)
        self.assertEqual(len(results), 1)
        self.assertIn("alice@example.com", results[0].matched_value)


class RecipientSearchUsesFlatColumnTests(MailFixtureMixin, APITestCase):
    """The recipient prefilter must read recipients_text, not the JSON.

    We blank recipients_text through queryset.update() (bypasses save(), so
    the JSON keeps the recipient): a query matching only the JSON must no
    longer return the contact once the WHERE clause reads the flat column.
    """

    def setUp(self):
        super().setUp()
        self.msg = self._create_message(
            to_addresses=[{"name": "Bob Builder", "email": "bob@example.com"}]
        )
        MailMessage.objects.filter(pk=self.msg.pk).update(recipients_text="")
        self.client.force_authenticate(user=self.user)

    def test_autocomplete_prefilter_reads_recipients_text(self):
        resp = self.client.get("/api/v1/mail/contacts/autocomplete", {"q": "bob"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_search_contacts_prefilter_reads_recipients_text(self):
        self.assertEqual(search_contacts("bob", self.user, 10), [])
