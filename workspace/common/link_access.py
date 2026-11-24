"""Remember that a visitor unlocked a password-protected public link.

The right password earns a signed token. It lives in an HttpOnly cookie and
never in a URL: a query string lands in access logs, in the browser history
and in the Referer of every request the page makes, and redaction reaches
none of them.
"""

import hashlib

from django.utils.cache import patch_cache_control, patch_vary_headers
from rest_framework.authentication import CSRFCheck


def access_cookie_name(scope, link_token):
    """The cookie of one link, named after a digest rather than the token."""
    digest = hashlib.sha256(link_token.encode()).hexdigest()[:24]
    return f"{scope}_{digest}"


def set_access_cookie(response, request, name, value, max_age):
    """Hand the visitor *value* under *name* for *max_age* seconds.

    Lax keeps it off cross-site POSTs. A same-site page still sends it, so a
    write it authorizes also checks ``csrf_failure``.
    """
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=request.is_secure(),
        samesite="Lax",
    )


def keep_private(response):
    """Keep a response the access cookie unlocked out of every shared cache.

    The URL is the same with and without the cookie, so a cache keyed on it
    alone would hand the unlocked bytes to the next visitor.
    """
    patch_cache_control(response, private=True)
    patch_vary_headers(response, ["Cookie"])
    return response


def csrf_failure(request):
    """Why *request* fails Django's CSRF check, or None when it passes.

    A write the access cookie authorizes needs it: the browser sends the
    cookie on any same-site request, a sibling subdomain's form included.
    """
    check = CSRFCheck(lambda request: None)
    check.process_request(request)
    return check.process_view(request, None, (), {})
