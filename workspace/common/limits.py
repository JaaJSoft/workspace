"""Parsing for user-supplied result limits."""


def clamp_limit(value, default: int = 10, maximum: int = 50) -> int:
    """Coerce *value* (query param, tool argument) into ``[1, maximum]``.

    Permissive like :func:`workspace.common.booleans.is_truthy`: anything that
    isn't an integer falls back to *default* rather than raising, so a
    malformed ``?limit=`` doesn't 400 a search endpoint.
    """
    try:
        limit = int(value)
    except TypeError, ValueError:
        limit = default
    return max(1, min(limit, maximum))
