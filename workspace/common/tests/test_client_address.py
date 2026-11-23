from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory

from workspace.common.client_address import client_address


def _request(forwarded_for):
    return APIRequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR=forwarded_for
    )


class ClientAddressTests(SimpleTestCase):
    def test_the_header_is_ignored_until_a_proxy_count_is_declared(self):
        with patch.object(api_settings, "NUM_PROXIES", None):
            self.assertEqual(client_address(_request("203.0.113.7")), "10.0.0.9")

    def test_a_declared_proxy_names_the_entry_it_wrote(self):
        # The caller wrote the first entry, the proxy appended the second.
        with patch.object(api_settings, "NUM_PROXIES", 1):
            self.assertEqual(
                client_address(_request("198.51.100.1, 203.0.113.7")), "203.0.113.7"
            )

    def test_no_proxy_means_the_peer(self):
        with patch.object(api_settings, "NUM_PROXIES", 0):
            self.assertEqual(client_address(_request("203.0.113.7")), "10.0.0.9")
