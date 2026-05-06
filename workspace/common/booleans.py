"""Helpers for parsing boolean-like values from query strings or other
loosely-typed sources (form data, JSON-encoded URL params, headers).

Use :func:`is_truthy` whenever you need to coerce a string-or-None value
into a boolean. Plain Python truthiness (``if value:``) is wrong here:
non-empty strings like ``'false'`` or ``'0'`` evaluate to True, so a URL
like ``?unread=false`` would silently enable a filter that the user is
trying to disable.
"""

from rest_framework.fields import BooleanField


def is_truthy(value) -> bool:
    """Return True iff *value* represents a true-like boolean.

    Accepts the same true values as DRF's :class:`BooleanField`
    (``true``, ``1``, ``yes``, ``on``, ``t``, ``y``, case-insensitive)
    so the parser stays in sync with what serializers accept. Permissive:
    None, empty string, the false-values, and any unrecognized string all
    yield ``False`` (no exception). For a strict variant that rejects
    garbage with a 400, use a serializer with ``BooleanField`` instead.
    """
    if value is None:
        return False
    return str(value).lower() in BooleanField.TRUE_VALUES
