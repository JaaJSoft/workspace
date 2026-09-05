"""Who a message list is being rendered for.

The member pane and the public meeting page render the same templates, so the
templates ask a viewer what it may do instead of dereferencing
``request.user``: a meeting guest has no user row, and asking one for ``.id``
would crash the page a stranger loads.

Two things live here. ``participant_key`` is the identity the grouping
compares a message against to decide whether it is the reader's own - the same
opaque key the call layer routes by, so a guest and a member are told apart by
one comparison rather than by two branches. The capability flags are the
render-time half of authorization: a guest reaches the chat through the
``/api/v1/chat/meet/<slug>/`` endpoints, which accept a message and nothing
else, so every control that would call a member-only endpoint is simply not
drawn. They are a UI contract, never the fence - each endpoint still checks
its own caller.
"""

from dataclasses import dataclass

from ..services.participant_keys import guest_key, user_key


@dataclass(frozen=True)
class Viewer:
    participant_key: str
    # None for a guest, which has no user row - templates must not print it.
    user_id: int | None = None
    is_guest: bool = False
    can_react: bool = False
    can_pin: bool = False
    can_edit_own: bool = False
    can_delete_own: bool = False
    can_thread: bool = False
    can_see_receipts: bool = False


def for_user(user):
    """A member: every capability the chat pane has always offered."""
    return Viewer(
        participant_key=user_key(user.id),
        user_id=user.id,
        is_guest=False,
        can_react=True,
        can_pin=True,
        can_edit_own=True,
        can_delete_own=True,
        can_thread=True,
        can_see_receipts=True,
    )


def for_guest(guest):
    """A meeting guest: replying is the only thing their endpoints accept."""
    return Viewer(participant_key=guest_key(guest.uuid), is_guest=True)


def message_participant_key(message):
    """The participant key the message's author is addressed by.

    A message carries exactly one of an author or a guest; anything else is a
    row that predates that invariant, and it gets a key nobody can match
    rather than an exception on a read path.
    """
    if message.author_id is not None:
        return user_key(message.author_id)
    if message.guest_id is not None:
        return guest_key(message.guest_id)
    return ""
