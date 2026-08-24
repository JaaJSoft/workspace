"""WOPI access tokens: per-user, per-file, signed, short-lived.

The token travels as a query parameter on every editor request - that is what
the WOPI protocol mandates - so it must carry no secret beyond its own
capability: it names a user, a file and a write flag, and expires. Permissions
are re-checked against the live ACL on every request, so a token outliving a
revoked share grants nothing.
"""

from django.contrib.auth import get_user_model
from django.core import signing

_SALT = "workspace.files.wopi"


def mint_access_token(user, file_uuid, can_write: bool) -> str:
    return signing.dumps(
        {"u": str(user.pk), "f": str(file_uuid), "w": bool(can_write)}, salt=_SALT
    )


def parse_access_token(token: str, file_uuid) -> tuple | None:
    """(user, can_write) when *token* is valid for *file_uuid*, else None."""
    from django.conf import settings

    try:
        data = signing.loads(token, salt=_SALT, max_age=settings.WOPI_TOKEN_TTL)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get("f") != str(file_uuid):
        return None
    User = get_user_model()
    user = User.objects.filter(pk=data.get("u"), is_active=True).first()
    if user is None:
        return None
    return user, bool(data.get("w"))
