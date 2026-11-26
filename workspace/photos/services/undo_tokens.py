"""What a batch of corrections changed, kept a few minutes under a random token."""

import secrets

from django.core.cache import cache

UNDO_TTL = 10 * 60


class UndoExpired(Exception):
    """The token names nothing: too old, already used, or someone else's."""


def _key(kind, user, token):
    return f"photos:{kind}:undo:{user.pk}:{token}"


def save_state(kind, user, state):
    """Keep *state* for *user*; return the token that takes it back."""
    token = secrets.token_urlsafe(16)
    cache.set(_key(kind, user, token), state, UNDO_TTL)
    return token


def take_state(kind, user, token):
    """The state *token* names, which no later call can take again."""
    key = _key(kind, user, token)
    state = cache.get(key)
    if state is None:
        raise UndoExpired
    cache.delete(key)
    return state


def drop_state(kind, user, token):
    cache.delete(_key(kind, user, token))
