from functools import partial
from unittest import mock

import httpx2
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from workspace.imports.services import url_guard
from workspace.mail.services import autodiscover
from workspace.mail.services.autodiscover import MailServer, MailServers, discover

User = get_user_model()

PROVIDER_FILE = b"""<?xml version="1.0"?>
<clientConfig version="1.1">
  <emailProvider id="example.com">
    <domain>example.com</domain>
    <incomingServer type="imap">
      <hostname>imap.example.com</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <username>%EMAILADDRESS%</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.example.com</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""

AUTOCONFIG_URL = "https://autoconfig.example.com/mail/config-v1.1.xml"
WELL_KNOWN_URL = "https://example.com/.well-known/autoconfig/mail/config-v1.1.xml"
ISPDB_URL = "https://autoconfig.thunderbird.net/v1.1/example.com"

EXAMPLE_SERVERS = MailServers(
    imap=MailServer("imap.example.com", 993, "ssl"),
    smtp=MailServer("smtp.example.com", 587, "starttls"),
)


class FakeWeb:
    """Answers the autoconfig requests from a ``{url: response}`` table.

    A response is bytes (a 200), an int (a status), or an exception to raise.
    Every URL asked for is recorded, in order, in ``requested``.
    """

    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def _handle(self, request):
        url = str(request.url)
        self.requested.append(url)
        answer = self.responses.get(url, 404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx2.Response(answer)
        return httpx2.Response(200, content=answer)

    def __enter__(self):
        client = partial(httpx2.Client, transport=httpx2.MockTransport(self._handle))
        self._patches = [
            mock.patch.object(autodiscover.httpx2, "Client", client),
            # Resolving names is the guard's business, tested on its own below.
            mock.patch.object(autodiscover, "check_remote_url"),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()


class DiscoverTests(SimpleTestCase):
    def test_reads_the_servers_of_a_provider_file(self):
        with FakeWeb({AUTOCONFIG_URL: PROVIDER_FILE}):
            self.assertEqual(discover("example.com"), EXAMPLE_SERVERS)

    def test_asks_the_provider_before_mozillas_database(self):
        with FakeWeb({}) as web:
            self.assertIsNone(discover("example.com"))
        self.assertEqual(web.requested, [AUTOCONFIG_URL, WELL_KNOWN_URL, ISPDB_URL])

    def test_stops_at_the_first_usable_file(self):
        with FakeWeb({AUTOCONFIG_URL: 500, WELL_KNOWN_URL: PROVIDER_FILE}) as web:
            self.assertEqual(discover("example.com"), EXAMPLE_SERVERS)
        self.assertEqual(web.requested, [AUTOCONFIG_URL, WELL_KNOWN_URL])

    def test_falls_through_an_unusable_file_and_a_network_error(self):
        web = FakeWeb(
            {
                AUTOCONFIG_URL: httpx2.ConnectError("refused"),
                WELL_KNOWN_URL: b"<html>not a config</html>",
                ISPDB_URL: PROVIDER_FILE,
            }
        )
        with web:
            self.assertEqual(discover("example.com"), EXAMPLE_SERVERS)

    def test_domain_is_case_insensitive_and_may_end_with_a_dot(self):
        with FakeWeb({AUTOCONFIG_URL: PROVIDER_FILE}):
            self.assertEqual(discover("Example.COM."), EXAMPLE_SERVERS)

    def test_internationalized_domain_is_asked_for_in_ascii(self):
        with FakeWeb({}) as web:
            discover("bücher.example")
        self.assertEqual(
            web.requested[0],
            "https://autoconfig.xn--bcher-kva.example/mail/config-v1.1.xml",
        )

    def test_hostile_domains_are_refused_without_a_request(self):
        hostile = (
            "",
            "localhost",
            "example.com/path",
            "example.com:8080",
            "user@example.com",
            "example.com#frag",
            "example.com?x=1",
            "127.0.0.1",
            "[::1]",
            "exa mple.com",
            "a" * 64 + ".com",
            "example..com",
            "-example.com",
        )
        for domain in hostile:
            with self.subTest(domain=domain), FakeWeb({}) as web:
                self.assertIsNone(discover(domain))
                self.assertEqual(web.requested, [])

    def test_redirects_are_not_followed(self):
        class Redirecting(FakeWeb):
            def _handle(self, request):
                self.requested.append(str(request.url))
                return httpx2.Response(
                    302, headers={"Location": "http://169.254.169.254/latest"}
                )

        with Redirecting({}) as web:
            self.assertIsNone(discover("example.com"))
        self.assertNotIn("http://169.254.169.254/latest", web.requested)
        self.assertEqual(len(web.requested), 3)

    def test_oversized_file_is_dropped(self):
        padding = b"<!-- " + b"x" * (autodiscover._MAX_BYTES + 1) + b" -->"
        big = PROVIDER_FILE.replace(b"</clientConfig>", padding + b"</clientConfig>")
        with FakeWeb({AUTOCONFIG_URL: big}):
            self.assertIsNone(discover("example.com"))

    def test_file_declaring_an_entity_is_dropped(self):
        xxe = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE clientConfig [<!ENTITY leak SYSTEM "file:///etc/hostname">]>'
            b'<clientConfig version="1.1"><emailProvider id="e">'
            b'<incomingServer type="imap"><hostname>&leak;</hostname>'
            b"<port>993</port><socketType>SSL</socketType></incomingServer>"
            b'<outgoingServer type="smtp"><hostname>smtp.example.com</hostname>'
            b"<port>587</port><socketType>STARTTLS</socketType></outgoingServer>"
            b"</emailProvider></clientConfig>"
        )
        with FakeWeb({AUTOCONFIG_URL: xxe}):
            self.assertIsNone(discover("example.com"))

    def test_other_documents_are_not_configs(self):
        for body in (b"", b"not xml", b"<feed><emailProvider/></feed>"):
            with self.subTest(body=body), FakeWeb({AUTOCONFIG_URL: body}):
                self.assertIsNone(discover("example.com"))


class ServerSelectionTests(SimpleTestCase):
    @staticmethod
    def _file(incoming="", outgoing=""):
        return (
            b'<clientConfig version="1.1"><emailProvider id="e">'
            + incoming.encode()
            + outgoing.encode()
            + b"</emailProvider></clientConfig>"
        )

    @staticmethod
    def _imap(host="imap.example.com", port="993", socket_type="SSL"):
        return (
            f'<incomingServer type="imap"><hostname>{host}</hostname>'
            f"<port>{port}</port><socketType>{socket_type}</socketType>"
            "</incomingServer>"
        )

    @staticmethod
    def _smtp(host="smtp.example.com", port="587", socket_type="STARTTLS"):
        return (
            f'<outgoingServer type="smtp"><hostname>{host}</hostname>'
            f"<port>{port}</port><socketType>{socket_type}</socketType>"
            "</outgoingServer>"
        )

    def _discover(self, incoming, outgoing):
        with FakeWeb({AUTOCONFIG_URL: self._file(incoming, outgoing)}):
            return discover("example.com")

    def test_first_usable_server_wins_in_file_order(self):
        servers = self._discover(
            self._imap("imap.one.example", "143", "STARTTLS")
            + self._imap("imap.two.example"),
            self._smtp(),
        )
        self.assertEqual(servers.imap, MailServer("imap.one.example", 143, "starttls"))

    def test_unencrypted_servers_are_skipped(self):
        servers = self._discover(
            self._imap("plain.example.com", "143", "plain") + self._imap(),
            self._smtp(socket_type="plain")
            + self._smtp("smtp.tls.example", "465", "SSL"),
        )
        self.assertEqual(servers.imap.host, "imap.example.com")
        self.assertEqual(servers.smtp, MailServer("smtp.tls.example", 465, "ssl"))

    def test_only_unencrypted_servers_means_nothing_found(self):
        self.assertIsNone(self._discover(self._imap(socket_type="plain"), self._smtp()))

    def test_pop3_alone_is_not_enough(self):
        pop3 = (
            '<incomingServer type="pop3"><hostname>pop.example.com</hostname>'
            "<port>995</port><socketType>SSL</socketType></incomingServer>"
        )
        self.assertIsNone(self._discover(pop3, self._smtp()))

    def test_both_directions_are_required(self):
        self.assertIsNone(self._discover(self._imap(), ""))
        self.assertIsNone(self._discover("", self._smtp()))

    def test_domain_placeholder_is_filled_in(self):
        servers = self._discover(
            self._imap("imap.%EMAILDOMAIN%"), self._smtp("smtp.%EMAILDOMAIN%")
        )
        self.assertEqual(servers.imap.host, "imap.example.com")
        self.assertEqual(servers.smtp.host, "smtp.example.com")

    def test_placeholder_we_cannot_fill_skips_the_server(self):
        servers = self._discover(
            self._imap("%EMAILLOCALPART%.example.com") + self._imap(),
            self._smtp(),
        )
        self.assertEqual(servers.imap.host, "imap.example.com")

    def test_invalid_ports_and_hosts_skip_the_server(self):
        servers = self._discover(
            self._imap(port="0")
            + self._imap(port="99999")
            + self._imap(port="abc")
            + self._imap(host="evil.com/x")
            + self._imap(host="127.0.0.1")
            + self._imap("imap.ok.example"),
            self._smtp(),
        )
        self.assertEqual(servers.imap.host, "imap.ok.example")


class PrivateNetworkGuardTests(SimpleTestCase):
    """The real guard, with only name resolution replaced."""

    def _discover_resolving_to(self, address):
        answer = [(2, 1, 6, "", (address, 0))]
        web = FakeWeb({AUTOCONFIG_URL: PROVIDER_FILE, WELL_KNOWN_URL: PROVIDER_FILE})
        with (
            mock.patch.object(url_guard.socket, "getaddrinfo", return_value=answer),
            mock.patch.object(
                autodiscover.httpx2,
                "Client",
                partial(httpx2.Client, transport=httpx2.MockTransport(web._handle)),
            ),
        ):
            return discover("example.com"), web

    def test_domain_resolving_to_a_private_network_is_never_contacted(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254"):
            with self.subTest(address=address):
                servers, web = self._discover_resolving_to(address)
                self.assertIsNone(servers)
                self.assertEqual(web.requested, [])

    def test_public_address_is_contacted(self):
        servers, web = self._discover_resolving_to("93.184.216.34")
        self.assertEqual(servers, EXAMPLE_SERVERS)
        self.assertEqual(web.requested, [AUTOCONFIG_URL])


class AutodiscoverViewTests(APITestCase):
    url = "/api/v1/mail/autodiscover"

    def setUp(self):
        self.user = User.objects.create_user(username="autodiscover", password="x")
        self.client.force_authenticate(self.user)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        resp = self.client.post(self.url, {"email": "a@example.com"}, format="json")
        self.assertIn(resp.status_code, (401, 403))

    def test_maps_the_servers_to_the_account_form_fields(self):
        with FakeWeb({AUTOCONFIG_URL: PROVIDER_FILE}):
            resp = self.client.post(
                self.url, {"email": "me@example.com"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_use_ssl": True,
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_use_tls": True,
            },
        )

    def test_implicit_tls_smtp_is_not_flagged_as_starttls(self):
        implicit = PROVIDER_FILE.replace(
            b"<port>587</port>\n      <socketType>STARTTLS</socketType>",
            b"<port>465</port>\n      <socketType>SSL</socketType>",
        )
        with FakeWeb({AUTOCONFIG_URL: implicit}):
            data = self.client.post(
                self.url, {"email": "me@example.com"}, format="json"
            ).json()
        self.assertEqual(data["smtp_port"], 465)
        self.assertFalse(data["smtp_use_tls"])

    def test_unknown_domain_is_a_404(self):
        with FakeWeb({}):
            resp = self.client.post(
                self.url, {"email": "me@example.com"}, format="json"
            )
        self.assertEqual(resp.status_code, 404)

    def test_missing_or_malformed_email_is_a_400(self):
        for payload in ({}, {"email": ""}, {"email": "no-at-sign"}):
            with self.subTest(payload=payload):
                resp = self.client.post(self.url, payload, format="json")
                self.assertEqual(resp.status_code, 400)
