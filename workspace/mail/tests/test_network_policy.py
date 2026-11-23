"""Mail connects to the IMAP and SMTP servers users type, so a server is
vetted before anything is dialed: loopback never, a private network only when
the administrator opened it. The address that was vetted is the one dialed."""

import contextlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.common.tests.network import FakeNetwork
from workspace.common.url_guard import UnsafeUrl
from workspace.mail.models import MailAccount
from workspace.mail.services.imap_connection import connect_imap
from workspace.mail.services.smtp import connect_smtp

User = get_user_model()

PUBLIC = "93.184.216.34"


def _account(owner, imap_host, smtp_host=None):
    account = MailAccount(
        owner=owner,
        email="me@example.org",
        imap_host=imap_host,
        smtp_host=smtp_host or imap_host,
        username="me",
    )
    account.set_password("secret")
    account.save()
    return account


class _Unreachable:
    """Stands in for ``socket.create_connection``: records where a connection
    was attempted and refuses it, so nothing leaves the test."""

    def __init__(self):
        self.dialed = []

    def __call__(self, address, *args, **kwargs):
        self.dialed.append(address[0])
        raise ConnectionRefusedError


def _dialed(account):
    """The addresses each connector tried to reach for *account*."""
    dialed = {}
    for protocol, connect in (("imap", connect_imap), ("smtp", connect_smtp)):
        network = _Unreachable()
        with (
            patch("socket.create_connection", network),
            contextlib.suppress(UnsafeUrl, OSError),
        ):
            connect(account)
        dialed[protocol] = network.dialed
    return dialed


class ConnectionGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="guarded", password="pass")

    def assertRefused(self, account):
        self.assertEqual(_dialed(account), {"imap": [], "smtp": []})

    def assertDialed(self, account, address):
        self.assertEqual(_dialed(account), {"imap": [address], "smtp": [address]})

    def test_loopback_is_refused_before_anything_is_dialed(self):
        for host in ("127.0.0.1", "localhost", "169.254.169.254"):
            with self.subTest(host=host):
                self.assertRefused(_account(self.user, host))

    def test_a_private_address_is_refused_by_default(self):
        self.assertRefused(_account(self.user, "192.168.1.20"))

    @override_settings(MAIL_ALLOW_PRIVATE_NETWORKS=True)
    def test_private_networks_can_be_opened(self):
        self.assertDialed(_account(self.user, "192.168.1.20"), "192.168.1.20")
        self.assertRefused(_account(self.user, "127.0.0.1"))

    def test_a_name_with_one_private_answer_is_refused(self):
        with FakeNetwork({"mail.example.org": [[PUBLIC, "10.0.0.5"]]}):
            self.assertRefused(_account(self.user, "mail.example.org"))

    def test_a_name_is_dialed_at_the_address_that_was_vetted(self):
        with FakeNetwork({"mail.example.org": [[PUBLIC]]}):
            self.assertDialed(_account(self.user, "mail.example.org"), PUBLIC)

    def test_a_name_that_changes_its_answer_is_never_dialed_unvetted(self):
        # Public for the first lookup, loopback from then on: each connection
        # looks the name up once and dials what that lookup was checked for.
        account = _account(self.user, "mail.example.org")
        with FakeNetwork({"mail.example.org": [[PUBLIC], ["127.0.0.1"]]}) as net:
            dialed = _dialed(account)
        self.assertEqual(dialed, {"imap": [PUBLIC], "smtp": []})
        self.assertEqual(net.lookups, ["mail.example.org"] * 2)

    @override_settings(MAIL_ALLOWED_HOSTS=["mail.lan"])
    def test_a_listed_server_is_trusted_as_it_is(self):
        with FakeNetwork({}) as net:
            self.assertDialed(_account(self.user, "mail.lan"), "mail.lan")
        self.assertEqual(net.lookups, [])

    @override_settings(
        OAUTH_GENERIC_IMAP_HOST="imap.corp", OAUTH_GENERIC_SMTP_HOST="smtp.corp"
    )
    def test_the_generic_provider_servers_are_trusted(self):
        with FakeNetwork({}):
            dialed = _dialed(_account(self.user, "imap.corp", "smtp.corp"))
        self.assertEqual(dialed, {"imap": ["imap.corp"], "smtp": ["smtp.corp"]})


class AccountHostTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_authenticate(self.user)

    def _payload(self, **overrides):
        return {
            "email": "me@example.org",
            "imap_host": "imap.example.org",
            "smtp_host": "smtp.example.org",
            "username": "me",
            "password": "secret",
            **overrides,
        }

    def test_an_account_on_a_refused_server_is_not_created(self):
        for field, host in (("imap_host", "127.0.0.1"), ("smtp_host", "10.0.0.5")):
            with self.subTest(field=field):
                response = self.client.post(
                    "/api/v1/mail/accounts",
                    self._payload(**{field: host}),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)
        self.assertFalse(MailAccount.objects.exists())

    def test_an_account_cannot_be_moved_to_a_refused_server(self):
        account = _account(self.user, "imap.example.org")
        response = self.client.patch(
            f"/api/v1/mail/accounts/{account.uuid}",
            {"smtp_host": "localhost"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        account.refresh_from_db()
        self.assertEqual(account.smtp_host, "imap.example.org")

    def test_a_named_server_is_accepted_without_a_lookup(self):
        with FakeNetwork({}) as net:
            response = self.client.post(
                "/api/v1/mail/accounts", self._payload(), format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(net.lookups, [])

    def test_the_connection_test_says_why_a_server_is_refused(self):
        # A row stored before servers were vetted.
        account = _account(self.user, "192.168.1.20")
        network = _Unreachable()
        with patch("socket.create_connection", network):
            response = self.client.post(f"/api/v1/mail/accounts/{account.uuid}/test")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for protocol in ("imap", "smtp"):
            self.assertFalse(response.data[protocol]["success"])
            self.assertIn("private network", response.data[protocol]["error"])
        self.assertEqual(network.dialed, [])
