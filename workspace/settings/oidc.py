"""OpenID Connect (SSO) login and the authentication backends."""

import os

from .env import env_bool, env_list

# Enabled only when the core RP credentials + endpoints are all set. All values
# default to '' so the mozilla-django-oidc views never raise ImproperlyConfigured
# at import time when OIDC is disabled.
OIDC_RP_CLIENT_ID = os.getenv("OIDC_RP_CLIENT_ID", "")
OIDC_RP_CLIENT_SECRET = os.getenv("OIDC_RP_CLIENT_SECRET", "")
OIDC_OP_AUTHORIZATION_ENDPOINT = os.getenv("OIDC_OP_AUTHORIZATION_ENDPOINT", "")
OIDC_OP_TOKEN_ENDPOINT = os.getenv("OIDC_OP_TOKEN_ENDPOINT", "")
OIDC_OP_USER_ENDPOINT = os.getenv("OIDC_OP_USER_ENDPOINT", "")
OIDC_OP_JWKS_ENDPOINT = os.getenv("OIDC_OP_JWKS_ENDPOINT", "")

# Override two library defaults on purpose:
#   - RP_SIGN_ALGO: lib default 'HS256'; real IdPs sign with RS256 (uses JWKS).
#   - RP_SCOPES: lib default 'openid email'; 'profile' is required to receive
#     preferred_username / given_name / family_name.
OIDC_RP_SIGN_ALGO = os.getenv("OIDC_RP_SIGN_ALGO", "RS256")
OIDC_RP_SCOPES = os.getenv("OIDC_RP_SCOPES", "openid email profile")

# Project-specific knobs (not part of mozilla-django-oidc):
OIDC_PROVIDER_NAME = os.getenv("OIDC_PROVIDER_NAME", "OpenID")
OIDC_ALLOWED_DOMAINS = [domain.lower() for domain in env_list("OIDC_ALLOWED_DOMAINS")]
OIDC_REQUIRE_EMAIL_VERIFIED = env_bool("OIDC_REQUIRE_EMAIL_VERIFIED")
OIDC_USERNAME_CLAIM = os.getenv("OIDC_USERNAME_CLAIM", "preferred_username")

# RS*/ES* signing (our default RS256) requires a JWKS endpoint or the backend's
# __init__ raises ImproperlyConfigured (verified against mozilla-django-oidc
# 5.0.2). Gate OIDC_ENABLED on it too, so a half-configured IdP (everything but
# the JWKS) never enables the backend and breaks local login.
_oidc_core_configured = all(
    [
        OIDC_RP_CLIENT_ID,
        OIDC_RP_CLIENT_SECRET,
        OIDC_OP_AUTHORIZATION_ENDPOINT,
        OIDC_OP_TOKEN_ENDPOINT,
        OIDC_OP_USER_ENDPOINT,
    ]
)
_oidc_sign_key_ok = not OIDC_RP_SIGN_ALGO.startswith(("RS", "ES")) or bool(
    OIDC_OP_JWKS_ENDPOINT
)
OIDC_ENABLED = _oidc_core_configured and _oidc_sign_key_ok

# Keep local username/password login working; add the OIDC backend ONLY when
# configured (its __init__ raises ImproperlyConfigured on missing endpoints, and
# Django instantiates every backend on each authenticate()).
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
if OIDC_ENABLED:
    AUTHENTICATION_BACKENDS.insert(
        0, "workspace.users.services.oidc.WorkspaceOIDCBackend"
    )
