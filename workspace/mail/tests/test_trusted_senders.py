"""Trusted senders: whose messages show their remote images without asking."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailTrustedSender,
)

User = get_user_model()
URL = "/api/v1/mail/trusted-senders"


def _message(owner, from_email, body_html=""):
    account = MailAccount.objects.create(
        owner=owner,
        email=f"{owner.username}@example.com",
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username=owner.username,
    )
    folder = MailFolder.objects.create(
        account=account, name="INBOX", display_name="Inbox", folder_type="inbox"
    )
    return MailMessage.objects.create(
        account=account,
        folder=folder,
        imap_uid=1,
        from_email=from_email,
        date=timezone.now(),
        body_html=body_html,
    )


class TrustedSenderEndpointTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.alice)

    def test_trusting_stores_the_address_lowercased_once(self):
        for _ in range(2):
            resp = self.client.post(
                URL, {"email": " News@Shop.Example "}, format="json"
            )
            self.assertEqual(resp.status_code, 204)

        self.assertEqual(
            list(MailTrustedSender.objects.values_list("owner", "email")),
            [(self.alice.pk, "news@shop.example")],
        )

    def test_distrusting_removes_only_the_callers_row(self):
        MailTrustedSender.objects.create(owner=self.alice, email="news@shop.example")
        MailTrustedSender.objects.create(owner=self.bob, email="news@shop.example")

        resp = self.client.delete(URL, {"email": "NEWS@shop.example"}, format="json")

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            list(MailTrustedSender.objects.values_list("owner", flat=True)),
            [self.bob.pk],
        )

    def test_an_empty_address_is_refused(self):
        resp = self.client.post(URL, {"email": ""}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(MailTrustedSender.objects.exists())

    def test_anonymous_callers_are_refused(self):
        resp = APIClient().post(URL, {"email": "a@b.example"}, format="json")

        self.assertIn(resp.status_code, (401, 403))
        self.assertFalse(MailTrustedSender.objects.exists())


class MessageDetailImageFieldsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.message = _message(
            self.alice,
            "News@Shop.Example",
            '<p>Sale</p><img src="https://cdn.shop.example/banner.png">',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.alice)

    def _get(self):
        return self.client.get(f"/api/v1/mail/messages/{self.message.uuid}").json()

    def test_remote_images_are_flagged_and_untrusted_by_default(self):
        data = self._get()

        self.assertTrue(data["has_remote_images"])
        self.assertFalse(data["sender_trusted"])

    def test_a_sender_trusted_by_the_owner_is_reported_whatever_the_case(self):
        MailTrustedSender.objects.create(owner=self.alice, email="news@shop.example")

        self.assertTrue(self._get()["sender_trusted"])

    def test_another_users_trust_does_not_apply(self):
        MailTrustedSender.objects.create(owner=self.bob, email="news@shop.example")

        self.assertFalse(self._get()["sender_trusted"])
