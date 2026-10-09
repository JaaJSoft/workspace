"""Text normalization shared by every substring search."""

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
