"""Nextcloud: WebDAV underneath, plus what only Nextcloud offers (URL layout,
OCS discovery - and later its own data kinds: calendar, contacts, Deck...)."""

import logging
from urllib.parse import urlparse

import httpx2

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub

from .base import ProviderError, RemoteTag
from .webdav import (
    DAV,
    WebDavProvider,
    _raise_for_status,
    _translate_transport_errors,
    build_client,
    entry_id_from_href,
    ok_props,
    parse_dav_xml,
)

logger = logging.getLogger(__name__)

DAV_FILES_PREFIX = "/remote.php/dav/files/"
_LEGACY_DAV_PREFIX = "/remote.php/webdav"
_OCS_CAPABILITIES = "/ocs/v1.php/cloud/capabilities"
_DAV_SYSTEMTAGS = "/remote.php/dav/systemtags/"

OC = "{http://owncloud.org/ns}"

_SYSTEMTAGS_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop>'
    b"<oc:id/><oc:display-name/><oc:user-visible/>"
    b"</d:prop></d:propfind>"
)


def _instance_root(base_url: str) -> str:
    """'https://cloud.example.org/remote.php/dav/files/alice' -> 'https://cloud.example.org'."""
    parsed = urlparse(base_url)
    path = parsed.path
    for marker in (DAV_FILES_PREFIX, _LEGACY_DAV_PREFIX):
        if marker in path:
            path = path[: path.index(marker)]
            break
    return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}"


def _filter_files_body(rule: bytes) -> bytes:
    """A REPORT asking the files endpoint for every entry matching one rule.

    Nextcloud answers with a multistatus whose hrefs are the matching paths -
    the whole tree in one round trip, which is what makes reading favorites and
    tags cheap enough to run after every import.
    """
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<oc:filter-files xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        b"<d:prop><d:getetag/></d:prop>"
        b"<oc:filter-rules>" + rule + b"</oc:filter-rules>"
        b"</oc:filter-files>"
    )


class NextcloudMetadataSource:
    """Favorites and tags, read over the same instance the files came from.

    Both live outside the per-user files endpoint - favorites are a filter on
    it, tags a collection of their own at the instance root - so this source
    talks to the root and addresses the files endpoint by path.
    """

    def __init__(self, connection, client=None):
        self._root = _instance_root(connection.base_url)
        # Absolute, to strip it off the hrefs the server answers with...
        self._files_path = urlparse(connection.base_url).path.rstrip("/")
        # ...and root-relative, because that is what the client's base URL
        # already carries on an instance served under a sub-path.
        root_path = urlparse(self._root).path.rstrip("/")
        self._files_url = self._files_path[len(root_path) :] + "/"
        self._host = urlparse(self._root).hostname or self._root
        self._client = client or build_client(connection, base_url=self._root)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- FileMetadataSource --------------------------------------------

    def favorites(self):
        yield from self._filter_files(b"<oc:favorite>1</oc:favorite>")

    def tags(self):
        response = self._request(
            "PROPFIND", _DAV_SYSTEMTAGS, _SYSTEMTAGS_BODY, depth="1"
        )
        for element in parse_dav_xml(response.content).iter(f"{DAV}response"):
            props = ok_props(element)
            if props is None:
                continue
            tag_id = (props.findtext(f"{OC}id") or "").strip()
            name = (props.findtext(f"{OC}display-name") or "").strip()
            # A tag id goes back to the server inside an XML body, so only the
            # integers Nextcloud actually mints are accepted.
            if not tag_id.isdigit() or not name:
                continue
            # Tags the user cannot see are the ones another user assigned, or
            # an app's own bookkeeping; neither belongs in a personal tag list.
            # Tri-state on purpose: servers spell the flag "false" or "0"
            # depending on the version, and one that omits it altogether has
            # told us nothing - the endpoint already scopes to this user.
            visible = (props.findtext(f"{OC}user-visible") or "").strip()
            if visible and not is_truthy(visible):
                continue
            yield RemoteTag(id=tag_id, name=name)

    def tagged(self, tag_id: str):
        if not str(tag_id).isdigit():
            return
        yield from self._filter_files(f"<oc:systemtag>{tag_id}</oc:systemtag>".encode())

    # -- internals -----------------------------------------------------

    def _filter_files(self, rule: bytes):
        response = self._request("REPORT", self._files_url, _filter_files_body(rule))
        for element in parse_dav_xml(response.content).iter(f"{DAV}response"):
            href = element.findtext(f"{DAV}href")
            if href:
                yield entry_id_from_href(href, self._files_path)

    def _request(self, method, url, body, *, depth="0"):
        with _translate_transport_errors(self._host):
            response = self._client.request(
                method,
                url,
                content=body,
                headers={"Depth": depth, "Content-Type": "application/xml"},
            )
        _raise_for_status(response, url)
        if response.status_code != 207:
            raise ProviderError(
                f"'{self._host}' answered HTTP {response.status_code} to "
                f"{method} {url} instead of a multistatus."
            )
        return response


class NextcloudProvider(WebDavProvider):
    slug = "nextcloud"
    name = "Nextcloud"

    def normalize_base_url(self, url: str, username: str) -> str:
        """Accept the instance URL the user copies from the address bar and
        derive the per-user DAV root. A full DAV URL is kept, except that its
        user segment follows the username: the files endpoint only serves the
        authenticated user's own tree, so a stale segment after a username
        change would just 404."""
        url = url.rstrip("/")
        parsed = urlparse(url)
        path = parsed.path
        if DAV_FILES_PREFIX in path:
            head, _, tail = path.partition(DAV_FILES_PREFIX)
            _, _, rest = tail.partition("/")
            new_path = f"{head}{DAV_FILES_PREFIX}{username}"
            if rest:
                new_path += f"/{rest}"
            return parsed._replace(path=new_path).geturl()
        if path.endswith(_LEGACY_DAV_PREFIX):
            return url
        return f"{url}{DAV_FILES_PREFIX}{username}"

    def test_connection(self, connection) -> dict:
        capabilities = super().test_connection(connection)
        capabilities.update(self._discover(connection))
        return capabilities

    def file_metadata_source(self, connection):
        return NextcloudMetadataSource(connection)

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
