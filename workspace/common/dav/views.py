"""Base class for DAV endpoints served as Django views.

Authentication is HTTP Basic only (see ``common.dav.auth``): DAV clients do
not carry a session cookie, and a CSRF token makes no sense for them, so the
views are CSRF-exempt and never fall back to the session user.
"""

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .auth import authenticate_basic, basic_credentials
from .xml import DavError

DAV_METHODS = (
    "propfind",
    "proppatch",
    "report",
    "mkcol",
    "copy",
    "move",
    "lock",
    "unlock",
)


@method_decorator(csrf_exempt, name="dispatch")
class DavView(View):
    """A DAV resource. Subclasses implement the methods they support.

    ``dav_compliance`` is the ``DAV`` header OPTIONS advertises: the
    compliance classes and the extensions (``calendar-access``...) clients
    probe for before trusting a server with their data.
    """

    http_method_names = [*View.http_method_names, *DAV_METHODS]
    realm = "Workspace"
    dav_compliance = "1, 3"

    def dispatch(self, request, *args, **kwargs):
        credentials = basic_credentials(request.headers.get("Authorization"))
        user = authenticate_basic(*credentials) if credentials else None
        if user is None:
            response = HttpResponse("Authentication required.", status=401)
            response["WWW-Authenticate"] = (
                f'Basic realm="{self.realm}", charset="UTF-8"'
            )
            return response
        request.user = user
        try:
            return super().dispatch(request, *args, **kwargs)
        except DavError as exc:
            return exc.response()

    def options(self, request, *args, **kwargs):
        response = HttpResponse()
        response["DAV"] = self.dav_compliance
        response["Allow"] = ", ".join(self._allowed_methods())
        response["Content-Length"] = "0"
        return response

    def http_method_not_allowed(self, request, *args, **kwargs):
        response = HttpResponse(status=405)
        response["Allow"] = ", ".join(self._allowed_methods())
        return response


def depth(request, default="infinity"):
    """The ``Depth`` header as ``"0"``, ``"1"`` or ``"infinity"``."""
    value = request.headers.get("Depth", default).strip().lower()
    if value not in ("0", "1", "infinity"):
        raise DavError(400, message="Invalid Depth header.")
    return value


def etag_matches(header, etag):
    """True when an ``If-Match`` / ``If-None-Match`` value names *etag*.

    ``*`` matches any existing resource (*etag* not None). Weak validators
    are compared as strong ones: DAV clients send back exactly what they got.
    """
    if etag is None:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.removeprefix("W/") == etag:
            return True
    return False


def check_preconditions(request, etag):
    """Enforce ``If-Match`` / ``If-None-Match`` against the current *etag*.

    *etag* is None when the resource does not exist. Raises a 412 DavError on
    a failed precondition - the lost-update guard every sync client relies on.
    """
    if_match = request.headers.get("If-Match")
    if if_match is not None and not etag_matches(if_match, etag):
        raise DavError(412)
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None and etag_matches(if_none_match, etag):
        raise DavError(412)
