"""Two traps live here, and both fail silently in production.

`AnonRateThrottle` returns no cache key for an authenticated request, so a
per-IP limit built on it never fires. And `override_settings(REST_FRAMEWORK=…)`
replaces the whole dictionary while DRF caches its own view of it, so a test
that pins only the rates strips the authentication classes and reads stale
values unless `api_settings` is reloaded.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory

from workspace.vault import throttling
from workspace.vault.throttling import IpRateThrottle

User = get_user_model()

ENVELOPE_URL = "/api/v1/vault/account/envelope"


def rest_framework_with(rates):
    """The real REST_FRAMEWORK setting with *rates* merged into its own."""
    merged = dict(settings.REST_FRAMEWORK)
    merged["DEFAULT_THROTTLE_RATES"] = {
        **settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}),
        **rates,
    }
    return merged


class IpRateThrottleTests(TestCase):
    def tearDown(self):
        cache.clear()
        api_settings.reload()

    def test_the_cache_key_ignores_the_authenticated_user(self):
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

    def test_two_users_behind_one_ip_share_the_budget(self):
        """The per-IP limit exists to catch exfiltration spread across stolen
        cookies. Keyed on the user it would just be the per-user limit again."""
        alice = User.objects.create_user(username="alice", password="pw")
        bob = User.objects.create_user(username="bob", password="pw")

        with override_settings(
            REST_FRAMEWORK=rest_framework_with({"vault.account.envelope.ip": "1/hour"})
        ):
            api_settings.reload()
            self.client.force_login(alice)
            first = self.client.get(ENVELOPE_URL)
            self.client.force_login(bob)
            second = self.client.get(ENVELOPE_URL)

        self.assertNotEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second)


class ScopeTests(TestCase):
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
