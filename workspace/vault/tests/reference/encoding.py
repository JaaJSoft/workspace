"""Encoding helpers for the reference implementation.

This package mirrors the browser bundle in Python so the two can be compared
byte for byte. It is test-only and must never be imported by application code -
the server is not allowed to hold anything it could decrypt with.
"""

import base64
import hmac
import secrets


def to_base64url(data: bytes) -> str:
    """base64url without padding, the transport form of every ciphertext."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def from_base64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def random_bytes(length: int) -> bytes:
    data = secrets.token_bytes(length)
    if len(data) != length:  # pragma: no cover - defensive, token_bytes is exact
        raise ValueError("CSPRNG returned a short read")
    return data


def equal_bytes(a: bytes, b: bytes) -> bool:
    """Constant-time comparison for tags, AD UUIDs and fingerprints."""
    return hmac.compare_digest(a, b)
