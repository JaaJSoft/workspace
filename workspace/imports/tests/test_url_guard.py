from unittest.mock import patch

import httpx2
from django.test import SimpleTestCase, override_settings

from workspace.imports.services.url_guard import (
    UnsafeUrl,
    check_remote_url,
    guarded_transport,
)

from .network import FakeNetwork

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


@override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=False, IMPORTS_ALLOWED_HOSTS=[])
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
        with override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=True):
            with self.assertRaises(UnsafeUrl):
                check_remote_url("http://127.0.0.1:8080/dav")

    def test_private_networks_are_refused_by_default(self):
        with self.assertRaisesRegex(UnsafeUrl, "private network"):
            check_remote_url("https://192.168.1.10/remote.php/dav")

    def test_carrier_grade_nat_counts_as_private(self):
        with self.assertRaisesRegex(UnsafeUrl, "private network"):
            check_remote_url("http://100.64.0.1/")

    @override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=True)
    def test_private_networks_can_be_allowed(self):
        check_remote_url("https://192.168.1.10/remote.php/dav")

    @override_settings(IMPORTS_ALLOWED_HOSTS=["127.0.0.1"])
    def test_allow_listed_address_passes(self):
        check_remote_url("http://127.0.0.1:8080/dav")

    def test_hostnames_are_left_to_the_transport(self):
        with patch("socket.getaddrinfo") as resolve:
            check_remote_url("https://cloud.example.org/")
        resolve.assert_not_called()


@override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=False, IMPORTS_ALLOWED_HOSTS=[])
class GuardedTransportTests(SimpleTestCase):
    def _get(self, url):
        with httpx2.Client(transport=guarded_transport()) as client:
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

    @override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=True)
    def test_private_networks_can_be_allowed(self):
        with FakeNetwork({"nas.example.org": [["192.168.1.10"]]}) as net:
            self._get("http://nas.example.org/")
        self.assertEqual(net.dialed, [("192.168.1.10", 80)])

    @override_settings(IMPORTS_ALLOW_PRIVATE_NETWORKS=True)
    def test_loopback_stays_refused_with_private_networks_allowed(self):
        with FakeNetwork({"nas.example.org": [["127.0.0.1"]]}) as net:
            with self.assertRaises(UnsafeUrl):
                self._get("http://nas.example.org/")
        self.assertEqual(net.dialed, [])

    @override_settings(IMPORTS_ALLOWED_HOSTS=["Nas.Local"])
    def test_an_allow_listed_host_is_dialed_by_name(self):
        with FakeNetwork({}) as net:
            self._get("http://nas.local:8080/")
        self.assertEqual(net.lookups, [])
        self.assertEqual(net.dialed, [("nas.local", 8080)])
