"""Endpoints around the instance's own mail: the provider's bounce webhooks,
the unsubscribe link a recipient follows, and the admin's test mail."""

import functools
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from workspace.common.logging import scrub
from workspace.core.models import EmailSuppression
from workspace.core.services import admin_dashboard
from workspace.core.services.admin_dashboard import test_email_unavailable_reason
from workspace.core.services.email import suppress_address, unsubscribe_target

logger = logging.getLogger(__name__)


def requires_webhook_secret(view):
    """Answer 404 until ANYMAIL_WEBHOOK_SECRET is set.

    Anymail only warns when a webhook has no basic auth, and serves it
    anyway: anyone could then report any address as bounced and silence the
    instance's mail to it.
    """

    @functools.wraps(view)
    def guarded(request, *args, **kwargs):
        if not settings.ANYMAIL.get("WEBHOOK_SECRET"):
            raise Http404
        return view(request, *args, **kwargs)

    return guarded


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
