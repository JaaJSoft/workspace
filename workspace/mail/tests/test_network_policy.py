"""Mail connects to the IMAP and SMTP servers users type, so a server is
vetted before anything is dialed: loopback never, a private network only when
the administrator opened it."""

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


class ConnectionGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="guarded", password="pass")

    def _dialed(self, account):
        """Which connectors got as far as opening a connection."""
        with (
            patch("workspace.mail.services.imap_connection.IMAPClient") as imap,
            patch("workspace.mail.services.smtp.smtplib") as smtp,
        ):
            for connect in (connect_imap, connect_smtp):
                with contextlib.suppress(UnsafeUrl):
                    connect(account)
            return {
                "imap": imap.called,
                "smtp": smtp.SMTP.called or smtp.SMTP_SSL.called,
            }

    def assertRefused(self, account):
        self.assertEqual(self._dialed(account), {"imap": False, "smtp": False})

    def assertDialed(self, account):
        self.assertEqual(self._dialed(account), {"imap": True, "smtp": True})

    def test_loopback_is_refused_before_anything_is_dialed(self):
        for host in ("127.0.0.1", "localhost", "169.254.169.254"):
            with self.subTest(host=host):
                self.assertRefused(_account(self.user, host))

    def test_a_private_address_is_refused_by_default(self):
        self.assertRefused(_account(self.user, "192.168.1.20"))

    @override_settings(MAIL_ALLOW_PRIVATE_NETWORKS=True)
    def test_private_networks_can_be_opened(self):
        self.assertDialed(_account(self.user, "192.168.1.20"))
        self.assertRefused(_account(self.user, "127.0.0.1"))

    def test_a_name_pointing_at_a_private_address_is_refused(self):
        with FakeNetwork({"mail.example.org": [[PUBLIC, "10.0.0.5"]]}):
            self.assertRefused(_account(self.user, "mail.example.org"))

    def test_a_public_server_is_dialed(self):
        with FakeNetwork({"mail.example.org": [[PUBLIC]]}):
            self.assertDialed(_account(self.user, "mail.example.org"))

    @override_settings(MAIL_ALLOWED_HOSTS=["mail.lan"])
    def test_a_listed_server_is_trusted_as_it_is(self):
        with FakeNetwork({}) as net:
            self.assertDialed(_account(self.user, "mail.lan"))
        self.assertEqual(net.lookups, [])

    @override_settings(
        OAUTH_GENERIC_IMAP_HOST="imap.corp", OAUTH_GENERIC_SMTP_HOST="smtp.corp"
    )
    def test_the_generic_provider_servers_are_trusted(self):
        with FakeNetwork({}):
            self.assertDialed(_account(self.user, "imap.corp", "smtp.corp"))


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
        with (
            patch("workspace.mail.services.imap_connection.IMAPClient") as imap,
            patch("workspace.mail.services.smtp.smtplib") as smtp,
        ):
            response = self.client.post(f"/api/v1/mail/accounts/{account.uuid}/test")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for protocol in ("imap", "smtp"):
            self.assertFalse(response.data[protocol]["success"])
            self.assertIn("private network", response.data[protocol]["error"])
        imap.assert_not_called()
        smtp.SMTP.assert_not_called()
