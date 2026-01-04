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
        # hasattr is slightly faster than try/except on call,
        # but keep both to be extra safe.
        gen = getattr(uuid, "uuid7", None)
        if callable(gen):
            return gen()
    except Exception:
        # In case accessing or calling uuid7 raises unexpectedly, ignore and fallback.
        pass
    return uuid.uuid4()
