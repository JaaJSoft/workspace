"""Fernet-based encryption for mail account credentials.

Derives a stable Fernet key from Django SECRET_KEY so that credentials
survive application restarts but are encrypted at rest.
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings

_fernet = None


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
