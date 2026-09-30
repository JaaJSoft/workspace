"""Endpoints around the instance's own mail: the bounce webhook the relay
calls, the unsubscribe link a recipient follows, and the admin's test mail."""

import base64
import binascii
import hmac
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.logging import scrub
from workspace.core.models import EmailSuppression
from workspace.core.services import admin_dashboard
from workspace.core.services.admin_dashboard import test_email_unavailable_reason
from workspace.core.services.email import (
    record_bounce,
    suppress_address,
    unsubscribe_target,
)
from workspace.vault.throttling import IpRateThrottle

logger = logging.getLogger(__name__)

_SUPPRESSING_EVENTS = {
    "hard_bounce": EmailSuppression.Reason.HARD_BOUNCE,
    "complaint": EmailSuppression.Reason.COMPLAINT,
}
# Soft bounces are the relay's to retry; a delivery report needs nothing.
_EVENT_TYPES = (*_SUPPRESSING_EVENTS, "soft_bounce", "delivered")


class BounceEventSerializer(serializers.Serializer):
    address = serializers.EmailField()
    type = serializers.ChoiceField(choices=_EVENT_TYPES)
    message_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    detail = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class EmailBounceIpThrottle(IpRateThrottle):
    scope = "core.email_bounce.ip"


@sensitive_variables("expected", "presented", "credentials", "decoded")
def _authorized(request):
    """The relay presents EMAIL_BOUNCE_WEBHOOK_TOKEN as a bearer token, or as
    the password of HTTP basic auth - what an SNS subscription or a relay
    that only takes a URL with credentials can send."""
    expected = settings.EMAIL_BOUNCE_WEBHOOK_TOKEN
    scheme, _, credentials = request.META.get("HTTP_AUTHORIZATION", "").partition(" ")
    presented = ""
    if scheme.lower() == "bearer":
        presented = credentials.strip()
    elif scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(credentials.strip(), validate=True).decode()
        except binascii.Error, UnicodeDecodeError:
            return False
        presented = decoded.partition(":")[2]
    return bool(presented) and hmac.compare_digest(
        presented.encode(), expected.encode()
    )


class EmailBounceWebhookView(APIView):
    """Hard bounces and complaints reported by the relay.

    Takes one event or a list of them. Hard bounces and complaints suppress
    the address for every future mail; soft bounces are acknowledged and
    dropped, since the relay retries those itself.
    """

    # The relay has no session, and SessionAuthentication would demand a CSRF
    # token it cannot have. The shared secret is the authentication.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [EmailBounceIpThrottle]

    # Called by the relay, not by clients of the API: documented in
    # docs/guides/email.md rather than in the schema.
    @extend_schema(exclude=True)
    def post(self, request):
        if not settings.EMAIL_BOUNCE_WEBHOOK_TOKEN:
            raise Http404
        if not _authorized(request):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        events = request.data if isinstance(request.data, list) else [request.data]
        serializer = BounceEventSerializer(data=events, many=True)
        if not serializer.is_valid():
            return Response(
                {"detail": "Invalid bounce event."}, status=status.HTTP_400_BAD_REQUEST
            )

        suppressed = ignored = 0
        for event in serializer.validated_data:
            reason = _SUPPRESSING_EVENTS.get(event["type"])
            if reason is None:
                ignored += 1
                continue
            record_bounce(
                event["address"],
                reason,
                message_id=event.get("message_id", ""),
                detail=event.get("detail", ""),
            )
            suppressed += 1
        return Response({"suppressed": suppressed, "ignored": ignored})


# A mail client performing RFC 8058 one-click unsubscribe POSTs without a
# session or a CSRF token; the signed token in the URL is what authorizes it.
# A GET only shows the confirmation - link scanners fetch every URL in a mail,
# and must not unsubscribe anybody by doing so.
@csrf_exempt
@require_http_methods(["GET", "POST"])
def unsubscribe(request, token):
    target = unsubscribe_target(token)
    if target is None:
        raise Http404
    address, feature = target
    done = request.method == "POST"
    if done:
        suppress_address(address, EmailSuppression.Reason.UNSUBSCRIBED, feature=feature)
        logger.info("Email %s unsubscribed from %s", scrub(address), scrub(feature))
    return render(
        request,
        "core/email/unsubscribe_page.html",
        {
            "address": address,
            "feature_label": feature.replace("_", " "),
            "done": done,
        },
    )


@staff_member_required
@require_POST
def send_test_email(request):
    """Send the administrator a test mail; the admin index shows how it went."""
    reason = test_email_unavailable_reason(request.user)
    if reason is not None:
        messages.error(request, reason)
        return redirect("admin:index")

    delivery = admin_dashboard.send_test_email(request.user)
    if delivery.status == delivery.Status.QUEUED:
        messages.success(
            request,
            f"Test email queued for {delivery.to_address}. Reload to see the outcome.",
        )
    else:
        messages.warning(request, f"Test email not sent: {delivery.error}")
    return redirect("admin:index")
