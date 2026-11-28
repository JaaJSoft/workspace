"""Sign-in flows: the remote hands out the credentials once the user approves
Workspace in its own page, so nobody creates an app password by hand.

The browser only learns a flow's status and the page to open. The poll token
stays on the server, encrypted, and is dropped the moment the flow ends; the
credentials it brings back go straight into the connection.
"""

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables

from workspace.common.logging import scrub
from workspace.common.url_guard import check_remote_url

from ..errors import REPORTED_ERRORS, ImportsError
from ..models import ImportLoginFlow
from ..providers.base import ConnectionFailed
from .connections import create_connection, get_available_provider
from .network_policy import imports_network_policy

logger = logging.getLogger(__name__)

# How long Nextcloud keeps a Login Flow v2 open.
LIFETIME = timedelta(minutes=20)
# However often the page reads the flow, the remote is asked at most this often.
POLL_INTERVAL = timedelta(seconds=2)
# A flow is of no use once it expired; the margin spares a page still reading it.
PURGE_AFTER_EXPIRY = timedelta(hours=1)


class LoginFlowUnsupported(ImportsError):
    pass


def start_login_flow(owner, *, provider, label, base_url):
    """Open a flow on the remote and record it. Raises ``UnknownProvider``,
    ``LoginFlowUnsupported``, ``UnsafeUrl`` or ``ProviderError``; nothing is
    written in those cases."""
    prov = get_available_provider(provider)
    if not prov.login_flow:
        raise LoginFlowUnsupported(
            _("%(provider)s has no sign-in flow: connect it with a password.")
            % {"provider": prov.name}
        )
    base_url = prov.login_flow_base_url(base_url)
    check_remote_url(base_url, imports_network_policy())
    start = prov.start_login_flow(base_url)
    flow = ImportLoginFlow(
        owner=owner,
        provider=prov.slug,
        label=label,
        base_url=base_url,
        login_url=start.login_url,
        poll_endpoint=start.poll_endpoint,
        expires_at=timezone.now() + LIFETIME,
    )
    flow.set_poll_token(start.poll_token)
    flow.save()
    return flow


@sensitive_variables("grant")
def advance_login_flow(flow):
    """Ask the remote, at most once per ``POLL_INTERVAL``, whether the user
    approved the flow, and save the connection when they did. Returns the flow
    as it stands afterwards."""
    if flow.status != ImportLoginFlow.Status.PENDING:
        return flow
    now = timezone.now()
    if now >= flow.expires_at:
        return _finish(
            flow,
            ImportLoginFlow.Status.FAILED,
            error=_("The sign-in expired. Start it again."),
        )
    # The claim is the throttle and the lock at once: a read that loses it
    # answers the state as it is rather than asking the remote a second time.
    claimed = (
        ImportLoginFlow.objects.filter(
            pk=flow.pk, status=ImportLoginFlow.Status.PENDING
        )
        .filter(Q(polled_at__isnull=True) | Q(polled_at__lte=now - POLL_INTERVAL))
        .update(polled_at=now)
    )
    if not claimed:
        return flow
    try:
        prov = get_available_provider(flow.provider)
        grant = prov.poll_login_flow(
            flow.base_url, flow.poll_endpoint, flow.get_poll_token()
        )
    except ConnectionFailed as exc:
        # The remote may be away for a moment; the flow stays open until it
        # expires, and the next read asks again.
        logger.info(
            "Sign-in flow %s could not reach the remote: %s",
            flow.pk,
            scrub(exc.user_message),
        )
        return flow
    except REPORTED_ERRORS as exc:
        return _finish(flow, ImportLoginFlow.Status.FAILED, error=exc.user_message)
    if grant is None:
        return flow
    try:
        connection = create_connection(
            flow.owner,
            provider=flow.provider,
            label=flow.label,
            base_url=flow.base_url,
            username=grant.username,
            secret=grant.secret,
        )
    except REPORTED_ERRORS as exc:
        return _finish(flow, ImportLoginFlow.Status.FAILED, error=exc.user_message)
    return _finish(flow, ImportLoginFlow.Status.COMPLETED, connection=connection)


def _finish(flow, status, *, error="", connection=None):
    ImportLoginFlow.objects.filter(
        pk=flow.pk, status=ImportLoginFlow.Status.PENDING
    ).update(
        status=status, error=error, connection=connection, poll_token_encrypted=None
    )
    flow.status = status
    flow.error = error
    flow.connection = connection
    flow.poll_token_encrypted = None
    return flow


def discard_login_flow(flow):
    """Forget a flow the user gave up on. The remote lets it expire on its own."""
    flow.delete()


def purge_expired_login_flows():
    """Drop the poll token of every expired flow, whether or not anyone reads
    it again, then the rows expired past the margin. Answers how many rows
    were deleted."""
    now = timezone.now()
    ImportLoginFlow.objects.filter(
        expires_at__lte=now, poll_token_encrypted__isnull=False
    ).update(poll_token_encrypted=None)
    deleted, _per_model = ImportLoginFlow.objects.filter(
        expires_at__lt=now - PURGE_AFTER_EXPIRY
    ).delete()
    return deleted
