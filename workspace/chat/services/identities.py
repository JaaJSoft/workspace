"""One place that turns a member-or-guest row into something renderable.

A call participant and a chat message each carry exactly one of a user or a
meeting guest. Every read path needs the same four things out of that pair -
a display name, an optional numeric user id for the avatar, the participant
key it is addressed by, and a flag saying which kind it is - so they resolve
it here rather than each branching on ``author is None`` and drifting apart.
"""

from .participant_keys import guest_key, user_key


def display_name_for_identity(user, guest):
    """Human label for a member or a guest."""
    if user is not None:
        return user.get_full_name() or user.username
    if guest is not None:
        return guest.display_name
    raise ValueError("an identity must be a user or a guest")


def identity_payload(user, guest):
    """Serialized identity. ``id`` is None for an unbound guest, which has no
    user row; a guest who knocked while signed in carries theirs.

    ``participant_key`` is the one field both halves always have: it is what
    a call tile is addressed by, so a reader holding its own key can tell
    its own messages from everyone else's without comparing display names.
    """
    display_name = display_name_for_identity(user, guest)
    if user is not None:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": display_name,
            "is_guest": False,
            "participant_key": user_key(user.id),
        }
    return {
        # A bound guest carries the account so the avatar resolves; an
        # anonymous one has nothing to resolve.
        "id": guest.user_id,
        "username": guest.user.username if guest.user_id else display_name,
        "display_name": display_name,
        "is_guest": True,
        "participant_key": guest_key(guest.uuid),
    }
