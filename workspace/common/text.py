"""Text normalization: substring search, caseless name matching."""

import unicodedata


def fold_text(text):
    """*text* without accents and case: ``"Hélène"`` and ``"HELENE"`` both give
    ``"helene"``.

    A search stores the folded haystack and folds the needle the same way, so
    a query typed without accents (or on a keyboard that has none) still finds
    the accented name. NFKD also splits compatibility forms (``"ﬁ"`` gives
    ``"fi"``), and ``casefold`` goes further than ``lower`` (``"ß"`` gives
    ``"ss"``), so the result can be longer than the input.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def caseless(text):
    """*text* folded for Unicode canonical caseless matching.

    Two strings fold alike when they differ only in case or in how their
    accents are encoded (``"É"`` as one code point or as ``"E"`` plus a
    combining accent), which is what a case-insensitive file system that
    ignores normalization takes for one name. Accents still count, unlike
    :func:`fold_text`. The result can be longer than the input.
    """
    decomposed = unicodedata.normalize("NFD", text or "")
    return unicodedata.normalize("NFC", decomposed.casefold())
