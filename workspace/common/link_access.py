"""Remember that a visitor unlocked a password-protected public link.

The right password earns a signed token. It lives in an HttpOnly cookie and
never in a URL: a query string lands in access logs, in the browser history
and in the Referer of every request the page makes, and redaction reaches
none of them.
"""

import hashlib


def access_cookie_name(scope, link_token):
    """The cookie of one link, named after a digest rather than the token."""
    digest = hashlib.sha256(link_token.encode()).hexdigest()[:24]
    return f"{scope}_{digest}"


def set_access_cookie(response, request, name, value, max_age):
    """Hand the visitor *value* under *name* for *max_age* seconds.

    Lax keeps it off cross-site POSTs, so a page elsewhere cannot write
    through a link the visitor unlocked.
    """
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=request.is_secure(),
        samesite="Lax",
    )
