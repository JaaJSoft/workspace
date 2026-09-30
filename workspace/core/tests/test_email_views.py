"""The bounce webhook, the unsubscribe page and the admin's test mail."""

import base64

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from workspace.core.models import EmailDelivery, EmailSuppression
from workspace.core.services.admin_dashboard import ADMIN_TEST_FEATURE, email_panel
from workspace.core.services.email import _unsubscribe_url, send_email

User = get_user_model()

TOKEN = "bounce-secret"
BOUNCES = "/api/v1/email/bounces"

ENABLED = override_settings(
    EMAIL_ENABLED=True,
    EMAIL_BASE_URL="https://ws.example.com",
    DEFAULT_FROM_EMAIL="Workspace <noreply@example.com>",
)


@override_settings(EMAIL_BOUNCE_WEBHOOK_TOKEN=TOKEN)
class BounceWebhookTests(TestCase):
    def tearDown(self):
        # The IP throttle counts in the cache.
        cache.clear()

    def post(self, payload, *, auth=f"Bearer {TOKEN}"):
        headers = {"HTTP_AUTHORIZATION": auth} if auth else {}
        return self.client.post(
            BOUNCES, payload, content_type="application/json", **headers
        )

    @override_settings(EMAIL_BOUNCE_WEBHOOK_TOKEN="")
    def test_off_without_a_token(self):
        response = self.post({"address": "a@example.com", "type": "hard_bounce"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(EmailSuppression.objects.exists())

    def test_refuses_a_missing_or_wrong_secret(self):
        for auth in (None, "Bearer wrong", "Token bounce-secret", "Basic !!"):
            with self.subTest(auth=auth):
                response = self.post(
                    {"address": "a@example.com", "type": "hard_bounce"}, auth=auth
                )
                self.assertEqual(response.status_code, 401)
        self.assertFalse(EmailSuppression.objects.exists())

    def test_a_hard_bounce_suppresses_the_address(self):
        response = self.post(
            {"address": "A@Example.com", "type": "hard_bounce", "detail": "5.1.1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"suppressed": 1, "ignored": 0})
        suppression = EmailSuppression.objects.get()
        self.assertEqual(suppression.address, "a@example.com")
        self.assertEqual(suppression.feature, "")
        self.assertEqual(suppression.reason, EmailSuppression.Reason.HARD_BOUNCE)

    def test_accepts_basic_auth(self):
        credentials = base64.b64encode(f"relay:{TOKEN}".encode()).decode()
        response = self.post(
            {"address": "a@example.com", "type": "complaint"},
            auth=f"Basic {credentials}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            EmailSuppression.objects.get().reason, EmailSuppression.Reason.COMPLAINT
        )

    def test_takes_a_batch_and_ignores_soft_bounces(self):
        response = self.post(
            [
                {"address": "a@example.com", "type": "hard_bounce"},
                {"address": "b@example.com", "type": "soft_bounce"},
                {"address": "c@example.com", "type": "complaint"},
            ]
        )
        self.assertEqual(response.json(), {"suppressed": 2, "ignored": 1})
        self.assertEqual(
            set(EmailSuppression.objects.values_list("address", flat=True)),
            {"a@example.com", "c@example.com"},
        )

    def test_refuses_an_invalid_event(self):
        for payload in (
            {"address": "not an address", "type": "hard_bounce"},
            {"address": "a@example.com", "type": "exploded"},
            {"type": "hard_bounce"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(EmailSuppression.objects.exists())

    @ENABLED
    def test_marks_the_bounced_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            delivery = send_email(
                "a@example.com",
                "core/email/admin_test",
                feature="test",
                context={"requested_by": User(username="a")},
            )
        self.post(
            {
                "address": "a@example.com",
                "type": "hard_bounce",
                "message_id": delivery.message_id,
            }
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.BOUNCED)


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
