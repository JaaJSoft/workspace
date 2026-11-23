"""Which servers mail may connect to."""

from django.conf import settings
from django.utils.translation import gettext as _

from workspace.common.url_guard import NetworkPolicy, check_resolved_host


def mail_network_policy() -> NetworkPolicy:
    """Where mail may connect. A server on the LAN is opened by the
    administrator, with MAIL_ALLOW_PRIVATE_NETWORKS or by listing it in
    MAIL_ALLOWED_HOSTS; the servers of the generic OAuth2 provider are the
    administrator's choice already. Built per call, so the hint is in the
    language of whoever reads the refusal."""
    trusted = (
        *settings.MAIL_ALLOWED_HOSTS,
        settings.OAUTH_GENERIC_IMAP_HOST,
        settings.OAUTH_GENERIC_SMTP_HOST,
    )
    return NetworkPolicy(
        allow_private_networks=settings.MAIL_ALLOW_PRIVATE_NETWORKS,
        allowed_hosts=frozenset(host for host in trusted if host),
        private_network_hint=_(
            "Ask the administrator to allow private networks for mail if your "
            "mail server lives there."
        ),
    )


def check_mail_host(host):
    """Raise ``UnsafeUrl`` when mail may not connect to *host*."""
    check_resolved_host(host, mail_network_policy())
