from django.conf import settings
from django.utils.translation import gettext as _

from workspace.common.url_guard import NetworkPolicy


def imports_network_policy() -> NetworkPolicy:
    """Where an import may connect. Self-hosters whose previous cloud lives on
    the LAN open it with IMPORTS_ALLOW_PRIVATE_NETWORKS or list the host in
    IMPORTS_ALLOWED_HOSTS. Built per call, so the hint is in the language of
    whoever reads the refusal."""
    return NetworkPolicy(
        allow_private_networks=settings.IMPORTS_ALLOW_PRIVATE_NETWORKS,
        allowed_hosts=settings.IMPORTS_ALLOWED_HOSTS,
        private_network_hint=_(
            "Ask the administrator to allow private networks for imports if "
            "your previous cloud lives there."
        ),
    )
