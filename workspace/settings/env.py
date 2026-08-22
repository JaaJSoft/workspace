"""Environment variable helpers shared by the settings modules.

Importing this module loads the ``.env`` file, so it must be the first thing
any settings submodule touches before reading ``os.getenv``.
"""

import os

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

# Values accepted as "true" in every boolean env var of the project.
_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_bool(name, default=False):
    """Read a boolean env var. Anything outside _TRUE_VALUES reads as False."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).lower() in _TRUE_VALUES


def env_list(name):
    """Read a comma-separated env var into a list of stripped, non-empty items."""
    raw = os.getenv(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_non_negative_int(name):
    """Read a non-negative integer env var, or ``None`` when it is unset.

    Refuses loudly where the project's other numeric settings simply let
    ``int()`` raise, because the settings read through here have to fail
    closed. A negative value is the reason: it does not fail at all, it
    quietly means something else.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    # isdigit() alone accepts superscripts that int() then rejects.
    if not (raw.isascii() and raw.isdigit()):
        raise ImproperlyConfigured(
            f"{name} must be a non-negative integer, got {raw!r}"
        )
    return int(raw)
