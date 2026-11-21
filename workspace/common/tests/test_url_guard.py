from unittest.mock import patch

import httpx2
from django.test import SimpleTestCase

from workspace.common.url_guard import (
    NetworkPolicy,
    ResponseTooLarge,
    UnsafeUrl,
    check_remote_url,
    check_resolved_url,
    guarded_transport,
    read_capped,
)

from .network import FakeNetwork, http_response

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
LAN = NetworkPolicy(allow_private_networks=True)


class RemoteUrlGuardTests(SimpleTestCase):
    def test_rejects_non_http_schemes(self):
        with self.assertRaises(UnsafeUrl):
            check_remote_url("ftp://example.org/")
        with self.assertRaises(UnsafeUrl):
            check_remote_url("file:///etc/passwd")

    def test_rejects_urls_without_host(self):
        with self.assertRaises(UnsafeUrl):
            check_remote_url("http:///path")

    def test_loopback_and_link_local_are_always_refused(self):
        for url in ("http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/"):
            with self.assertRaises(UnsafeUrl, msg=url):
                check_remote_url(url)

    def test_loopback_stays_refused_even_with_private_networks_allowed(self):
        with self.assertRaises(UnsafeUrl):
            check_remote_url("http://127.0.0.1:8080/dav", LAN)

    def test_private_networks_are_refused_by_default(self):
        with self.assertRaisesRegex(UnsafeUrl, "private network"):
            check_remote_url("https://192.168.1.10/remote.php/dav")

    def test_carrier_grade_nat_counts_as_private(self):
        with self.assertRaisesRegex(UnsafeUrl, "private network"):
            check_remote_url("http://100.64.0.1/")

    def test_private_networks_can_be_allowed(self):
        check_remote_url("https://192.168.1.10/remote.php/dav", LAN)

    def test_the_policy_hint_follows_the_private_network_refusal(self):
        policy = NetworkPolicy(private_network_hint="Ask your administrator.")
        with self.assertRaisesRegex(
            UnsafeUrl, r"private network\. Ask your administrator\.$"
        ):
            check_remote_url("https://10.1.2.3/", policy)

    def test_allow_listed_address_passes(self):
        policy = NetworkPolicy(allowed_hosts=["127.0.0.1"])
        check_remote_url("http://127.0.0.1:8080/dav", policy)

    def test_localhost_names_are_refused_without_a_lookup(self):
        with patch("socket.getaddrinfo") as resolve:
            for url in ("http://localhost:8000/", "http://api.LOCALHOST/"):
                with self.assertRaises(UnsafeUrl, msg=url):
                    check_remote_url(url, LAN)
        resolve.assert_not_called()

    def test_ipv4_mapped_loopback_is_refused(self):
        with self.assertRaises(UnsafeUrl):
            check_remote_url("http://[::ffff:127.0.0.1]/", LAN)

    def test_a_malformed_url_is_refused_not_a_crash(self):
        with self.assertRaises(UnsafeUrl):
            check_remote_url("http://[::1/")

    def test_hostnames_are_left_to_the_transport(self):
        with patch("socket.getaddrinfo") as resolve:
            check_remote_url("https://cloud.example.org/")
        resolve.assert_not_called()


class GuardedTransportTests(SimpleTestCase):
    def _get(self, url, policy=None, **kwargs):
        transport = guarded_transport() if policy is None else guarded_transport(policy)
        with httpx2.Client(transport=transport, **kwargs) as client:
            return client.get(url)

    def test_dials_the_address_the_guard_vetted(self):
        # DNS rebinding: a public answer for the check, loopback for any
        # second lookup. The transport must never make that second lookup.
        answers = {"cloud.example.org": [[PUBLIC], ["127.0.0.1"]]}
        with FakeNetwork(answers) as net:
            self.assertEqual(
                self._get("https://cloud.example.org/dav").status_code, 200
            )
        self.assertEqual(net.lookups, ["cloud.example.org"])
        self.assertEqual(net.dialed, [(PUBLIC, 443)])

    def test_tls_and_the_host_header_keep_the_hostname(self):
        with FakeNetwork({"cloud.example.org": [[PUBLIC]]}) as net:
            self._get("https://cloud.example.org/dav")
        self.assertEqual(net.server_names, ["cloud.example.org"])
        self.assertIn(b"\r\nHost: cloud.example.org\r\n", bytes(net.sent))

    def test_a_private_answer_is_refused_before_dialing(self):
        for address in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"):
            with self.subTest(address=address):
                with FakeNetwork({"cloud.example.org": [[address]]}) as net:
                    with self.assertRaises(UnsafeUrl):
                        self._get("https://cloud.example.org/")
                self.assertEqual(net.dialed, [])

    def test_every_answer_is_checked(self):
        answers = {"cloud.example.org": [[PUBLIC, "10.0.0.5"]]}
        with FakeNetwork(answers) as net:
            with self.assertRaisesRegex(UnsafeUrl, "private network"):
                self._get("https://cloud.example.org/")
        self.assertEqual(net.dialed, [])

    def test_the_next_answer_is_tried_when_one_is_unreachable(self):
        answers = {"cloud.example.org": [[PUBLIC_V6, PUBLIC]]}
        with FakeNetwork(answers, unreachable=[PUBLIC_V6]) as net:
            self.assertEqual(self._get("http://cloud.example.org/").status_code, 200)
        self.assertEqual(net.dialed, [(PUBLIC_V6, 80), (PUBLIC, 80)])

    def test_no_reachable_answer_is_a_connect_error(self):
        answers = {"cloud.example.org": [[PUBLIC_V6, PUBLIC]]}
        with FakeNetwork(answers, unreachable=[PUBLIC_V6, PUBLIC]) as net:
            with self.assertRaisesRegex(httpx2.ConnectError, PUBLIC):
                self._get("http://cloud.example.org/")
        self.assertEqual(net.dialed, [(PUBLIC_V6, 80), (PUBLIC, 80)])

    def test_an_unresolvable_host_is_refused(self):
        with FakeNetwork({}) as net:
            with self.assertRaisesRegex(UnsafeUrl, "could not be resolved"):
                self._get("https://nope.invalid/")
        self.assertEqual(net.dialed, [])

    def test_an_address_in_the_url_is_checked_without_a_lookup(self):
        with FakeNetwork({}) as net:
            with self.assertRaises(UnsafeUrl):
                self._get("http://127.0.0.1:8080/")
            self._get(f"http://{PUBLIC}/")
        self.assertEqual(net.lookups, [])
        self.assertEqual(net.dialed, [(PUBLIC, 80)])

    def test_private_networks_can_be_allowed(self):
        with FakeNetwork({"nas.example.org": [["192.168.1.10"]]}) as net:
            self._get("http://nas.example.org/", LAN)
        self.assertEqual(net.dialed, [("192.168.1.10", 80)])

    def test_loopback_stays_refused_with_private_networks_allowed(self):
        with FakeNetwork({"nas.example.org": [["127.0.0.1"]]}) as net:
            with self.assertRaises(UnsafeUrl):
                self._get("http://nas.example.org/", LAN)
        self.assertEqual(net.dialed, [])

    def test_an_allow_listed_host_is_dialed_by_name(self):
        policy = NetworkPolicy(allowed_hosts=["Nas.Local"])
        with FakeNetwork({}) as net:
            self._get("http://nas.local:8080/", policy)
        self.assertEqual(net.lookups, [])
        self.assertEqual(net.dialed, [("nas.local", 8080)])

    def test_every_redirect_hop_is_vetted(self):
        # A public page answering with a redirect to an internal name: the
        # client follows it, and the guard refuses the hop before dialing.
        redirect = http_response(302, content_type="text/html").replace(
            b"\r\n\r\n", b"\r\nLocation: http://redis:6379/\r\n\r\n", 1
        )
        answers = {"cloud.example.org": [[PUBLIC]], "redis": [["172.18.0.3"]]}
        with FakeNetwork(answers, response=redirect) as net:
            with self.assertRaisesRegex(UnsafeUrl, "private network"):
                self._get("https://cloud.example.org/feed", follow_redirects=True)
        self.assertEqual(net.lookups, ["cloud.example.org", "redis"])
        self.assertEqual(net.dialed, [(PUBLIC, 443)])


class CheckResolvedUrlTests(SimpleTestCase):
    def test_a_name_resolving_to_a_public_address_passes(self):
        with FakeNetwork({"push.example.org": [[PUBLIC]]}) as net:
            check_resolved_url("https://push.example.org/sub/1")
        self.assertEqual((net.lookups, net.dialed), (["push.example.org"], []))

    def test_a_name_resolving_to_a_private_address_is_refused(self):
        with FakeNetwork({"push.example.org": [[PUBLIC, "10.0.0.5"]]}):
            with self.assertRaisesRegex(UnsafeUrl, "private network"):
                check_resolved_url("https://push.example.org/sub/1")

    def test_an_unresolvable_name_is_refused(self):
        with FakeNetwork({}):
            with self.assertRaisesRegex(UnsafeUrl, "could not be resolved"):
                check_resolved_url("https://nope.invalid/")

    def test_an_allow_listed_host_is_not_resolved(self):
        policy = NetworkPolicy(allowed_hosts=["nas.local"])
        with FakeNetwork({}) as net:
            check_resolved_url("http://nas.local/", policy)
        self.assertEqual(net.lookups, [])


class ReadCappedTests(SimpleTestCase):
    def _response(self, body, **headers):
        return httpx2.Response(200, headers=headers, content=body)

    def test_a_body_within_the_cap_is_returned_whole(self):
        self.assertEqual(read_capped(self._response(b"x" * 10), 10), b"x" * 10)

    def test_a_body_past_the_cap_is_refused(self):
        with self.assertRaises(ResponseTooLarge):
            read_capped(self._response(b"x" * 11), 10)

    def test_the_cap_counts_decompressed_bytes(self):
        import gzip

        bomb = gzip.compress(b"\0" * 100_000)
        response = self._response(bomb, **{"Content-Encoding": "gzip"})
        with self.assertRaises(ResponseTooLarge):
            read_capped(response, len(bomb) * 2)
