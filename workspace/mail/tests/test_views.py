import orjson
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from workspace.mail.models import MailAccount
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
