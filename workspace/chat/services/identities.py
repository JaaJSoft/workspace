"""One place that turns a member-or-guest row into something renderable.

A call participant and a chat message each carry exactly one of a user or a
meeting guest. Every read path needs the same three things out of that pair -
a display name, an optional numeric user id for the avatar, and a flag saying
which kind it is - so they resolve it here rather than each branching on
``author is None`` and drifting apart.
"""


def display_name_for_identity(user, guest):
    """Human label for a member or a guest."""
    if user is not None:
        return user.get_full_name() or user.username
    if guest is not None:
        return guest.display_name
    raise ValueError("an identity must be a user or a guest")


def identity_payload(user, guest):
    """Serialized identity. ``id`` is None for a guest, which has no user row."""
    display_name = display_name_for_identity(user, guest)
    if user is not None:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": display_name,
            "is_guest": False,
        }
    return {
        "id": None,
        # Callers that fall back to a username must still get a label.
        "username": display_name,
        "display_name": display_name,
        "is_guest": True,
    }
