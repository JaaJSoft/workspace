"""Guest tokens and the single gate every guest request passes through.

The token is opaque and stored hashed. It is deliberately not signed: the row
is read anyway to check state, so signing would buy statelessness we do not use
while making "remove this guest right now" harder. Validity is re-derived per
request from the event's recurrence rather than baked into the token, so ending
a meeting revokes access on the next request instead of at expiry.
"""

import hashlib
import secrets

from .meeting_occurrences import current_occurrence

TOKEN_BYTES = 32


def issue_token():
    """Return a fresh ``(token, token_hash)`` pair. Only the hash is stored."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_token_or_none(token):
    """``hash_token(token)``, or None for anything that cannot be hashed.

    A lone surrogate passes a str/truthy check but cannot be UTF-8 encoded -
    reachable from any JSON-bodied request, since json.loads accepts unpaired
    surrogates. Shared by every lookup keyed on the token so the guard is
    applied once rather than copied.
    """
    if not token or not isinstance(token, str):
        return None
    try:
        return hash_token(token)
    except UnicodeEncodeError:
        return None


def resolve_guest(token, now=None):
    """The MeetingGuest this token currently authorizes, or None.

    Rejects, in order: an unusable token, an unknown one, a guest that is not
    admitted, a meeting with no reachable occurrence right now, a guest holding
    a token for a different occurrence, and an occurrence the host has ended.
    """
    from ..models import MeetingGuest

    digest = _hash_token_or_none(token)
    if digest is None:
        return None

    guest = (
        MeetingGuest.objects.select_related("meeting", "meeting__event")
        .filter(token_hash=digest)
        .first()
    )
    if guest is None or guest.state != MeetingGuest.State.ADMITTED:
        return None

    occurrence = current_occurrence(guest.meeting, now=now)
    if occurrence is None:
        return None

    start, _end = occurrence
    if guest.occurrence_start != start:
        return None
    if guest.meeting.closed_occurrence_start == start:
        return None
    return guest


def guest_for_token(token):
    """The MeetingGuest this token names, whatever its state, or None.

    This is NOT the gate - it checks only that the token names a real guest,
    with no state check and no occurrence check. It exists so a WAITING guest
    can be told their own lobby status, which resolve_guest rejects by design.
    Anything reading meeting content must use resolve_guest instead.
    """
    from ..models import MeetingGuest

    digest = _hash_token_or_none(token)
    if digest is None:
        return None

    return (
        MeetingGuest.objects.select_related("meeting", "meeting__event")
        .filter(token_hash=digest)
        .first()
    )
