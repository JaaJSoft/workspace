"""OIDC authentication backend for self-hosted SSO login.

Subclasses mozilla-django-oidc's backend to add the project's provisioning
rules: optional email-verified enforcement, an optional email-domain allowlist,
and a human-readable Django username derived from a configurable claim.

The backend is only wired into AUTHENTICATION_BACKENDS when OIDC is configured
(see settings.OIDC_ENABLED) because OIDCAuthenticationBackend.__init__ reads the
OP endpoints without defaults and raises ImproperlyConfigured otherwise.
"""

import logging
import re

from django.conf import settings
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Django's UnicodeUsernameValidator allows letters, digits and @ . + - _ .
_USERNAME_DISALLOWED = re.compile(r'[^\w.@+-]', re.UNICODE)


class WorkspaceOIDCBackend(OIDCAuthenticationBackend):
    """OIDC backend with JIT provisioning, domain allowlist and readable usernames."""

    def verify_claims(self, claims):
        """Gate login: base checks + email presence + optional email_verified + allowlist.

        Returning False makes authenticate() return None, so no session is created.
        """
        if not super().verify_claims(claims):
            return False

        email = str(claims.get('email') or '')
        if not email:
            logger.warning('OIDC login refused: no email claim')
            return False

        if getattr(settings, 'OIDC_REQUIRE_EMAIL_VERIFIED', False):
            verified = claims.get('email_verified')
            if verified not in (True, 'true', 'True'):
                logger.warning(
                    'OIDC login refused: email not verified for %s', scrub(email))
                return False

        allowed = getattr(settings, 'OIDC_ALLOWED_DOMAINS', []) or []
        if allowed:
            domain = email.rsplit('@', 1)[-1].lower() if '@' in email else ''
            if domain not in allowed:
                logger.warning(
                    'OIDC login refused: domain not allowed for %s', scrub(email))
                return False

        return True

    def create_user(self, claims):
        """JIT-provision a Django user with a readable username and profile fields.

        Deliberately does NOT call super().create_user(), whose default username
        is a hash of the email address.
        """
        username = self._generate_username(claims)
        email = str(claims.get('email') or '')
        user = self.UserModel.objects.create_user(username, email=email)
        user.first_name = str(claims.get('given_name') or '')[:150]
        user.last_name = str(claims.get('family_name') or '')[:150]
        user.save(update_fields=['first_name', 'last_name'])
        logger.info('OIDC JIT-provisioned user %s', scrub(username))
        return user

    def _generate_username(self, claims):
        """Build a unique, sanitized username from the configured claim.

        Falls back to the email local-part, then the `sub` claim, and appends a
        numeric suffix on collision.
        """
        claim_name = getattr(settings, 'OIDC_USERNAME_CLAIM', 'preferred_username')
        email = str(claims.get('email') or '')
        raw = (
            claims.get(claim_name)
            or (email.split('@', 1)[0] if email else '')
            or claims.get('sub')
            or 'user'
        )
        base = _USERNAME_DISALLOWED.sub('', str(raw))[:150] or 'user'

        username = base
        suffix = 1
        while self.UserModel.objects.filter(username=username).exists():
            suffix += 1
            username = f'{base}{suffix}'[:150]
        return username
