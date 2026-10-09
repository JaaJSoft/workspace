"""HTTP Basic authentication for DAV clients.

Basic is the only scheme every DAV client speaks (file mounts, calendar and
contact apps alike), and OIDC-managed accounts have no usable local password:
for them a personal API token in the password field is the way in.

Results are cached for ``AUTH_TTL`` seconds per worker. A sync client sends a
burst of requests per round, and without the cache each one would pay the
password hash on its own. The flip side is that a revoked token keeps working
on a worker that cached it until its entry expires.
"""

import base64
import binascii
import hashlib
import hmac
import os
import threading
import time

from django.contrib.auth import authenticate
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

AUTH_TTL = 60  # seconds

_auth_cache = {}
_auth_lock = threading.Lock()
_CACHE_KEY_SECRET = os.urandom(32)


def basic_credentials(authorization):
    """Split an ``Authorization: Basic ...`` header into ``(username, password)``.

    None for a missing header, another scheme, or a payload that does not
    decode to ``user:password``.
    """
    scheme, _, payload = (authorization or "").partition(" ")
    if scheme.lower() != "basic" or not payload:
        return None
    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except binascii.Error, UnicodeDecodeError:
        return None
    user_name, sep, password = decoded.partition(":")
    if not sep:
        return None
    return user_name, password


def authenticate_basic(user_name, password):
    """Return the active user *user_name* / *password* identify, or None.

    The password may be the account password or one of the account's API
    tokens.
    """
    cache_key = _cache_key(user_name, password)

    with _auth_lock:
        entry = _auth_cache.get(cache_key)
        if entry and time.monotonic() - entry[1] < AUTH_TTL:
            return entry[0]

    user = authenticate(username=user_name, password=password)
    if user is None:
        user = _api_token_user(user_name, password)
    if user is None or not user.is_active:
        return None

    with _auth_lock:
        now = time.monotonic()
        # Only a miss writes, so expired entries go here or never: the
        # cache lives as long as the worker. A key is only rewritten once
        # expired, and so evicted below before its new insert: the dict
        # stays in cache-time order and eviction stops at the first live
        # entry instead of scanning them all.
        while _auth_cache:
            oldest = next(iter(_auth_cache))
            if now - _auth_cache[oldest][1] < AUTH_TTL:
                break
            del _auth_cache[oldest]
        _auth_cache[cache_key] = (user, now)
    return user


def clear_auth_cache():
    with _auth_lock:
        _auth_cache.clear()


def _api_token_user(user_name, password):
    """Resolve *password* as a Knox API token owned by *user_name*.

    The username must still match the token's owner, so a leaked token
    cannot be presented under another identity.
    """
    try:
        user, _token = KnoxTokenAuthentication().authenticate_credentials(
            password.encode()
        )
    except AuthenticationFailed:
        return None
    if user.get_username() != user_name:
        return None
    return user


def _cache_key(user_name, password):
    """Return a deterministic, non-reversible cache key for the given credentials.

    HMAC-SHA256 keyed with a process-local random secret: the digest cannot
    be brute-forced without the in-memory key, and unlike a KDF it costs
    microseconds. This runs on EVERY DAV request, before the cache lookup can
    short-circuit anything - a KDF here would cost more than the bcrypt
    verification the cache exists to avoid.
    """
    message = f"{user_name}:{password}".encode()
    return hmac.new(_CACHE_KEY_SECRET, message, hashlib.sha256).hexdigest()
