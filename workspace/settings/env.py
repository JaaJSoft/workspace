"""Environment variable helpers shared by the settings modules.

Importing this module loads the ``.env`` file, so it must be the first thing
any settings submodule touches before reading ``os.getenv``.
"""

import os

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
