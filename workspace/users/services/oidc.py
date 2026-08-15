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
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Django's UnicodeUsernameValidator allows letters, digits and @ . + - _ .
_USERNAME_DISALLOWED = re.compile(r"[^\w.@+-]", re.UNICODE)


class WorkspaceOIDCBackend(OIDCAuthenticationBackend):
    """OIDC backend with JIT provisioning, domain allowlist and readable usernames."""

    def verify_claims(self, claims):
        """Gate login: base checks + email presence + optional email_verified + allowlist.

        Returning False makes authenticate() return None, so no session is created.
        """
        if not super().verify_claims(claims):
            return False

        email = str(claims.get("email") or "")
        if not email:
            logger.warning("OIDC login refused: no email claim")
            return False

        # The subject is the account's immutable identity: without it the user
        # would be provisioned unlinked, hence not IdP-managed, and any later
        # subject could claim the account through the email match.
        if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
            logger.warning("OIDC login refused: no subject claim for %s", scrub(email))
            return False

        if settings.OIDC_REQUIRE_EMAIL_VERIFIED:
            verified = claims.get("email_verified")
            if verified not in (True, "true", "True"):
                logger.warning(
                    "OIDC login refused: email not verified for %s", scrub(email)
                )
                return False

        allowed = settings.OIDC_ALLOWED_DOMAINS
        if allowed:
            domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
            if domain not in allowed:
                logger.warning(
                    "OIDC login refused: domain not allowed for %s", scrub(email)
                )
                return False

        return True

    def filter_users_by_claims(self, claims):
        """Resolve a linked subject first, then fall back to the email match.

        The library matches on the email address alone, so a user whose address
        changed at the IdP would miss their own account, be sent down
        create_user, and have the login refused by the subject that is already
        bound. The subject is the stable identifier - it wins over the email.
        """
        sub = str(claims.get("sub") or "")
        if sub:
            linked = self.UserModel.objects.filter(oidc_identity__sub=sub)
            if linked.exists():
                return linked
        return super().filter_users_by_claims(claims)

    def create_user(self, claims):
        """JIT-provision a Django user with a readable username and profile fields.

        Deliberately does NOT call super().create_user(), whose default username
        is a hash of the email address.
        """
        username = self._generate_username(claims)
        email = str(claims.get("email") or "")
        with transaction.atomic():
            user = self.UserModel.objects.create_user(username, email=email)
            user.first_name = str(claims.get("given_name") or "")[:150]
            user.last_name = str(claims.get("family_name") or "")[:150]
            user.save(update_fields=["first_name", "last_name"])
            self._link_identity(user, claims)
        logger.info("OIDC JIT-provisioned user %s", scrub(username))
        return user

    def update_user(self, user, claims):
        """Sync IdP-managed profile fields on each login and keep the link.

        The identity provider is authoritative for the display name and the
        address, so refresh them from the claims - but only when the claim is
        present, so a provider that omits them never wipes an existing value.
        The identity link is validated first, so a subject mismatch refuses the
        login before any profile field is touched.
        """
        self._link_identity(user, claims)
        fields = []
        email = str(claims.get("email") or "")
        if email and email != user.email:
            user.email = email[:254]
            fields.append("email")
        given_name = claims.get("given_name")
        if given_name:
            user.first_name = str(given_name)[:150]
            fields.append("first_name")
        family_name = claims.get("family_name")
        if family_name:
            user.last_name = str(family_name)[:150]
            fields.append("last_name")
        if fields:
            user.save(update_fields=fields)
        return user

    def _link_identity(self, user, claims):
        """Record the user's OIDC identity link (the OIDC-managed marker).

        The link is created once on first login (JIT or first email match) and
        is then immutable. A login whose ``sub`` disagrees with the stored one,
        or a ``sub`` already bound to a different account, is refused - so the
        stored subject is a real anti-takeover check (e.g. against a recycled
        email address), not just a passive marker.
        """
        from ..models import OIDCIdentity

        sub = str(claims.get("sub") or "").strip()
        if not sub:
            # verify_claims already refused this login; reaching here means the
            # backend was driven directly, and an unlinked account is exactly
            # what the subject binding exists to prevent.
            raise SuspiciousOperation("OIDC claims carry no subject")

        existing = OIDCIdentity.objects.filter(user=user).first()
        if existing is not None:
            if existing.sub != sub:
                logger.warning(
                    "OIDC login refused: subject changed for user %s",
                    scrub(user.get_username()),
                )
                raise SuspiciousOperation("OIDC subject mismatch for existing user")
            return

        if OIDCIdentity.objects.filter(sub=sub).exists():
            logger.warning(
                "OIDC login refused: subject already linked to another account"
            )
            raise SuspiciousOperation("OIDC subject already linked to another account")

        OIDCIdentity.objects.create(user=user, sub=sub)

    def _generate_username(self, claims):
        """Build a unique, sanitized username from the configured claim.

        Falls back to the email local-part, then the `sub` claim, and appends a
        numeric suffix on collision.
        """
        claim_name = settings.OIDC_USERNAME_CLAIM
        email = str(claims.get("email") or "")
        raw = (
            claims.get(claim_name)
            or (email.split("@", 1)[0] if email else "")
            or claims.get("sub")
            or "user"
        )
        base = _USERNAME_DISALLOWED.sub("", str(raw))[:150] or "user"

        username = base
        suffix = 1
        while self.UserModel.objects.filter(username=username).exists():
            suffix += 1
            suffix_text = str(suffix)
            username = f"{base[: 150 - len(suffix_text)]}{suffix_text}"
        return username


def is_oidc_managed(user):
    """Return True if *user* is linked to an external OIDC identity.

    Such users authenticate through the identity provider, so their display
    name is IdP-managed and local password changes are disabled.
    """
    from ..models import OIDCIdentity

    if not getattr(user, "is_authenticated", False):
        return False
    return OIDCIdentity.objects.filter(user=user).exists()
