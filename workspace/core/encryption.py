"""Fernet-based symmetric encryption for sensitive data stored at rest.

Derives a stable Fernet key from Django SECRET_KEY so that ciphertext
survives application restarts. Intended for any module that needs to
store encrypted credentials or secrets in the database.

Usage::

    from workspace.core.encryption import encrypt, decrypt

    ciphertext = encrypt("my-secret")   # bytes
    plaintext  = decrypt(ciphertext)    # str
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
        _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> bytes:
    """Encrypt a plaintext string and return ciphertext bytes."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    """Decrypt ciphertext bytes and return the original plaintext string."""
    return _get_fernet().decrypt(ciphertext).decode()