"""Reference TOTP, on the standard library alone.

Test-only, like every module in this package. It is written from RFC 4226 and
RFC 6238 rather than from the browser module, because a reference that borrowed
the implementation it measures would prove only that a file equals itself.
"""

import base64
import hashlib
import hmac
import struct

_HASHES = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def base32_decode(text: str) -> bytes:
    """RFC 4648 base32, forgiving about how a service prints it."""
    cleaned = "".join(text.split()).replace("-", "").rstrip("=").upper()
    if not cleaned:
        raise ValueError("authenticator secret is empty")
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def totp_code(
    secret: bytes, *, algorithm: str = "SHA1", digits: int = 6, period: int = 30, at: int
) -> str:
    counter = int(at) // period
    mac = hmac.new(secret, struct.pack(">Q", counter), _HASHES[algorithm]).digest()
    # RFC 4226 dynamic truncation.
    offset = mac[-1] & 0x0F
    truncated = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % 10**digits).zfill(digits)
