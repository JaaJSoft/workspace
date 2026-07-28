import re

KEY_MAX_LENGTH = 10
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
# WR-42 style; the key part is matched case-insensitively and normalized
# to uppercase by callers.
REFERENCE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,9})-([0-9]{1,9})$")
FALLBACK_KEY = "PROJ"


def generate_base_key(name):
    """Derive a display key from a project name.

    Multi-word names use word initials (up to 5), single words a 4-letter
    prefix. Names yielding fewer than 2 usable characters fall back to
    FALLBACK_KEY. The result always matches KEY_RE.
    """
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) >= 2:
        base = "".join(word[0] for word in words[:5])
    elif words:
        base = words[0][:4]
    else:
        base = ""
    # Keys must start with a letter, so leading digits are dropped.
    base = base.upper().lstrip("0123456789")
    if len(base) < 2:
        return FALLBACK_KEY
    return base[:KEY_MAX_LENGTH]


def unique_project_key(name, *, taken):
    """First key derived from *name* absent from *taken* (uppercase keys).

    Collisions get a numeric suffix (WR, WR2, WR3, ...), truncating the
    base so the result stays within KEY_MAX_LENGTH.
    """
    base = generate_base_key(name)
    if base not in taken:
        return base
    suffix = 2
    while True:
        digits = str(suffix)
        candidate = base[: KEY_MAX_LENGTH - len(digits)] + digits
        if candidate not in taken:
            return candidate
        suffix += 1
