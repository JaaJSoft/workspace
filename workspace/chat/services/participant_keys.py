"""Routing identity for a call participant.

A participant is addressed by an opaque string rather than a user id so a
meeting guest, which has no user row, can occupy the same mailbox, presence map
and peer table as a member. The key routes; it does not describe. Anything the
UI needs in order to render a participant (numeric user id for the avatar,
display name) travels beside the key in the serialized payload.

The format is deliberately sortable: the WebRTC glare rules elect exactly one
offerer by comparing two keys, and any total order satisfies that.
"""

import re

USER_PREFIX = "u"
GUEST_PREFIX = "g"

# Canonical decimal payload only: no sign, no underscore digit-group
# separators, no surrounding whitespace, no non-ASCII digits, no leading
# zeros beyond a lone "0". int() accepts all of those spellings, but the only
# producer of a user key is user_key(), so a payload that would not round-trip
# through it is not a key this service issued.
_USER_ID_PAYLOAD_RE = re.compile(r"0|[1-9][0-9]*")


def user_key(user_id):
    """Routing key for a member."""
    return f"{USER_PREFIX}:{user_id}"


def guest_key(guest_uuid):
    """Routing key for a meeting guest."""
    return f"{GUEST_PREFIX}:{guest_uuid}"


def _has_prefix(key, prefix):
    return isinstance(key, str) and key.startswith(f"{prefix}:")


def is_user_key(key):
    return _has_prefix(key, USER_PREFIX)


def is_guest_key(key):
    return _has_prefix(key, GUEST_PREFIX)


def user_id_from_key(key):
    """The int user id a member key addresses, or None for anything else.

    Returns None rather than raising: keys arrive from request bodies, so a
    malformed one is client input to reject, not an internal error.
    """
    if not is_user_key(key):
        return None
    payload = key.split(":", 1)[1]
    if not _USER_ID_PAYLOAD_RE.fullmatch(payload):
        return None
    return int(payload)
