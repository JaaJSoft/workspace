"""Parsing of HTTP Basic credentials (RFC 7617)."""

import base64
import binascii


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
