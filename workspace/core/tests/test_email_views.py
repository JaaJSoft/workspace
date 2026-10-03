"""The bounce webhook, the unsubscribe page and the admin's test mail."""

import base64
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.mail.backends import locmem
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.core.models import EmailDelivery, EmailSuppression
from workspace.core.services.admin_dashboard import ADMIN_TEST_FEATURE, email_panel
from workspace.core.services.email import _unsubscribe_url, send_email

User = get_user_model()

SECRET = "relay:bounce-secret"
POSTMARK = "/api/v1/email/postmark/tracking"

ENABLED = override_settings(
    EMAIL_ENABLED=True,
    EMAIL_BASE_URL="https://ws.example.com",
    DEFAULT_FROM_EMAIL="Workspace <noreply@example.com>",
)


class ProviderBackend(locmem.EmailBackend):
    """What an anymail backend leaves on a message it sent: the id the
    provider gave it."""

    def send_messages(self, messages):
        for message in messages:
            message.anymail_status = SimpleNamespace(message_id="pm-42")
        return super().send_messages(messages)


def _postmark_bounce(address, bounce_type="HardBounce", message_id="pm-42"):
    return {
        "RecordType": "Bounce",
        "Type": bounce_type,
        "MessageID": message_id,
        "Email": address,
        "Description": "The server was unable to deliver your message",
        "Details": "550 5.1.1 user unknown",
        "BouncedAt": "2026-09-30T10:00:00Z",
    }


@override_settings(ANYMAIL={"WEBHOOK_SECRET": SECRET})
class ProviderWebhookTests(TestCase):
    """Bounces reported through anymail's webhooks, Postmark standing in for
    every provider: the parsing is anymail's, the suppression is ours."""

    def post(self, payload, *, auth=SECRET):
        headers = {}
        if auth:
            credentials = base64.b64encode(auth.encode()).decode()
            headers["HTTP_AUTHORIZATION"] = f"Basic {credentials}"
        return self.client.post(
            POSTMARK, payload, content_type="application/json", **headers
        )

    def test_every_provider_has_a_route_without_trailing_slash(self):
        for provider in ("amazon_ses", "mailgun", "postmark", "sendgrid", "brevo"):
            with self.subTest(provider=provider):
                self.assertEqual(
                    reverse(f"anymail-{provider}_tracking_webhook"),
                    f"/api/v1/email/{provider}/tracking",
                )

    @override_settings(ANYMAIL={})
    def test_off_without_a_secret(self):
        # Anymail would serve it open, with a warning.
        response = self.post(_postmark_bounce("a@example.com"), auth=None)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(EmailSuppression.objects.exists())

    def test_refuses_a_wrong_secret(self):
        for auth in (None, "relay:wrong"):
            with self.subTest(auth=auth):
                response = self.post(_postmark_bounce("a@example.com"), auth=auth)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(EmailSuppression.objects.exists())

    def test_a_hard_bounce_suppresses_the_address(self):
        response = self.post(_postmark_bounce("A@Example.com"))

        self.assertEqual(response.status_code, 200)
        suppression = EmailSuppression.objects.get()
        self.assertEqual(suppression.address, "a@example.com")
        self.assertEqual(suppression.feature, "")
        self.assertEqual(suppression.reason, EmailSuppression.Reason.HARD_BOUNCE)
        self.assertIn("Postmark", suppression.detail)

    def test_a_complaint_suppresses_the_address(self):
        payload = {
            "RecordType": "SpamComplaint",
            "Type": "SpamComplaint",
            "MessageID": "pm-42",
            "Email": "a@example.com",
            "BouncedAt": "2026-09-30T10:00:00Z",
        }
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(
            EmailSuppression.objects.get().reason, EmailSuppression.Reason.COMPLAINT
        )

    def test_temporary_failures_suppress_nothing(self):
        # A full mailbox, a DNS hiccup, a virus in the message: none of them
        # says the address is dead.
        for bounce_type in ("SoftBounce", "Transient", "DnsError", "VirusNotification"):
            with self.subTest(bounce_type=bounce_type):
                response = self.post(_postmark_bounce("a@example.com", bounce_type))
                self.assertEqual(response.status_code, 200)
        self.assertFalse(EmailSuppression.objects.exists())

    def test_an_address_the_provider_refuses_is_suppressed(self):
        self.post(_postmark_bounce("a@example.com", "BadEmailAddress"))
        self.assertEqual(
            EmailSuppression.objects.get().reason, EmailSuppression.Reason.HARD_BOUNCE
        )

    @ENABLED
    @override_settings(
        EMAIL_BACKEND="workspace.core.tests.test_email_views.ProviderBackend"
    )
    def test_marks_the_bounced_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            delivery = send_email(
                "a@example.com",
                "core/email/admin_test",
                feature="test",
                context={"requested_by": User(username="a")},
            )
        delivery.refresh_from_db()
        self.assertEqual(delivery.provider_message_id, "pm-42")

        self.post(_postmark_bounce("a@example.com", message_id="pm-42"))

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.BOUNCED)
        self.assertIn("user unknown", delivery.error)


@ENABLED
class UnsubscribeTests(TestCase):
    def setUp(self):
        self.url = _unsubscribe_url("a@example.com", "digest").removeprefix(
            "https://ws.example.com"
        )

    def test_get_only_asks(self):
        # Link scanners fetch every URL in a mail.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "a@example.com")
        self.assertContains(response, 'method="post"')
        self.assertFalse(EmailSuppression.objects.exists())

    def test_one_click_post_unsubscribes_from_the_feature(self):
        # What a mail client sends for RFC 8058: no session, no CSRF token.
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(
            self.url,
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are unsubscribed")
        suppression = EmailSuppression.objects.get()
        self.assertEqual(suppression.address, "a@example.com")
        self.assertEqual(suppression.feature, "digest")
        self.assertEqual(suppression.reason, EmailSuppression.Reason.UNSUBSCRIBED)

    def test_unsubscribing_twice_is_harmless(self):
        self.client.post(self.url)
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertEqual(EmailSuppression.objects.count(), 1)

    def test_a_forged_token_is_not_found(self):
        response = self.client.post(reverse("email-unsubscribe", args=["forged"]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(EmailSuppression.objects.exists())


class AdminTestEmailTests(TestCase):
    url = "/admin/email/test"

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_staff_only(self):
        self.client.logout()
        user = User.objects.create_user(username="joe", password="pw")
        self.client.force_login(user)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response["Location"])
        self.assertFalse(EmailDelivery.objects.exists())

    def test_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_disabled_explains_itself(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("admin:index"))
        self.assertIn("not configured", self.messages(response)[0])
        self.assertFalse(EmailDelivery.objects.exists())

    @ENABLED
    def test_an_admin_without_an_address_is_told_so(self):
        self.admin.email = ""
        self.admin.save(update_fields=["email"])
        response = self.client.post(self.url)
        self.assertIn("no email address", self.messages(response)[0])
        self.assertFalse(EmailDelivery.objects.exists())

    @ENABLED
    def test_sends_through_the_normal_path(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)

        self.assertRedirects(response, reverse("admin:index"))
        self.assertIn("queued for root@example.com", self.messages(response)[0])
        self.assertEqual(mail.outbox[0].to, ["root@example.com"])
        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.feature, ADMIN_TEST_FEATURE)
        self.assertEqual(delivery.user, self.admin)
        self.assertEqual(delivery.status, EmailDelivery.Status.SENT)

    @ENABLED
    def test_the_dashboard_shows_the_outcome(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url)

        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Send a test email")
        self.assertContains(response, "Last test to root@example.com: sent")

    def test_the_dashboard_disables_the_button_when_it_cannot_send(self):
        response = self.client.get(reverse("admin:index"))
        panel = response.context["email_panel"]
        self.assertFalse(panel["enabled"])
        self.assertIn("not configured", panel["unavailable"])
        self.assertContains(response, "disabled")

    @ENABLED
    def test_the_panel_shows_only_the_viewers_own_tests(self):
        other = User.objects.create_superuser(
            username="other", email="other@example.com", password="pw"
        )
        EmailDelivery.objects.create(
            to_address="other@example.com",
            user=other,
            feature=ADMIN_TEST_FEATURE,
            template="core/email/admin_test",
            subject="s",
        )
        request = self.client.get(reverse("admin:index")).wsgi_request
        self.assertIsNone(email_panel(request)["last_test"])
