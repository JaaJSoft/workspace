"""Turn the configured VAPID private key into a py_vapid signing object.

pywebpush hands a private key given as a string straight to
``py_vapid.Vapid.from_string``, which understands only a base64url-encoded
DER body or a raw 32-byte scalar: it base64-decodes the ``-----BEGIN`` armor
along with the key material, so a PEM raises before anything is signed.
Parsing here means the deployment may supply PEM, DER or raw, and pywebpush
receives a ``Vapid`` instance it uses as-is.
"""

from functools import lru_cache

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from py_vapid import Vapid


class VapidKeyError(ValueError):
    """The configured private key could not be parsed."""


@lru_cache(maxsize=4)
def load_vapid_key(private_key: str) -> Vapid:
    """Build a ``Vapid`` signer from a PEM, DER or raw base64url private key.

    Cached because it runs on every push and the key never changes within a
    worker process.
    """
    value = (private_key or "").strip()
    if not value:
        raise VapidKeyError("VAPID private key is empty")

    try:
        if "-----BEGIN" in value:
            # cryptography's loader tolerates the stray leading/trailing
            # newlines a multi-line env var picks up; py_vapid's from_pem,
            # which slices off the first and last line, does not.
            return Vapid(load_pem_private_key(value.encode(), password=None))
        return Vapid.from_string(private_key=value)
    except Exception as exc:
        raise VapidKeyError(
            "VAPID private key is not valid PEM, base64url DER or raw base64url "
            "key material; regenerate it with `manage.py generate_vapid_keys`"
        ) from exc
