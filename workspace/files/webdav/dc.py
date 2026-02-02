"""Domain controller for WsgiDAV using Django's auth backend."""

import hashlib
import os
import threading
import time

from django.contrib.auth import authenticate
from wsgidav.dc.base_dc import BaseDomainController

_auth_cache = {}
_auth_lock = threading.Lock()
_AUTH_TTL = 60  # seconds
_CACHE_KEY_SECRET = os.urandom(32)

# TODO find a better way !!!!!
class DjangoBasicDomainController(BaseDomainController):
    """Authenticate WebDAV requests via Django's ``authenticate()``.

    Results are cached for ``_AUTH_TTL`` seconds to avoid running the
    full authentication backend (bcrypt hash + DB query) on every HTTP
    request.
    """

    def __init__(self, wsgidav_app, config):
        super().__init__(wsgidav_app, config)

    def get_domain_realm(self, path_info, environ):
        return "Workspace"

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        return False

    def basic_auth_user(self, realm, user_name, password, environ):
        cache_key = _cache_key(user_name, password)

        with _auth_lock:
            entry = _auth_cache.get(cache_key)
            if entry and time.monotonic() - entry[1] < _AUTH_TTL:
                environ["workspace.user"] = entry[0]
                return True

        user = authenticate(username=user_name, password=password)
        if user is None or not user.is_active:
            return False

        with _auth_lock:
            _auth_cache[cache_key] = (user, time.monotonic())
        environ["workspace.user"] = user
        return True


def _cache_key(user_name, password):
    """Return a deterministic, non-reversible cache key for the given credentials.

    Uses PBKDF2-HMAC with a process-local salt.  The iteration count is kept
    modest (100 000 ≈ 30-50 ms) because this is only an in-memory cache key —
    the real password verification is handled by Django's auth backend.
    """
    message = f"{user_name}:{password}".encode()
    dk = hashlib.pbkdf2_hmac("sha256", message, _CACHE_KEY_SECRET, 100_000)
    return dk.hex()
