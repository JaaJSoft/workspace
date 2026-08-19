"""Nextcloud: WebDAV underneath, plus what only Nextcloud offers (URL layout,
OCS discovery - and later its own data kinds: calendar, contacts, Deck...)."""

import logging
from urllib.parse import urlparse

import httpx2

from workspace.common.logging import scrub

from .base import ProviderError
from .webdav import WebDavProvider, _translate_transport_errors, build_client

logger = logging.getLogger(__name__)

DAV_FILES_PREFIX = "/remote.php/dav/files/"
_LEGACY_DAV_PREFIX = "/remote.php/webdav"
_OCS_CAPABILITIES = "/ocs/v1.php/cloud/capabilities"


def _instance_root(base_url: str) -> str:
    """'https://cloud.example.org/remote.php/dav/files/alice' -> 'https://cloud.example.org'."""
    parsed = urlparse(base_url)
    path = parsed.path
    for marker in (DAV_FILES_PREFIX, _LEGACY_DAV_PREFIX):
        if marker in path:
            path = path[: path.index(marker)]
            break
    return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}"


class NextcloudProvider(WebDavProvider):
    slug = "nextcloud"
    name = "Nextcloud"

    def normalize_base_url(self, url: str, username: str) -> str:
        """Accept the instance URL the user copies from the address bar and
        derive the per-user DAV root; leave a full DAV URL untouched."""
        url = url.rstrip("/")
        path = urlparse(url).path
        if DAV_FILES_PREFIX in path or path.endswith(_LEGACY_DAV_PREFIX):
            return url
        return f"{url}{DAV_FILES_PREFIX}{username}"

    def test_connection(self, connection) -> dict:
        capabilities = super().test_connection(connection)
        capabilities.update(self._discover(connection))
        return capabilities

    def _discover(self, connection) -> dict:
        """Best effort: OCS is not required for files, so any failure here
        only means less information on the connection card."""
        root = _instance_root(connection.base_url)
        try:
            with (
                build_client(connection, base_url=root) as client,
                _translate_transport_errors(urlparse(root).hostname),
            ):
                response = client.get(
                    _OCS_CAPABILITIES,
                    params={"format": "json"},
                    headers={"OCS-APIRequest": "true"},
                )
            if response.status_code != 200:
                return {}
            data = response.json()["ocs"]["data"]
        except (
            ProviderError,
            httpx2.HTTPError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            logger.info(
                "Nextcloud discovery skipped for %s: %s", scrub(root), scrub(str(exc))
            )
            return {}
        version = data.get("version", {})
        return {
            "server_version": str(version.get("string") or version.get("major") or ""),
            "apps": sorted((data.get("capabilities") or {}).keys()),
        }
