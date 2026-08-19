import socket
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from workspace.imports.services.url_guard import UnsafeUrl, check_remote_url


def _addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


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

    @override_settings(IMPORTS_ALLOWED_HOSTS=["Nas.Local"])
    def test_allow_listed_host_skips_resolution(self):
        with patch("socket.getaddrinfo") as resolve:
            check_remote_url("https://nas.local/dav")
        resolve.assert_not_called()

    def test_hostnames_are_resolved_and_every_address_checked(self):
        with patch(
            "socket.getaddrinfo", return_value=_addrinfo("93.184.216.34", "10.0.0.5")
        ):
            with self.assertRaisesRegex(UnsafeUrl, "private network"):
                check_remote_url("https://cloud.example.org/")

    def test_public_host_passes(self):
        with patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            check_remote_url("https://cloud.example.org/")

    def test_unresolvable_host_is_refused(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaisesRegex(UnsafeUrl, "could not be resolved"):
                check_remote_url("https://nope.invalid/")
