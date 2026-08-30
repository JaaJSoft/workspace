"""A bounded reader over a file blob.

Streaming a multi-gigabyte upload to a virus scanner is worse than not
scanning it, so the task caps what it sends. The cap is ours rather than the
daemon's: clamd's StreamMaxLength is server-side configuration this
application does not control.
"""

from __future__ import annotations


class CappedReader:
    """Wrap *stream*, yielding at most *max_bytes* and reporting truncation.

    ``truncated`` becomes True only when the source genuinely had more to
    give, which is what lets the caller tell "scanned in full and clean" from
    "clean as far as we looked".
    """

    def __init__(self, stream, max_bytes):
        self._stream = stream
        self._remaining = max(0, int(max_bytes))
        self.truncated = False

    def read(self, size=-1):
        if self._remaining <= 0:
            return b""
        want = self._remaining
        if size is not None and size >= 0:
            want = min(size, self._remaining)
        chunk = self._stream.read(want)
        self._remaining -= len(chunk)
        if self._remaining <= 0:
            # One byte past the cap answers "was there more?" without pulling
            # another block into memory. The byte is discarded: a truncated
            # scan cannot be trusted clean regardless of what follows.
            self.truncated = bool(self._stream.read(1))
        return chunk
