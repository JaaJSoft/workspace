"""Cutting a document too long for one result into parts a reader can ask for.

What lies past the cut is reachable only if the parts are fixed: the same call
has to return the same stretch of the document whether the reader walks to it
or jumps straight at it, so a part is a slice of a settled width and never a
share of what happens to be left.
"""


def part_count(length: int, size: int) -> int:
    """How many parts of *size* a document of *length* holds — at least one."""
    return max(1, -(-length // size))


def check_part(part: int, total: int) -> None:
    """Refuse a part the document does not have, naming the ones it does.

    Returning the last part instead would have the reader take a stretch it
    has already read for the one that follows it.
    """
    if not 1 <= part <= total:
        raise ValueError(
            f"This page has {total} part{'s' if total > 1 else ''} — "
            f"there is no part {part}."
        )
