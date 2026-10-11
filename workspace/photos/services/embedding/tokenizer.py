"""CLIP's byte-pair tokenizer, without torch or ftfy.

The text encoder reads token ids, so a query must be cut exactly as the model
was trained on: OpenAI's ``simple_tokenizer.py`` (MIT), reading the same
merges file. ``ftfy.fix_text`` is left out of the cleaning: it repairs
mojibake, which a query typed in a search box does not carry.
"""

from __future__ import annotations

import gzip
import html
from functools import cache

import regex

START_OF_TEXT = "<|startoftext|>"
END_OF_TEXT = "<|endoftext|>"
CONTEXT_LENGTH = 77

# The merges file opens with a version line, and the vocabulary is 49152
# tokens: 256 bytes, the same 256 ending a word, the merges, and the two
# markers.
_MERGE_COUNT = 49152 - 256 - 2

_WORD_RE = regex.compile(
    r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+""",
    regex.IGNORECASE,
)


@cache
def _byte_symbols():
    """A printable character per byte, as GPT-2 and CLIP map them: the BPE
    works on characters, and a raw space or control byte would break it."""
    printable = [
        *range(ord("!"), ord("~") + 1),
        *range(ord("¡"), ord("¬") + 1),
        *range(ord("®"), ord("ÿ") + 1),
    ]
    symbols = printable[:]
    extra = 0
    for byte in range(256):
        if byte not in printable:
            printable.append(byte)
            symbols.append(256 + extra)
            extra += 1
    return dict(zip(printable, map(chr, symbols), strict=True))


def _pairs(word):
    return set(zip(word, word[1:], strict=False))


class ClipTokenizer:
    def __init__(self, merges):
        """*merges*: the lines of the merges file after its version line."""
        merges = [tuple(line.split()) for line in merges[:_MERGE_COUNT]]
        symbols = list(_byte_symbols().values())
        vocab = symbols + [s + "</w>" for s in symbols]
        vocab += ["".join(merge) for merge in merges]
        vocab += [START_OF_TEXT, END_OF_TEXT]
        self.encoder = {token: i for i, token in enumerate(vocab)}
        self.ranks = {merge: i for i, merge in enumerate(merges)}
        self._cache = {}

    @classmethod
    def from_file(cls, path):
        """The tokenizer of the gzipped merges file at *path*."""
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        return cls(lines[1:])

    def _bpe(self, token):
        cached = self._cache.get(token)
        if cached is not None:
            return cached
        word = (*token[:-1], token[-1] + "</w>")
        pairs = _pairs(word)
        while pairs:
            bigram = min(pairs, key=lambda pair: self.ranks.get(pair, float("inf")))
            if bigram not in self.ranks:
                break
            first, second = bigram
            merged = []
            i = 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == first and word[i + 1] == second:
                    merged.append(first + second)
                    i += 2
                else:
                    merged.append(word[i])
                    i += 1
            word = tuple(merged)
            pairs = _pairs(word)
        self._cache[token] = word
        return word

    def encode(self, text):
        """The token ids of *text*, without the start and end markers."""
        text = html.unescape(html.unescape(text)).strip()
        text = regex.sub(r"\s+", " ", text).strip().lower()
        byte_symbols = _byte_symbols()
        ids = []
        for token in _WORD_RE.findall(text):
            token = "".join(byte_symbols[b] for b in token.encode("utf-8"))
            ids += [self.encoder[piece] for piece in self._bpe(token)]
        return ids

    def __call__(self, text, context_length=CONTEXT_LENGTH):
        """*text* as the text encoder reads it: ``(input_ids, attention_mask)``,
        each *context_length* ints, the markers included and the text cut to
        fit."""
        ids = [self.encoder[START_OF_TEXT], *self.encode(text)]
        ids = ids[: context_length - 1] + [self.encoder[END_OF_TEXT]]
        padding = [0] * (context_length - len(ids))
        return ids + padding, [1] * len(ids) + padding
