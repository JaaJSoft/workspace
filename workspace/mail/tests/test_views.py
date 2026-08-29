import orjson
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
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


class MailIndexSidebarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sidebar", password="pw")
        self.client.force_login(self.user)

    def test_renders_the_accounts_menu(self):
        resp = self.client.get(reverse("mail_ui:index"))

        self.assertEqual(resp.status_code, 200)
        # The sidebar button targets the panel by id through aria-controls.
        self.assertContains(resp, 'aria-controls="accounts-menu"')
        self.assertContains(resp, 'id="accounts-menu"')
        self.assertContains(resp, "Add account")


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
