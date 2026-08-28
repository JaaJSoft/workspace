"""Utilities for UUID generation.

Expose a single helper `uuid_v7_or_v4` that returns a UUIDv7 when the
standard library provides it, and falls back to UUIDv4 otherwise.

This is intended to be used as a Django `default=` callable for
`models.UUIDField`, so it must be importable at module level.
"""

from __future__ import annotations

import uuid


def uuid_v7_or_v4() -> uuid.UUID:
    """Return a UUIDv7 if available in the stdlib, otherwise a UUIDv4.

    - Python ≥3.11/3.12 may provide `uuid.uuid7`.
    - Older versions or alternative runtimes will not; we fall back to v4.
    """
    try:
        gen = getattr(uuid, "uuid7", None)
        if callable(gen):
            return gen()
    except Exception:
        # In case accessing or calling uuid7 raises unexpectedly, ignore and fallback.
        pass
    return uuid.uuid4()


def parse_uuid_or_none(value) -> uuid.UUID | None:
    """Parse *value* as a UUID, returning None on malformed/missing input.

    Use at the view boundary so that a non-UUID query param or body field
    can be turned into a 4xx by the caller, rather than crashing deep in
    Django's UUIDField cleaning layer (which would surface as a 500).
    Accepts strings, existing UUID objects, or anything stringifiable.
    """
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError, TypeError:
        return None


class UuidBatchError(ValueError):
    """A malformed batch body. Its text is the 400 a view answers with."""


def parse_uuid_batch(data, *, key="uuids", max_items=200) -> list[uuid.UUID]:
    """Parse a ``{"<key>": [...]}`` batch body, or raise ``UuidBatchError``.

    The isinstance guard on *data* is the load-bearing one: a JSON body whose
    top level is not an object arrives as a list or an int, and reading a key
    off it raises AttributeError - a 500 where the schema promises a 400.
    """
    if not isinstance(data, dict):
        raise UuidBatchError(f"The body must be an object with a {key} list.")
    items = data.get(key, [])
    if not isinstance(items, list) or not items:
        raise UuidBatchError(f"{key} must be a non-empty list.")
    if len(items) > max_items:
        raise UuidBatchError(f"Too many UUIDs (max {max_items}).")
    parsed = []
    for item in items:
        value = parse_uuid_or_none(item)
        if value is None:
            raise UuidBatchError(f"Malformed UUID in {key}.")
        parsed.append(value)
    return parsed
