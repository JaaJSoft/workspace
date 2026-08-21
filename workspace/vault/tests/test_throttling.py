"""Two traps live here, and both fail silently rather than loudly.

`AnonRateThrottle` returns no cache key once a request is authenticated, so a
per-IP limit built on it never fires on an authenticated endpoint - it reads
as a limit and is not one.

And a test cannot retune a rate with `override_settings` alone:
`SimpleRateThrottle.THROTTLE_RATES` is a class attribute read once when
`rest_framework.throttling` is imported, so the setting change never reaches
it and the test silently exercises the production rate.
"""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import SimpleRateThrottle

from workspace.vault import throttling
from workspace.vault.throttling import IpRateThrottle

User = get_user_model()

ENVELOPE_URL = "/api/v1/vault/account/envelope"


def with_rates(**rates):
    """Patch the throttle rate table where DRF actually reads it."""
    return patch.dict(SimpleRateThrottle.THROTTLE_RATES, rates)


class IpRateThrottleTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_the_cache_key_is_the_ip_and_not_the_user(self):
        class _Probe(IpRateThrottle):
            scope = "vault.account.envelope.ip"

        factory = APIRequestFactory()
        keys = []
        for username in ("probe-one", "probe-two"):
            request = factory.get(ENVELOPE_URL, REMOTE_ADDR="10.0.0.9")
            request.user = User.objects.create_user(username=username, password="pw")
            keys.append(_Probe().get_cache_key(request, None))

        self.assertIn("10.0.0.9", keys[0])
        self.assertEqual(keys[0], keys[1])

        elsewhere = factory.get(ENVELOPE_URL, REMOTE_ADDR="10.0.0.10")
        elsewhere.user = User.objects.get(username="probe-one")
        self.assertNotEqual(keys[0], _Probe().get_cache_key(elsewhere, None))

    def test_a_declared_proxy_count_makes_the_header_trusted_again(self):
        """The header is not banned, only disbelieved by default. A deployment
        that declares how many proxies sit in front has said which hop is the
        real peer, and DRF's parsing applies again."""

        class _Probe(IpRateThrottle):
            scope = "vault.account.envelope.ip"

        request = APIRequestFactory().get(
            ENVELOPE_URL, REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR="203.0.113.7"
        )
        request.user = User.objects.create_user(username="proxied", password="pw")

        with patch.object(api_settings, "NUM_PROXIES", 1):
            key = _Probe().get_cache_key(request, None)

        self.assertIn("203.0.113.7", key)

    def test_two_users_behind_one_ip_share_the_budget(self):
        """The per-IP limit exists to catch an exfiltration spread across
        stolen session cookies. Keyed on the user it would just be the
        per-user limit again."""
        alice = User.objects.create_user(username="alice", password="pw")
        bob = User.objects.create_user(username="bob", password="pw")

        with with_rates(**{"vault.account.envelope.ip": "1/hour"}):
            self.client.force_login(alice)
            first = self.client.get(ENVELOPE_URL)
            self.client.force_login(bob)
            second = self.client.get(ENVELOPE_URL)

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second)

    def test_a_client_supplied_forwarded_for_header_buys_no_new_budget(self):
        """DRF's own get_ident falls back to the whole X-Forwarded-For header
        when NUM_PROXIES is unset, and that header is written by the caller.
        Varying it per request would hand out a fresh bucket every time and
        the per-IP limit - the one control aimed at a distributed exfiltration
        - would be a single header away from doing nothing."""
        alice = User.objects.create_user(username="spoofer", password="pw")
        self.client.force_login(alice)

        with with_rates(**{"vault.account.envelope.ip": "1/hour"}):
            first = self.client.get(ENVELOPE_URL, HTTP_X_FORWARDED_FOR="203.0.113.1")
            second = self.client.get(ENVELOPE_URL, HTTP_X_FORWARDED_FOR="203.0.113.2")

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)

    def test_changing_ip_does_not_escape_the_per_user_limit(self):
        """The counterpart to the per-IP limit above: neither dimension can be
        dodged by moving along the other."""
        alice = User.objects.create_user(username="alice2", password="pw")
        self.client.force_login(alice)

        with with_rates(**{"vault.account.envelope.user": "1/hour"}):
            first = self.client.get(ENVELOPE_URL, REMOTE_ADDR="10.0.0.9")
            second = self.client.get(ENVELOPE_URL, REMOTE_ADDR="10.0.0.10")

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)


class ScopeTests(TestCase):
    def test_the_proxy_count_is_unset_by_default(self):
        """Fail closed: a default of anything but None would mean believing a
        caller-supplied header on every deployment that never declared one."""
        self.assertIsNone(settings.REST_FRAMEWORK["NUM_PROXIES"])

    def test_every_declared_scope_has_a_configured_rate(self):
        """A throttle whose scope carries no rate is inert, and DRF says
        nothing about it - the endpoint simply stops being limited."""
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        scopes = {
            member.scope
            for member in vars(throttling).values()
            if isinstance(member, type)
            and str(getattr(member, "scope", "")).startswith("vault.")
        }
        self.assertEqual(len(scopes), 7)
        for scope in scopes:
            with self.subTest(scope=scope):
                self.assertIn(scope, rates)
