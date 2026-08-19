"""Connection lifecycle: create, re-test, update, browse.

Nothing is persisted before the remote has answered once with the given
credentials - a connection row always describes something that worked.
"""

import logging

from django.utils import timezone

from workspace.common.logging import scrub

from ..models import ImportConnection
from ..providers.base import ProviderError
from ..providers.registry import provider_registry
from .url_guard import check_remote_url

logger = logging.getLogger(__name__)


class UnknownProvider(ValueError):
    pass


def get_available_provider(slug: str):
    provider = provider_registry.get(slug)
    if provider is None or not provider.is_available():
        raise UnknownProvider(f"Unknown provider '{slug}'.")
    return provider


def create_connection(owner, *, provider, label, base_url, username, secret):
    """Build, verify and save a credentials-based connection.

    Raises ``UnknownProvider``, ``UnsafeUrl`` or ``ProviderError``; nothing is
    written in those cases.
    """
    prov = get_available_provider(provider)
    connection = ImportConnection(
        owner=owner,
        provider=prov.slug,
        label=label,
        base_url=prov.normalize_base_url(base_url, username),
        username=username,
    )
    connection.set_secret(secret)
    check_remote_url(connection.base_url)
    connection.capabilities = prov.test_connection(connection)
    connection.last_checked_at = timezone.now()
    connection.save()
    return connection


def update_connection(
    connection, *, label=None, base_url=None, username=None, secret=None
):
    """Apply the given fields; any credential change is verified against the
    remote before it is saved (the label alone is not)."""
    prov = get_available_provider(connection.provider)
    credentials_changed = False
    if label is not None:
        connection.label = label
    if username is not None:
        connection.username = username
        credentials_changed = True
    if base_url is not None:
        credentials_changed = True
    if secret is not None:
        connection.set_secret(secret)
        credentials_changed = True
    if credentials_changed:
        connection.base_url = prov.normalize_base_url(
            base_url or connection.base_url, connection.username
        )
        check_remote_url(connection.base_url)
        connection.capabilities = prov.test_connection(connection)
        connection.last_checked_at = timezone.now()
        connection.last_error = ""
    connection.save()
    return connection


def test_connection(connection):
    """Re-check a stored connection, refreshing its capabilities; the error, if
    any, is both recorded on the row and re-raised."""
    prov = get_available_provider(connection.provider)
    try:
        check_remote_url(connection.base_url)
        connection.capabilities = prov.test_connection(connection)
    except (ProviderError, ValueError) as exc:
        connection.last_error = str(exc)
        connection.last_checked_at = timezone.now()
        connection.save(update_fields=["last_error", "last_checked_at", "updated_at"])
        logger.info(
            "Import connection %s failed its check: %s",
            connection.pk,
            scrub(str(exc)),
        )
        raise
    connection.last_error = ""
    connection.last_checked_at = timezone.now()
    connection.save(
        update_fields=["capabilities", "last_error", "last_checked_at", "updated_at"]
    )
    return connection


def browse_files(connection, entry_id):
    """List one level of the connection's file tree, folders first."""
    prov = get_available_provider(connection.provider)
    check_remote_url(connection.base_url)
    source = prov.file_source(connection)
    entries = list(source.list_dir(entry_id or source.ROOT_ID))
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries
