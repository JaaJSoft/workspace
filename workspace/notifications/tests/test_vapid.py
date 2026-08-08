"""VAPID key loading and request signing, exercised with real key material.

Every other push test mocks ``tasks.webpush`` wholesale, so nothing pins down
what the signing layer actually does with a configured key. These tests run the
real py_vapid/pywebpush code and only stub the outbound HTTP call.
"""

import base64
import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from workspace.notifications.models import Notification, PushSubscription
from workspace.notifications.services.vapid import VapidKeyError, load_vapid_key
from workspace.notifications.tests.vapid_fixtures import (
    b64,
    generate_keypair,
    subscription_keys,
)

User = get_user_model()


def _public_of(vapid):
    return b64(
        vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )


def _jwt_claims(authorization_header):
    """Decode the claims out of a VAPID ``Authorization`` header."""
    token = authorization_header.split(" ", 1)[1].split(",")[0]
    if token.startswith("t="):
        token = token[2:]
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


class LoadVapidKeyTests(SimpleTestCase):
    """The loader must accept every format the generator or a deployment
    might plausibly supply, and always yield the same signing key."""

    def test_accepts_pem_der_and_raw_forms_of_the_same_key(self):
        pem, der_b64, raw_b64, public_b64 = generate_keypair()
        for label, value in (("pem", pem), ("der", der_b64), ("raw", raw_b64)):
            with self.subTest(form=label):
                self.assertEqual(_public_of(load_vapid_key(value)), public_b64)

    def test_accepts_pem_with_surrounding_whitespace(self):
        """Multi-line env vars routinely pick up a leading or trailing
        newline; py_vapid's own from_pem rejects a leading one."""
        pem, _, _, public_b64 = generate_keypair()
        for label, value in (
            ("leading", "\n" + pem),
            ("trailing", pem + "\n"),
            ("indented", "  " + pem + "  "),
            ("crlf", pem.replace("\n", "\r\n")),
        ):
            with self.subTest(whitespace=label):
                self.assertEqual(_public_of(load_vapid_key(value)), public_b64)

    def test_rejects_garbage_with_a_diagnostic_error(self):
        for value in ("", "   ", "not-a-key", "-----BEGIN PRIVATE KEY-----\nzz\n"):
            with self.subTest(value=value):
                with self.assertRaises(VapidKeyError):
                    load_vapid_key(value)


class GenerateVapidKeysCommandTests(SimpleTestCase):
    """The printed pair must be usable exactly as printed: the private key
    loads, it derives the advertised public key, and both survive being pasted
    into an env file unedited."""

    def _generated_keys(self):
        out = StringIO()
        call_command("generate_vapid_keys", stdout=out)
        keys = {}
        for line in out.getvalue().splitlines():
            if line.startswith("WEBPUSH_VAPID_"):
                name, _, value = line.partition("=")
                keys[name] = value
        return keys

    def test_generated_private_key_loads_and_matches_the_public_key(self):
        keys = self._generated_keys()
        self.assertIn("WEBPUSH_VAPID_PRIVATE_KEY", keys)
        self.assertIn("WEBPUSH_VAPID_PUBLIC_KEY", keys)

        vapid = load_vapid_key(keys["WEBPUSH_VAPID_PRIVATE_KEY"])
        self.assertEqual(_public_of(vapid), keys["WEBPUSH_VAPID_PUBLIC_KEY"])

    def test_keys_are_single_line_and_unquoted(self):
        """A multi-line or quoted value does not survive .env files and
        container env vars intact."""
        for name, value in self._generated_keys().items():
            with self.subTest(key=name):
                self.assertNotIn(" ", value)
                self.assertNotIn('"', value)
                self.assertTrue(value)

    def test_each_run_generates_a_distinct_pair(self):
        self.assertNotEqual(
            self._generated_keys()["WEBPUSH_VAPID_PRIVATE_KEY"],
            self._generated_keys()["WEBPUSH_VAPID_PRIVATE_KEY"],
        )


class PushSigningTests(TestCase):
    """End-to-end through send_push_notification with a real key, stubbing
    only the HTTP POST so the signing path runs for real."""

    def setUp(self):
        self.pem, _, _, self.public_b64 = generate_keypair()
        self.user = User.objects.create_user(
            username="vapiduser", email="vapid@test.com", password="pass123"
        )
        self.notif = Notification.objects.create(
            recipient=self.user, origin="test", title="Hello", body="World"
        )
        p256dh, auth = subscription_keys()
        self.sub = PushSubscription.objects.create(
            user=self.user,
            endpoint="https://fcm.googleapis.com/fcm/send/abc",
            p256dh=p256dh,
            auth=auth,
        )

    def _run_task(self):
        """Run the task, returning the captured outbound requests."""
        from workspace.notifications.tasks import send_push_notification

        captured = []

        def fake_post(endpoint, **kwargs):
            captured.append((endpoint, kwargs.get("headers") or {}))
            return SimpleNamespace(status_code=201, text="", headers={})

        with (
            patch("workspace.notifications.tasks.is_active", return_value=False),
            patch("pywebpush.requests.post", side_effect=fake_post),
        ):
            send_push_notification(str(self.notif.uuid))
        return captured

    @override_settings(
        WEBPUSH_VAPID_CLAIMS={"sub": "mailto:admin@example.com"},
    )
    def test_pem_private_key_produces_a_signed_request(self):
        """A PEM private key must sign.

        pywebpush hands a key given as a string to py_vapid's from_string,
        which base64-decodes the armor along with the key material and
        raises, so a configured PEM only works if the task parses it first.
        """
        with override_settings(WEBPUSH_VAPID_PRIVATE_KEY=self.pem):
            captured = self._run_task()

        self.assertEqual(len(captured), 1, "no push request was sent")
        _, headers = captured[0]
        self.assertIn("Authorization", headers)
        claims = _jwt_claims(headers["Authorization"])
        self.assertEqual(claims["sub"], "mailto:admin@example.com")
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")

    @override_settings(
        WEBPUSH_VAPID_CLAIMS={"sub": "mailto:admin@example.com"},
    )
    def test_audience_matches_each_push_service(self):
        """pywebpush fills a missing `aud` into the claims dict it is given
        and never revises it. Sharing the settings dict across calls would
        pin every later push to the first endpoint's origin, which the push
        service rejects."""
        p256dh, auth = subscription_keys()
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://updates.push.services.mozilla.com/wpush/v2/xyz",
            p256dh=p256dh,
            auth=auth,
        )

        with override_settings(WEBPUSH_VAPID_PRIVATE_KEY=self.pem):
            captured = self._run_task()

        self.assertEqual(len(captured), 2)
        for endpoint, headers in captured:
            expected = urlparse(endpoint)
            with self.subTest(endpoint=endpoint):
                claims = _jwt_claims(headers["Authorization"])
                self.assertEqual(
                    claims["aud"], f"{expected.scheme}://{expected.netloc}"
                )

    @override_settings(
        WEBPUSH_VAPID_CLAIMS={"sub": "mailto:admin@example.com"},
    )
    def test_configured_claims_are_not_mutated(self):
        """The settings dict is process-global; letting pywebpush write `aud`
        and `exp` into it leaks the first push's audience into every later one."""
        with override_settings(WEBPUSH_VAPID_PRIVATE_KEY=self.pem):
            self._run_task()

        self.assertEqual(
            settings.WEBPUSH_VAPID_CLAIMS, {"sub": "mailto:admin@example.com"}
        )

    @override_settings(
        WEBPUSH_VAPID_PRIVATE_KEY="not-a-valid-key",
        WEBPUSH_VAPID_CLAIMS={"sub": "mailto:admin@example.com"},
    )
    def test_unparseable_key_sends_nothing_and_does_not_raise(self):
        captured = self._run_task()
        self.assertEqual(captured, [])
