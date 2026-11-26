"""Nextcloud: WebDAV underneath, plus what only Nextcloud offers (URL layout,
OCS discovery - and later its own data kinds: calendar, contacts, Deck...)."""

import json
import logging
from urllib.parse import quote, urlparse

import httpx2
from django.conf import settings
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_variables

from workspace.common.booleans import is_truthy
from workspace.common.logging import scrub
from workspace.common.url_guard import ResponseTooLarge, guarded_transport, read_capped

from ..services.network_policy import imports_network_policy
from .base import (
    KIND_CONTACTS,
    KIND_FILES,
    ConnectionFailed,
    LoginFlowGrant,
    LoginFlowStart,
    ProviderError,
    RemoteTag,
)
from .carddav import CardDavSource
from .webdav import (
    DAV,
    WebDavProvider,
    _translate_transport_errors,
    build_client,
    entry_id_from_href,
    multistatus_request,
    ok_props,
    parse_dav_xml,
)

logger = logging.getLogger(__name__)

DAV_FILES_PREFIX = "/remote.php/dav/files/"
_LEGACY_DAV_PREFIX = "/remote.php/webdav"
_OCS_CAPABILITIES = "/ocs/v1.php/cloud/capabilities"
_DAV_SYSTEMTAGS = "/remote.php/dav/systemtags/"
_DAV_ADDRESSBOOKS = "/remote.php/dav/addressbooks/users/"
_FRONT_CONTROLLER = "/index.php"
_LOGIN_FLOW_START = "/index.php/login/v2"
# Nextcloud names the app password it creates after the User-Agent of the
# request that starts the flow: this is what the user reads, and revokes, in
# their list of devices.
_LOGIN_FLOW_CLIENT_NAME = "Workspace"
# Both answers are a few hundred bytes of JSON.
_LOGIN_FLOW_MAX_BYTES = 64 * 1024

# Nextcloud-generated books that are not the user's own contacts: the instance
# account directory (>= 27), and the contactsinteraction app's auto-collected
# recent addresses.
_HIDDEN_ADDRESS_BOOK_PREFIXES = ("z-server-generated--", "z-app-generated--")

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
    for marker in (DAV_FILES_PREFIX, _LEGACY_DAV_PREFIX, _FRONT_CONTROLLER):
        if marker in path:
            path = path[: path.index(marker)]
            break
    return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}"


def addressbook_home(base_url: str, username: str) -> str:
    """The user's CardDAV address book home on the instance."""
    return f"{_instance_root(base_url)}{_DAV_ADDRESSBOOKS}{quote(username, safe='')}/"


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


def _member_failed(element) -> bool:
    """Whether a ``<response>`` reports an error of its own.

    RFC 4918 lets a member answer with a bare ``<status>`` instead of
    properties, and such a member is not a match. A status *inside* a
    ``<propstat>`` is a different thing entirely: it is about the property we
    asked for alongside, and the resource still matched the filter - requiring
    a 200 there would drop favorites whose etag the server could not read.
    """
    for token in (element.findtext(f"{DAV}status") or "").split():
        if len(token) == 3 and token.isdigit():
            return not 200 <= int(token) < 300
    return False


def _origin(url: str):
    """``(scheme, host, port)`` of a URL, the port filled in from the scheme;
    ``None`` for anything that is not an http(s) URL with a host."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        return None
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _same_origin(url: str, base_url: str) -> bool:
    origin = _origin(url)
    return origin is not None and origin == _origin(base_url)


def _login_flow_client() -> httpx2.Client:
    """No credentials, no redirects, and only addresses the URL guard accepts."""
    return httpx2.Client(
        timeout=settings.IMPORTS_HTTP_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": _LOGIN_FLOW_CLIENT_NAME, "Accept": "application/json"},
        transport=guarded_transport(imports_network_policy()),
    )


@sensitive_variables()
def _login_flow_post(url: str, host: str, data=None) -> tuple[int, dict | None]:
    """POST to a sign-in flow endpoint: the status and, on a 200, the JSON
    object it answered (``None`` when the body is not one)."""
    try:
        with (
            _translate_transport_errors(host),
            _login_flow_client() as client,
            client.stream("POST", url, data=data) as response,
        ):
            if response.status_code != 200:
                return response.status_code, None
            body = read_capped(response, _LOGIN_FLOW_MAX_BYTES)
    except ResponseTooLarge as exc:
        raise ProviderError(
            _("%(host)s answered the sign-in request with too much data.")
            % {"host": host}
        ) from exc
    try:
        payload = json.loads(body)
    except ValueError:
        return 200, None
    return 200, payload if isinstance(payload, dict) else None


def _not_a_login_flow(host):
    return ProviderError(
        _(
            "%(host)s did not answer as a Nextcloud server offering sign-in. "
            "Use an app password instead."
        )
        % {"host": host}
    )


def _text(value) -> str:
    return value if isinstance(value, str) else ""


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
        response = multistatus_request(
            self._client,
            self._host,
            "PROPFIND",
            _DAV_SYSTEMTAGS,
            _SYSTEMTAGS_BODY,
            depth="1",
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
            # Fail closed: only an explicit true carries a tag over, so a
            # server that omits the flag or spells it in some way we do not
            # recognise imports nothing rather than the wrong thing.
            if not is_truthy((props.findtext(f"{OC}user-visible") or "").strip()):
                continue
            yield RemoteTag(id=tag_id, name=name)

    def tagged(self, tag_id: str):
        if not str(tag_id).isdigit():
            return
        yield from self._filter_files(f"<oc:systemtag>{tag_id}</oc:systemtag>".encode())

    # -- internals -----------------------------------------------------

    def _filter_files(self, rule: bytes):
        response = multistatus_request(
            self._client,
            self._host,
            "REPORT",
            self._files_url,
            _filter_files_body(rule),
            depth="0",
        )
        for element in parse_dav_xml(response.content).iter(f"{DAV}response"):
            href = element.findtext(f"{DAV}href")
            if not href or _member_failed(element):
                continue
            yield entry_id_from_href(href, self._files_path)


class NextcloudProvider(WebDavProvider):
    slug = "nextcloud"
    name = "Nextcloud"
    login_flow = True
    kinds = frozenset({KIND_FILES, KIND_CONTACTS})

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

    def contact_source(self, connection):
        return CardDavSource(
            connection,
            addressbook_home(connection.base_url, connection.username),
            hidden_prefixes=_HIDDEN_ADDRESS_BOOK_PREFIXES,
        )

    # -- Login Flow v2 ------------------------------------------------

    def login_flow_base_url(self, url: str) -> str:
        """The instance root, whatever page of it the user copied."""
        return _instance_root(url.rstrip("/"))

    @sensitive_variables()
    def start_login_flow(self, base_url: str) -> LoginFlowStart:
        """Both addresses the answer carries must be on the instance the user
        entered: the poll is sent the token, and the sign-in page is where the
        user types their Nextcloud password."""
        host = urlparse(base_url).hostname or base_url
        code, payload = _login_flow_post(f"{base_url}{_LOGIN_FLOW_START}", host)
        if code != 200 or payload is None:
            raise _not_a_login_flow(host)
        poll = payload.get("poll")
        poll = poll if isinstance(poll, dict) else {}
        token = _text(poll.get("token"))
        endpoint = _text(poll.get("endpoint"))
        login_url = _text(payload.get("login"))
        if not (token and endpoint and login_url):
            raise _not_a_login_flow(host)
        if not (_same_origin(endpoint, base_url) and _same_origin(login_url, base_url)):
            raise ProviderError(
                _(
                    "%(host)s pointed the sign-in to another server. Check the "
                    "address, or use an app password instead."
                )
                % {"host": host}
            )
        return LoginFlowStart(
            login_url=login_url, poll_endpoint=endpoint, poll_token=token
        )

    @sensitive_variables()
    def poll_login_flow(
        self, base_url: str, poll_endpoint: str, poll_token: str
    ) -> LoginFlowGrant | None:
        host = urlparse(base_url).hostname or base_url
        if not _same_origin(poll_endpoint, base_url):
            raise _not_a_login_flow(host)
        code, payload = _login_flow_post(poll_endpoint, host, {"token": poll_token})
        # Nextcloud answers 404 until the user has approved the flow.
        if code == 404:
            return None
        if code >= 500:
            raise ConnectionFailed(
                _("%(host)s answered with HTTP %(code)s.")
                % {"host": host, "code": code}
            )
        if code != 200:
            raise ProviderError(
                _("The server answered with HTTP %(code)s.") % {"code": code}
            )
        payload = payload or {}
        login_name = _text(payload.get("loginName"))
        app_password = _text(payload.get("appPassword"))
        if not (login_name and app_password):
            raise _not_a_login_flow(host)
        return LoginFlowGrant(username=login_name, secret=app_password)

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
