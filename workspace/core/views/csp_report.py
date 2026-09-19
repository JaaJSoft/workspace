import json
import logging
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import RequestDataTooBig
from django.views.decorators.debug import sensitive_variables
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


class CspReportIpThrottle(IpRateThrottle):
    scope = "core.csp_report.ip"


def _loggable(uri):
    """What is left of a refused URI once the parts that can carry a secret
    are gone. Every field of a report is written by the page that was refused,
    and nothing downstream of the logger would redact any of it.

    A query string or a fragment can carry a token, and the authority can
    carry credentials. An opaque URI - data:, javascript: - has no authority
    and keeps its payload where a hierarchical URL keeps a path, so only its
    scheme survives; a bare keyword (inline, eval, blob) parses as a path
    under no scheme and is left alone.
    """
    if not isinstance(uri, str):
        return ""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return ""
    if parts.scheme and not parts.netloc:
        return f"{parts.scheme}:"
    # Everything up to the last "@" is userinfo. Taken this way rather than
    # through parts.hostname, which lowercases the host, drops the port and
    # unwraps the brackets an IPv6 authority needs.
    authority = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, authority, parts.path, "", ""))


class CspReportView(APIView):
    # A report-uri request carries the page's cookies but never a CSRF token,
    # and SessionAuthentication enforces CSRF on any authenticated request.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CspReportIpThrottle]

    # The report body and the parsed report both hold the script sample, which
    # on a vault page is whatever the refused snippet was holding. It is never
    # logged; this keeps it out of the traceback locals too.
    @sensitive_variables()
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
        blocked_uri = _loggable(report.get("blocked-uri"))
        document_uri = _loggable(report.get("document-uri"))
        logger.warning(
            "CSP violation: %s refused %s on %s",
            scrub(directive[:MAX_LOGGED_DIRECTIVE]),
            scrub(blocked_uri[:MAX_LOGGED_URI]),
            scrub(document_uri[:MAX_LOGGED_URI]),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
