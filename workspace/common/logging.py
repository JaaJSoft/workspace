"""Helpers for safely logging values that may include user-controlled data.

Use :func:`scrub` on every value taken from a request (body, headers, path,
query string), a database row that originated in user input (e.g. a stored
push-subscription endpoint, a filename, a free-text title), or a third-party
API response, before passing it to a ``logger.X(...)`` call. This prevents
log injection (CWE-117) — an attacker can otherwise embed ``\\r\\n`` in their
input and forge fake log lines, confusing operators and SIEM parsers.

The exact ``str(...).replace('\\r', '').replace('\\n', '')`` chain below is
the pattern CodeQL recognizes as a sanitizer for ``py/log-injection``; do
not refactor the replaces out into a separate function or the static
analyzer will lose track of the taint cleansing.
"""


def scrub(value):
    """Return ``value`` with CR/LF stripped, safe to put in a log entry."""
    return str(value).replace('\r', '').replace('\n', '')
