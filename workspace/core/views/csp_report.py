import json
import logging
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import RequestDataTooBig
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.logging import scrub
from workspace.vault.throttling import IpRateThrottle

logger = logging.getLogger("workspace.core.csp_report")

MAX_REPORT_BYTES = 8192
MAX_LOGGED_DIRECTIVE = 128
MAX_LOGGED_URI = 512

REPORT_CONTENT_TYPES = {"application/csp-report", "application/json"}

# What a browser writes in blocked-uri when the refused thing was not a URL.
URI_KEYWORDS = {
    "inline",
    "eval",
    "wasm-eval",
    "self",
    "data",
    "blob",
    "trusted-types-policy",
    "trusted-types-sink",
}


class CspReportIpThrottle(IpRateThrottle):
    scope = "core.csp_report.ip"


def _without_query(uri):
    """Scheme, host and path only: a query string or a fragment can carry a
    token, and nothing downstream of the logger would redact it."""
    if not isinstance(uri, str):
        return ""
    if uri in URI_KEYWORDS:
        return uri
    try:
        parts = urlsplit(uri)
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class CspReportView(APIView):
    # A report-uri request carries the page's cookies but never a CSRF token,
    # and SessionAuthentication enforces CSRF on any authenticated request.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CspReportIpThrottle]

    @extend_schema(exclude=True)
    def post(self, request):
        content_type = request.content_type.split(";")[0].strip().lower()
        if content_type not in REPORT_CONTENT_TYPES:
            return Response(status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        # Under WSGI (gunicorn), request.body reads at most Content-Length
        # bytes from the socket, so refusing on the declared length keeps an
        # oversized report from being buffered at all.
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        if length > MAX_REPORT_BYTES:
            return Response(status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        # That length is the client's word for it. ASGI reads the stream
        # whole whatever the header claims - or omits - and Django's own
        # DATA_UPLOAD_MAX_MEMORY_SIZE guard reads the same header, so the
        # bytes that arrived are what the cap has to be measured against.
        try:
            body = request.body
        except RequestDataTooBig:
            return Response(status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if len(body) > MAX_REPORT_BYTES:
            return Response(status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        try:
            report = json.loads(body)["csp-report"]
        except ValueError, KeyError, TypeError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(report, dict):
            return Response(status=status.HTTP_400_BAD_REQUEST)

        directive = str(
            report.get("effective-directive") or report.get("violated-directive") or ""
        )
        blocked_uri = _without_query(report.get("blocked-uri"))
        document_uri = _without_query(report.get("document-uri"))
        logger.warning(
            "CSP violation: %s refused %s on %s",
            scrub(directive[:MAX_LOGGED_DIRECTIVE]),
            scrub(blocked_uri[:MAX_LOGGED_URI]),
            scrub(document_uri[:MAX_LOGGED_URI]),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
