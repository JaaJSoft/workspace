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


def resolve_guest(token, now=None):
    """The MeetingGuest this token currently authorizes, or None.

    Rejects, in order: an unusable token, an unknown one, a guest that is not
    admitted, a meeting with no reachable occurrence right now, a guest holding
    a token for a different occurrence, and an occurrence the host has ended.
    """
    from ..models import MeetingGuest

    if not token or not isinstance(token, str):
        return None

    guest = (
        MeetingGuest.objects.select_related("meeting", "meeting__event")
        .filter(token_hash=hash_token(token))
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
