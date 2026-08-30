"""A character ceiling that extraction can check as it goes.

Document extractors are handed a ceiling by their caller, and the cheap way to
honour it is to extract everything and slice the result. That is exactly what
the ceiling exists to avoid: a thousand-slide deck costs a thousand parses
before the first character is thrown away. A running total lets the extractor
stop at the part, page or paragraph that fills it.
"""

from __future__ import annotations


class TextBudget:
    """Collects text until *max_chars* is reached, then accepts no more."""

    def __init__(self, max_chars: int):
        self._max_chars = max(max_chars, 0)
        self._parts: list[str] = []
        self._length = 0

    @property
    def full(self) -> bool:
        return self._length >= self._max_chars

    def add(self, text: str, *, separator: str = "") -> None:
        """Append as much of *text* as fits, preceded by *separator*.

        The separator only goes in when there is something in front of it and
        room for both, so the result never ends on a dangling newline.
        """
        if self.full or not text:
            return
        chunk = (separator if self._parts else "") + text
        self._parts.append(chunk[: self._max_chars - self._length])
        self._length += len(self._parts[-1])

    def text(self) -> str:
        return "".join(self._parts)
