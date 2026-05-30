from io import StringIO
from unittest.mock import MagicMock, patch

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


def _mock_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


FULL_DOC = {
    'authorization_endpoint': 'https://op/authorize',
    'token_endpoint': 'https://op/token',
    'userinfo_endpoint': 'https://op/userinfo',
    'jwks_uri': 'https://op/jwks',
}


class OidcDiscoverCommandTests(TestCase):
    @patch('workspace.users.management.commands.oidc_discover.httpx.get')
    def test_prints_all_endpoints(self, mock_get):
        mock_get.return_value = _mock_response(FULL_DOC)
        out = StringIO()
        call_command('oidc_discover', 'https://op.example/realms/r', stdout=out)
        output = out.getvalue()
        self.assertIn('OIDC_OP_AUTHORIZATION_ENDPOINT=https://op/authorize', output)
        self.assertIn('OIDC_OP_TOKEN_ENDPOINT=https://op/token', output)
        self.assertIn('OIDC_OP_USER_ENDPOINT=https://op/userinfo', output)
        self.assertIn('OIDC_OP_JWKS_ENDPOINT=https://op/jwks', output)

    @patch('workspace.users.management.commands.oidc_discover.httpx.get')
    def test_appends_well_known_path(self, mock_get):
        mock_get.return_value = _mock_response(FULL_DOC)
        call_command('oidc_discover', 'https://op.example', stdout=StringIO())
        called_url = mock_get.call_args[0][0]
        self.assertEqual(
            called_url,
            'https://op.example/.well-known/openid-configuration',
        )

    @patch('workspace.users.management.commands.oidc_discover.httpx.get')
    def test_does_not_double_append_well_known(self, mock_get):
        mock_get.return_value = _mock_response(FULL_DOC)
        url = 'https://op.example/.well-known/openid-configuration'
        call_command('oidc_discover', url, stdout=StringIO())
        self.assertEqual(mock_get.call_args[0][0], url)

    @patch('workspace.users.management.commands.oidc_discover.httpx.get')
    def test_errors_on_missing_field(self, mock_get):
        mock_get.return_value = _mock_response({'authorization_endpoint': 'a'})
        with self.assertRaises(CommandError):
            call_command('oidc_discover', 'https://op.example', stdout=StringIO())

    @patch('workspace.users.management.commands.oidc_discover.httpx.get')
    def test_errors_when_fetch_fails(self, mock_get):
        mock_get.side_effect = httpx.ConnectError('boom')
        with self.assertRaises(CommandError):
            call_command('oidc_discover', 'https://op.example', stdout=StringIO())
