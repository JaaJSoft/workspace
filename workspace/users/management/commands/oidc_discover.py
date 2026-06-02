"""Print mozilla-django-oidc endpoint settings discovered from an OIDC issuer.

Usage:
    manage.py oidc_discover https://idp.example.com/realms/myrealm

Fetches the issuer's `.well-known/openid-configuration` and prints the
`OIDC_OP_*` environment lines to copy into the deployment config. This is a
config-time helper only - the running app uses the explicit endpoints from the
environment, so the boot never depends on the IdP being reachable.
"""

import httpx
from django.core.management.base import BaseCommand, CommandError

from workspace.common.logging import scrub

WELL_KNOWN = '/.well-known/openid-configuration'


class Command(BaseCommand):
    help = (
        "Fetch an OIDC issuer's discovery document and print the OIDC_OP_* "
        "environment variables to configure."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'issuer',
            help='OIDC issuer URL (with or without the .well-known suffix).',
        )

    def handle(self, *args, **options):
        issuer = options['issuer'].rstrip('/')
        url = issuer if issuer.endswith(WELL_KNOWN) else issuer + WELL_KNOWN

        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            resp.raise_for_status()
            doc = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CommandError(
                f'Failed to fetch discovery document from {scrub(url)}: {exc}')

        mapping = {
            'OIDC_OP_AUTHORIZATION_ENDPOINT': doc.get('authorization_endpoint'),
            'OIDC_OP_TOKEN_ENDPOINT': doc.get('token_endpoint'),
            'OIDC_OP_USER_ENDPOINT': doc.get('userinfo_endpoint'),
            'OIDC_OP_JWKS_ENDPOINT': doc.get('jwks_uri'),
        }
        missing = [key for key, value in mapping.items() if not value]
        if missing:
            raise CommandError(
                'Discovery document is missing: ' + ', '.join(missing))

        for key, value in mapping.items():
            self.stdout.write(f'{key}={value}')
