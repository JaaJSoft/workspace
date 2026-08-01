import re

from ..models import Project

KEY_MAX_LENGTH = 10
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
# WR-42 style; the key part is matched case-insensitively and normalized
# to uppercase by callers.
REFERENCE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,9})-([0-9]{1,9})$")
FALLBACK_KEY = "PROJ"
PERSONAL_KEY_PREFIX = "PERS"


def _word_signature(text):
    """Uppercase initials of the first 5 words, or a 4-letter prefix.

    Empty when *text* holds no ASCII alphanumerics (accented-only names
    included - callers decide what to fall back to).
    """
    words = re.findall(r"[A-Za-z0-9]+", text)
    if len(words) >= 2:
        return "".join(word[0] for word in words[:5]).upper()
    if words:
        return words[0][:4].upper()
    return ""


def generate_base_key(name):
    """Derive a display key from a project name.

    Multi-word names use word initials (up to 5), single words a 4-letter
    prefix. Names yielding fewer than 2 usable characters fall back to
    FALLBACK_KEY. The result always matches KEY_RE.
    """
    # Keys must start with a letter, so leading digits are dropped.
    base = _word_signature(name).lstrip("0123456789")
    if len(base) < 2:
        return FALLBACK_KEY
    return base[:KEY_MAX_LENGTH]


def personal_key_base(username):
    """Derive a personal project's key from its owner's username (PERSPC).

    The constant prefix supplies the leading letter KEY_RE demands, so a
    digit-only username stays valid and one with no ASCII alphanumerics
    degrades to the prefix alone instead of failing validation. It also
    bounds the result: _word_signature caps at 5 characters, so the key
    always fits KEY_MAX_LENGTH.

    Distinct users can still collide (pierre.chopinet and paul.charpentier
    both yield PERSPC); unique_project_key arbitrates.
    """
    return PERSONAL_KEY_PREFIX + _word_signature(username)


def unique_project_key(base, *, taken):
    """First key from *base* absent from *taken* (uppercase keys).

    Collisions get a numeric suffix (WR, WR2, WR3, ...), truncating the
    base so the result stays within KEY_MAX_LENGTH.
    """
    if base not in taken:
        return base
    suffix = 2
    while True:
        digits = str(suffix)
        candidate = base[: KEY_MAX_LENGTH - len(digits)] + digits
        if candidate not in taken:
            return candidate
        suffix += 1


def allocate_task_number(project):
    """Reserve and return the next task number for *project*.

    Locks the project row; must run inside a transaction and before any
    status or task row locks (writers take locks project -> status, which
    keeps the ordering deadlock-free). The counter is monotone: numbers
    are never reused after a task is deleted.
    """
    locked = Project.objects.select_for_update().get(pk=project.pk)
    number = locked.next_task_number
    locked.next_task_number = number + 1
    locked.save(update_fields=["next_task_number"])
    project.next_task_number = locked.next_task_number
    return number
