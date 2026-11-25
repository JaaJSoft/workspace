from unittest.mock import patch

import orjson
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import (
    MailAccount,
    MailAttachment,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)
from workspace.users.services.settings import set_setting

User = get_user_model()


class MailIndexAIFeaturesContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mailai", password="pass123")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_mail_ai_features_default_all_true(self):
        resp = self.client.get(reverse("mail_ui:index"))
        self.assertEqual(
            resp.context["mail_ai_features"],
            {"classify": True, "extract": True, "manual": True},
        )

    def test_mail_ai_features_reflect_stored_setting(self):
        set_setting(self.user, "mail", "ai_classify", False)
        resp = self.client.get(reverse("mail_ui:index"))
        self.assertFalse(resp.context["mail_ai_features"]["classify"])
        self.assertTrue(resp.context["mail_ai_features"]["extract"])
        self.assertTrue(resp.context["mail_ai_features"]["manual"])


class MailIndexPreferencesTests(TestCase):
    """The page carries the preferences, so nothing fetches them on load."""

    def setUp(self):
        self.user = User.objects.create_user(username="mailprefs", password="pass")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _embedded(self):
        resp = self.client.get(reverse("mail_ui:index"))
        return resp.context["mail_prefs"], resp.content.decode()

    def test_a_user_on_the_defaults_gets_an_empty_object(self):
        prefs, html = self._embedded()
        self.assertEqual(prefs, {})
        self.assertIn('id="mail-prefs-data"', html)

    def test_stored_preferences_are_embedded(self):
        set_setting(self.user, "mail", "preferences", {"confirmBeforeDelete": False})
        prefs, _ = self._embedded()
        self.assertEqual(prefs, {"confirmBeforeDelete": False})


class MailAccountSignatureApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sig-api", password="x")
        self.client.force_authenticate(self.user)
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="api@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="api@example.com",
        )
        self.url = f"/api/v1/mail/accounts/{self.account.uuid}"

    def test_patch_sets_signature_and_get_returns_it(self):
        resp = self.client.patch(
            self.url,
            data=orjson.dumps({"signature": "Cordialement\nJean"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.account.refresh_from_db()
        self.assertEqual(self.account.signature, "Cordialement\nJean")

        # Read-side: signature present in serialized output.
        list_resp = self.client.get("/api/v1/mail/accounts")
        self.assertEqual(list_resp.status_code, 200)
        payload = list_resp.json()
        accounts = (
            payload if isinstance(payload, list) else payload.get("results", payload)
        )
        match = next(a for a in accounts if a["uuid"] == str(self.account.uuid))
        self.assertEqual(match["signature"], "Cordialement\nJean")

    def test_patch_without_signature_leaves_it_unchanged(self):
        self.account.signature = "keep me"
        self.account.save()
        resp = self.client.patch(
            self.url,
            data=orjson.dumps({"display_name": "New Name"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.account.refresh_from_db()
        self.assertEqual(self.account.signature, "keep me")

    def test_patch_keeps_sync_state_written_while_it_ran(self):
        """A sync landing between the PATCH's read and its write survives it."""
        from workspace.mail.views.accounts import MailAccountDetailView

        load = MailAccountDetailView._get_account

        def load_then_sync_lands(view, request, uuid):
            account = load(view, request, uuid)
            MailAccount.objects.filter(pk=account.pk).update(
                last_sync_error="Some folders failed to sync.",
                imap_delimiter=".",
            )
            return account

        with patch.object(MailAccountDetailView, "_get_account", load_then_sync_lands):
            resp = self.client.patch(
                self.url,
                data=orjson.dumps({"display_name": "New Name", "password": "pw"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.account.refresh_from_db()
        self.assertEqual(self.account.display_name, "New Name")
        self.assertEqual(self.account.get_password(), "pw")
        self.assertEqual(self.account.last_sync_error, "Some folders failed to sync.")
        self.assertEqual(self.account.imap_delimiter, ".")


class MergedFolderMessageListTests(APITestCase):
    """GET /api/v1/mail/messages?folder=<canonical> covers the whole group."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="mergelist", email="mergelist@test.com", password="pass123"
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
        self.in_canonical = MailMessage.objects.create(
            account=self.account,
            folder=self.trash,
            imap_uid=1,
            subject="from the canonical",
        )
        self.in_alias = MailMessage.objects.create(
            account=self.account,
            folder=self.corbeille,
            imap_uid=1,
            subject="from the alias",
        )
        self.client.force_authenticate(self.user)

    def test_canonical_lists_alias_messages(self):
        resp = self.client.get(
            "/api/v1/mail/messages", {"folder": str(self.trash.uuid)}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        subjects = {m["subject"] for m in resp.data["results"]}
        self.assertEqual(subjects, {"from the canonical", "from the alias"})
        self.assertEqual(resp.data["count"], 2)

    def test_alias_uuid_lists_the_same_group(self):
        """Pre-merge bookmarks and saved rules keep working."""
        resp = self.client.get(
            "/api/v1/mail/messages", {"folder": str(self.corbeille.uuid)}
        )
        self.assertEqual(resp.data["count"], 2)


class MessageListPayloadTests(APITestCase):
    """The list sends envelopes, so it reads neither bodies nor attachments."""

    def setUp(self):
        self.user = User.objects.create_user(username="listpayload", password="x")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account, name="INBOX", folder_type="inbox"
        )
        self.label = MailLabel.objects.create(
            account=self.account, name="Work", color="blue"
        )
        self.next_uid = 1
        self.client.force_authenticate(self.user)

    def _add_messages(self, count):
        for _ in range(count):
            message = MailMessage.objects.create(
                account=self.account,
                folder=self.inbox,
                imap_uid=self.next_uid,
                subject=f"Subject {self.next_uid}",
                from_name="Alice",
                from_email="alice@example.com",
                snippet="Hello",
                body_text="Hello " * 1000,
                body_html="<p>Hello</p>" * 1000,
                has_attachments=True,
            )
            MailMessageLabel.objects.create(message=message, label=self.label)
            MailAttachment.objects.create(message=message, filename="a.pdf")
            self.next_uid += 1

    def _list(self):
        with CaptureQueriesContext(connection) as queries:
            resp = self.client.get("/api/v1/mail/messages", {"inbox": "all"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data, [query["sql"] for query in queries.captured_queries]

    def test_serializes_the_envelope_of_each_message(self):
        self._add_messages(1)

        data, _ = self._list()

        (row,) = data["results"]
        self.assertEqual(row["subject"], "Subject 1")
        self.assertEqual(
            row["from_address"], {"name": "Alice", "email": "alice@example.com"}
        )
        self.assertEqual(row["snippet"], "Hello")
        self.assertTrue(row["has_attachments"])
        self.assertEqual(
            row["labels"],
            [{"uuid": str(self.label.uuid), "name": "Work", "color": "blue"}],
        )

    def test_query_count_does_not_grow_with_the_page(self):
        self._add_messages(1)
        self._list()  # warms the per-process caches the first request fills
        _, one = self._list()
        self._add_messages(4)
        _, five = self._list()

        self.assertEqual(len(one), len(five))

    def test_reads_neither_bodies_nor_attachments(self):
        self._add_messages(2)

        _, queries = self._list()

        message_table = MailMessage._meta.db_table
        page_query = next(
            sql
            for sql in queries
            if sql.startswith("SELECT")
            and f'FROM "{message_table}"' in sql
            and "LIMIT" in sql
        )
        for field in ("body_text", "body_html"):
            column = MailMessage._meta.get_field(field).column
            self.assertNotIn(f'"{column}"', page_query)
        self.assertNotIn(MailAttachment._meta.db_table, page_query)


class MergedFolderMarkReadTests(APITestCase):
    """POST /api/v1/mail/folders/<uuid>/mark-read reaches the alias."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="mergeread", email="mergeread@test.com", password="pass123"
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
            unread_count=1,
        )
        self.corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="trash",
            alias_of=self.trash,
            unread_count=1,
        )
        self.alias_message = MailMessage.objects.create(
            account=self.account,
            folder=self.corbeille,
            imap_uid=1,
            subject="unread in the alias",
            is_read=False,
        )
        self.client.force_authenticate(self.user)
        patcher = patch("workspace.mail.services.imap_messages.mark_uids_read")
        self.mark_uids_read = patcher.start()
        self.addCleanup(patcher.stop)

    def test_flags_each_mailbox_of_the_group_on_the_server(self):
        MailMessage.objects.create(
            account=self.account, folder=self.trash, imap_uid=9, is_read=False
        )

        self.client.post(f"/api/v1/mail/folders/{self.trash.uuid}/mark-read")

        flagged = {
            call.args[1].pk: sorted(call.args[2])
            for call in self.mark_uids_read.call_args_list
        }
        self.assertEqual(flagged, {self.trash.pk: [9], self.corbeille.pk: [1]})

    def test_marks_alias_messages_read(self):
        resp = self.client.post(f"/api/v1/mail/folders/{self.trash.uuid}/mark-read")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["updated"], 1)
        self.alias_message.refresh_from_db()
        self.assertTrue(self.alias_message.is_read)

    def test_zeroes_every_group_members_counter(self):
        self.client.post(f"/api/v1/mail/folders/{self.trash.uuid}/mark-read")
        self.trash.refresh_from_db()
        self.corbeille.refresh_from_db()
        self.assertEqual(self.trash.unread_count, 0)
        self.assertEqual(self.corbeille.unread_count, 0)
