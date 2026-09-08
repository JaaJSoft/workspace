"""Every branch of client_ip, especially the NUM_PROXIES-set hop selection.

That branch is the one place a reversed index would hand back the
attacker-controlled leftmost X-Forwarded-For entry instead of the hop a
trusted proxy actually appended, silently reopening the rate-limit bypass
this helper exists to close. NUM_PROXIES is patched on rest_framework's
api_settings object directly, mirroring workspace/vault/tests/test_throttling.py:
that object caches the setting on first read, so override_settings(REST_FRAMEWORK=...)
alone would not reach it.
"""

from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from rest_framework.settings import api_settings

from workspace.common.request_ip import client_ip

factory = RequestFactory()


class ClientIpTests(SimpleTestCase):
    def test_no_forwarded_for_uses_remote_addr(self):
        request = factory.get("/", REMOTE_ADDR="203.0.113.5")
        self.assertEqual(client_ip(request), "203.0.113.5")

    def test_forged_forwarded_for_is_ignored_when_num_proxies_unset(self):
        request = factory.get(
            "/", REMOTE_ADDR="203.0.113.5", HTTP_X_FORWARDED_FOR="198.51.100.1"
        )
        with patch.object(api_settings, "NUM_PROXIES", None):
            self.assertEqual(client_ip(request), "203.0.113.5")

    def test_num_proxies_one_returns_the_appended_hop_not_the_clients_own_value(self):
        # The attacker writes the leftmost entry; a single trusted proxy
        # appends the real peer after it. NUM_PROXIES=1 must select the
        # LAST entry - addrs[-1] - not the first. A reversed index
        # (addrs[0], or addrs[-len(addrs)] for any single-hop input) would
        # hand back "198.51.100.99" here instead and this assertion fails.
        request = factory.get(
            "/",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="198.51.100.99, 203.0.113.7",
        )
        with patch.object(api_settings, "NUM_PROXIES", 1):
            self.assertEqual(client_ip(request), "203.0.113.7")

    def test_num_proxies_two_with_three_entries_returns_the_right_one(self):
        request = factory.get(
            "/",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="198.51.100.99, 203.0.113.7, 203.0.113.8",
        )
        with patch.object(api_settings, "NUM_PROXIES", 2):
            # Two trusted hops means the real client is two entries from the
            # right: 203.0.113.7, with 203.0.113.8 being the nearest proxy.
            self.assertEqual(client_ip(request), "203.0.113.7")

    def test_num_proxies_larger_than_entry_count_does_not_indexerror(self):
        request = factory.get(
            "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.7"
        )
        with patch.object(api_settings, "NUM_PROXIES", 5):
            self.assertEqual(client_ip(request), "203.0.113.7")

    def test_garbage_forwarded_for_returns_empty_string(self):
        request = factory.get(
            "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="not-an-address"
        )
        with patch.object(api_settings, "NUM_PROXIES", 1):
            self.assertEqual(client_ip(request), "")

    def test_ipv6_address_is_accepted(self):
        request = factory.get("/", REMOTE_ADDR="2001:db8::1")
        self.assertEqual(client_ip(request), "2001:db8::1")

    def test_ipv4_mapped_ipv6_address_is_accepted(self):
        request = factory.get("/", REMOTE_ADDR="::ffff:203.0.113.5")
        self.assertEqual(client_ip(request), "::ffff:203.0.113.5")

    def test_empty_remote_addr_returns_empty_string(self):
        request = factory.get("/", REMOTE_ADDR="")
        self.assertEqual(client_ip(request), "")

    def test_ipv6_zone_id_is_stripped_before_validation(self):
        # ipaddress.ip_address accepts a zone id of unbounded length, so
        # without stripping it first, an attacker-controlled tail riding on
        # a trusted hop would pass through unbounded and unvalidated.
        request = factory.get(
            "/",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="fe80::1%eth0" + "x" * 500,
        )
        with patch.object(api_settings, "NUM_PROXIES", 1):
            self.assertEqual(client_ip(request), "fe80::1")
