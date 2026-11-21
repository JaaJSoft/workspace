"""Push delivery through the URL guard: real pywebpush, real signing, the
real HTTP client, with only DNS and TCP replaced."""

import threading
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.common.tests.network import FakeNetwork, http_response
from workspace.notifications.models import Notification, PushSubscription
from workspace.notifications.tests.vapid_fixtures import (
    generate_keypair,
    subscription_keys,
)

User = get_user_model()

PUBLIC = "93.184.216.34"
ENDPOINT = "https://push.example.org/wpush/v2/abc"
CREATED = http_response(201, content_type="text/plain")


@override_settings(WEBPUSH_VAPID_CLAIMS={"sub": "mailto:admin@example.com"})
class GuardedPushDeliveryTests(TestCase):
    def setUp(self):
        pem, _, _, _ = generate_keypair()
        self.enterContext(override_settings(WEBPUSH_VAPID_PRIVATE_KEY=pem))
        self.enterContext(
            patch("workspace.notifications.tasks.is_active", return_value=False)
        )
        self.user = User.objects.create_user(username="pushnet", password="pass123")
        self.notif = Notification.objects.create(
            recipient=self.user, origin="test", title="Hello", body="World"
        )
        p256dh, auth = subscription_keys()
        self.sub = PushSubscription.objects.create(
            user=self.user, endpoint=ENDPOINT, p256dh=p256dh, auth=auth
        )

    def _send(self, answers, response=CREATED):
        from workspace.notifications.tasks import send_push_notification

        with FakeNetwork(answers, response=response) as net:
            send_push_notification(str(self.notif.uuid))
        return net

    def test_a_public_push_service_receives_the_signed_post(self):
        net = self._send({"push.example.org": [[PUBLIC]]})
        self.assertEqual(net.dialed, [(PUBLIC, 443)])
        self.assertTrue(bytes(net.sent).startswith(b"POST /wpush/v2/abc HTTP/1.1"))
        self.assertIn(b"\r\nauthorization: vapid ", bytes(net.sent).lower())

    def test_an_endpoint_resolving_to_a_private_address_is_never_dialed(self):
        # Registered before endpoints were checked, or rebound since.
        net = self._send({"push.example.org": [["10.0.0.7"]]})
        self.assertEqual(net.dialed, [])
        self.assertTrue(PushSubscription.objects.filter(pk=self.sub.pk).exists())

    def test_a_redirect_is_not_followed(self):
        redirect = (
            b"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://203.0.113.9/\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        net = self._send({"push.example.org": [[PUBLIC]]}, response=redirect)
        self.assertEqual(net.dialed, [(PUBLIC, 443)])

    @patch("workspace.notifications.tasks.PUSH_DEADLINE_SECONDS", 0.5)
    def test_a_trickling_push_service_leaves_no_send_running(self):
        # One byte every 50 ms: no per-read timeout fires, so without a
        # deadline on the whole POST the send would outlive the task.
        from workspace.notifications.tasks import send_push_notification

        started = time.monotonic()
        with (
            FakeNetwork({"push.example.org": [[PUBLIC]]}, trickle=0.05),
            self.assertLogs("workspace.notifications.tasks", "WARNING") as logs,
        ):
            send_push_notification(str(self.notif.uuid))
            for thread in threading.enumerate():
                if thread.name.startswith("webpush"):
                    thread.join(timeout=0.5)
                    self.assertFalse(thread.is_alive())

        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("timed out", "\n".join(logs.output))
        self.assertTrue(PushSubscription.objects.filter(pk=self.sub.pk).exists())
