"""CalDAV (RFC 4791) and WebDAV sync (RFC 6578) for the user's calendars.

Layout, the one every client discovers from ``/.well-known/caldav``::

    /caldav/                                      root: points at the principal
    /caldav/principals/<username>/                the user: points at the home
    /caldav/calendars/<username>/                 home: one collection per calendar
    /caldav/calendars/<username>/<calendar>/      a calendar
    /caldav/calendars/<username>/<calendar>/<name>.ics   one event or series

The home lists every calendar the user can see: their own, the external ICS
calendars they follow, and the calendars they subscribed to. Only their own
non-external calendars are writable; the rest are served read-only, which is
what ``current-user-privilege-set`` tells the client.

Events the user is merely invited to live in somebody else's calendar and are
not served here.
"""

from datetime import UTC, datetime
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from django.db import IntegrityError
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.urls import get_script_prefix
from django.views.decorators.csrf import csrf_exempt

from workspace.common.cache import invalidate
from workspace.common.dav.views import DavView, check_preconditions, depth
from workspace.common.dav.xml import (
    DAV,
    DavError,
    Multistatus,
    element,
    href_element,
    parse_body,
    qname,
    register_prefix,
    requested_properties,
    status_line,
)
from workspace.common.uuids import parse_uuid_or_none
from workspace.users.services.settings import get_user_timezone

from ..models import Calendar
from ..queries import visible_calendar_ids
from ..services.calendar_objects import (
    calendar_masters,
    find_object,
    masters_in_range,
    store_object,
)
from ..services.feeds import render_feed
from ..services.ical_objects import InvalidObject, parse_object, render_object
from ..services.sync_log import (
    changes_since,
    object_etag,
    object_name,
    object_revisions,
)

CALDAV = "urn:ietf:params:xml:ns:caldav"
CALSERVER = "http://calendarserver.org/ns/"
APPLE_ICAL = "http://apple.com/ns/ical/"

register_prefix("cal", CALDAV)
register_prefix("cs", CALSERVER)
register_prefix("ical", APPLE_ICAL)

# The largest event a client may PUT. A series with its overrides and alarms
# is a few KB; this leaves room for inline attachments without letting one
# request hold megabytes of text in memory.
MAX_RESOURCE_SIZE = 1024 * 1024

SYNC_TOKEN_PREFIX = "urn:workspace:calendar-sync:"

CONTENT_TYPE = "text/calendar; charset=utf-8"

# What a calendar's palette name looks like in a client, and back.
_PALETTE = {
    "primary": "#6366F1",
    "secondary": "#EC4899",
    "accent": "#14B8A6",
    "info": "#0EA5E9",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#EF4444",
}


def _d(name):
    return qname(DAV, name)


def _cal(name):
    return qname(CALDAV, name)


# ── Paths ─────────────────────────────────────────────────────────────


def _root_path():
    return f"{get_script_prefix()}caldav/"


def _principals_path():
    return f"{_root_path()}principals/"


def _principal_path(username):
    return f"{_principals_path()}{username}/"


def _home_path(username):
    return f"{_root_path()}calendars/{username}/"


def _calendar_path(username, calendar):
    return f"{_home_path(username)}{calendar.uuid}/"


# ── Properties ────────────────────────────────────────────────────────


def _hrefs(tag, *paths):
    return element(tag, children=[href_element(path) for path in paths])


def _resourcetype(*kinds):
    return element(_d("resourcetype"), children=[element(kind) for kind in kinds])


def _privileges(writable):
    names = [_d("read")]
    if writable:
        names += [_d("write"), _d("write-content"), _d("bind"), _d("unbind")]
    privileges = [element(_d("privilege"), children=[element(name)]) for name in names]
    return element(_d("current-user-privilege-set"), children=privileges)


def _supported_reports(*reports):
    return element(
        _d("supported-report-set"),
        children=[
            element(
                _d("supported-report"),
                children=[element(_d("report"), children=[element(report)])],
            )
            for report in reports
        ],
    )


def _color_hex(color):
    if color.startswith("#"):
        return color
    return _PALETTE.get(color, _PALETTE["primary"])


def _palette_name(hex_color):
    """The palette entry closest to a client's ``#RRGGBB[AA]`` colour."""
    value = hex_color.strip().lstrip("#")[:6]
    try:
        rgb = tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None

    def distance(item):
        ref = item[1].lstrip("#")
        return sum((int(ref[i * 2 : i * 2 + 2], 16) - rgb[i]) ** 2 for i in range(3))

    return min(_PALETTE.items(), key=distance)[0]


def _sync_token(calendar):
    return f"{SYNC_TOKEN_PREFIX}{calendar.sync_revision}"


def _parse_sync_token(token, calendar):
    """The revision a client's token names; 403 for a token this server never issued."""
    if not token:
        return 0
    revision = token.removeprefix(SYNC_TOKEN_PREFIX)
    if (
        revision == token
        or not revision.isdigit()
        or int(revision) > calendar.sync_revision
    ):
        raise DavError(403, _d("valid-sync-token"))
    return int(revision)


class _PropertySet:
    """The properties of one resource, built only when asked for.

    *builders* maps a qualified name to a callable returning the property
    element; *expensive* names stay out of an ``allprop`` answer (RFC 4791
    excludes calendar-data from it).
    """

    def __init__(self, builders, expensive=()):
        self.builders = builders
        self.expensive = set(expensive)

    def add_to(self, multistatus, path, mode, requested):
        if mode == "propname":
            multistatus.add_propstat(
                path, found=[element(tag) for tag in self.builders]
            )
            return
        if mode == "allprop":
            requested = [tag for tag in self.builders if tag not in self.expensive]
        found, missing = [], []
        for tag in requested:
            builder = self.builders.get(tag)
            value = builder() if builder else None
            if value is None:
                missing.append(tag)
            else:
                found.append(value)
        multistatus.add_propstat(path, found=found, missing=missing)


def _principal_ref(request):
    return _hrefs(
        _d("current-user-principal"), _principal_path(request.user.get_username())
    )


def _collection_properties(request, displayname):
    return _PropertySet(
        {
            _d("resourcetype"): lambda: _resourcetype(_d("collection")),
            _d("displayname"): lambda: element(_d("displayname"), displayname),
            _d("current-user-principal"): lambda: _principal_ref(request),
            _d("principal-collection-set"): lambda: _hrefs(
                _d("principal-collection-set"), _principals_path()
            ),
        }
    )


def _principal_properties(request):
    user = request.user
    username = user.get_username()
    addresses = [f"mailto:{user.email}"] if user.email else []
    return _PropertySet(
        {
            _d("resourcetype"): lambda: _resourcetype(_d("principal")),
            _d("displayname"): lambda: element(
                _d("displayname"), user.get_full_name() or username
            ),
            _d("current-user-principal"): lambda: _principal_ref(request),
            _d("principal-URL"): lambda: _hrefs(
                _d("principal-URL"), _principal_path(username)
            ),
            _d("principal-collection-set"): lambda: _hrefs(
                _d("principal-collection-set"), _principals_path()
            ),
            _cal("calendar-home-set"): lambda: _hrefs(
                _cal("calendar-home-set"), _home_path(username)
            ),
            _cal("calendar-user-address-set"): lambda: element(
                _cal("calendar-user-address-set"),
                children=[
                    element(_d("href"), address)
                    for address in [*addresses, _principal_path(username)]
                ],
            ),
            _cal("calendar-user-type"): lambda: element(
                _cal("calendar-user-type"), "INDIVIDUAL"
            ),
            _d("supported-report-set"): _supported_reports,
        }
    )


def _home_properties(request):
    username = request.user.get_username()
    properties = _collection_properties(request, username)
    properties.builders |= {
        _d("owner"): lambda: _hrefs(_d("owner"), _principal_path(username)),
        _d("current-user-privilege-set"): lambda: _privileges(False),
        _d("supported-report-set"): _supported_reports,
    }
    return properties


def _calendar_properties(request, calendar, writable):
    owned = calendar.owner_id == request.user.pk
    builders = {
        _d("resourcetype"): lambda: _resourcetype(_d("collection"), _cal("calendar")),
        _d("displayname"): lambda: element(_d("displayname"), calendar.name),
        qname(APPLE_ICAL, "calendar-color"): lambda: element(
            qname(APPLE_ICAL, "calendar-color"), _color_hex(calendar.color)
        ),
        _cal("supported-calendar-component-set"): lambda: element(
            _cal("supported-calendar-component-set"),
            children=[ET.Element(_cal("comp"), name="VEVENT")],
        ),
        _cal("supported-calendar-data"): lambda: element(
            _cal("supported-calendar-data"),
            children=[
                ET.Element(
                    _cal("calendar-data"),
                    {"content-type": "text/calendar", "version": "2.0"},
                )
            ],
        ),
        _cal("max-resource-size"): lambda: element(
            _cal("max-resource-size"), MAX_RESOURCE_SIZE
        ),
        _d("sync-token"): lambda: element(_d("sync-token"), _sync_token(calendar)),
        qname(CALSERVER, "getctag"): lambda: element(
            qname(CALSERVER, "getctag"),
            f"{calendar.sync_revision}-{int(calendar.updated_at.timestamp())}",
        ),
        _d("current-user-principal"): lambda: _principal_ref(request),
        _d("current-user-privilege-set"): lambda: _privileges(writable),
        _d("supported-report-set"): lambda: _supported_reports(
            _cal("calendar-multiget"), _cal("calendar-query"), _d("sync-collection")
        ),
    }
    if owned:
        builders[_d("owner")] = lambda: _hrefs(
            _d("owner"), _principal_path(request.user.get_username())
        )
    return _PropertySet(builders)


def _object_properties(master, etag):
    return _PropertySet(
        {
            _d("resourcetype"): lambda: element(_d("resourcetype")),
            _d("getetag"): lambda: element(_d("getetag"), etag),
            _d("getcontenttype"): lambda: element(
                _d("getcontenttype"), "text/calendar; charset=utf-8; component=vevent"
            ),
            _cal("calendar-data"): lambda: element(
                _cal("calendar-data"),
                render_object(master, list(master.exceptions.all())),
            ),
        },
        expensive=[_cal("calendar-data")],
    )


# ── Views ─────────────────────────────────────────────────────────────


@csrf_exempt
def well_known(request):
    """RFC 6764 discovery: whatever the method, the service lives at /caldav/."""
    return HttpResponsePermanentRedirect(_root_path())


class _CalDavView(DavView):
    dav_compliance = "1, 3, calendar-access"

    def _require_self(self, username):
        if username != self.request.user.get_username():
            raise DavError(404)

    def _propfind_args(self, request):
        level = depth(request, default="0")
        if level == "infinity":
            raise DavError(403, _d("propfind-finite-depth"))
        mode, requested = requested_properties(parse_body(request.body))
        return level, mode, requested


class CalDavRootView(_CalDavView):
    """``/caldav/`` and ``/caldav/principals/``: where a client asks who it is."""

    def propfind(self, request):
        _level, mode, requested = self._propfind_args(request)
        multistatus = Multistatus()
        _collection_properties(request, "CalDAV").add_to(
            multistatus, request.path, mode, requested
        )
        return multistatus.response()


class PrincipalView(_CalDavView):
    def propfind(self, request, username):
        self._require_self(username)
        _level, mode, requested = self._propfind_args(request)
        multistatus = Multistatus()
        _principal_properties(request).add_to(
            multistatus, _principal_path(username), mode, requested
        )
        return multistatus.response()

    def report(self, request, username):
        raise DavError(403, _d("supported-report"))


class CalendarHomeView(_CalDavView):
    def propfind(self, request, username):
        self._require_self(username)
        level, mode, requested = self._propfind_args(request)
        multistatus = Multistatus()
        _home_properties(request).add_to(
            multistatus, _home_path(username), mode, requested
        )
        if level == "1":
            calendars = Calendar.objects.filter(
                uuid__in=visible_calendar_ids(request.user)
            ).select_related("external_source")
            for calendar in calendars:
                _calendar_properties(
                    request, calendar, _writable(request, calendar)
                ).add_to(
                    multistatus, _calendar_path(username, calendar), mode, requested
                )
        return multistatus.response()

    def report(self, request, username):
        raise DavError(403, _d("supported-report"))


def _writable(request, calendar):
    return calendar.owner_id == request.user.pk and not hasattr(
        calendar, "external_source"
    )


def _visible_calendar(request, username, calendar_id):
    calendar_id = parse_uuid_or_none(calendar_id)
    if username != request.user.get_username() or calendar_id is None:
        raise DavError(404)
    if calendar_id not in set(visible_calendar_ids(request.user)):
        raise DavError(404)
    return Calendar.objects.select_related("owner", "external_source").get(
        pk=calendar_id
    )


def _require_writable(request, calendar):
    if not _writable(request, calendar):
        raise DavError(403, _d("need-privileges"))


def _object_etags(calendar, masters):
    revisions = object_revisions(calendar.pk, [master.pk for master in masters])
    return {
        master.pk: object_etag(master.pk, revisions.get(master.pk, 0))
        for master in masters
    }


def _add_objects(multistatus, collection_path, calendar, masters, mode, requested):
    masters = list(masters)
    etags = _object_etags(calendar, masters)
    for master in masters:
        _object_properties(master, etags[master.pk]).add_to(
            multistatus, f"{collection_path}{object_name(master)}", mode, requested
        )


def _parse_utc(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise DavError(400, message="Invalid time-range.") from None


class CalendarCollectionView(_CalDavView):
    def _calendar(self, request, username, calendar_id):
        return _visible_calendar(request, username, calendar_id)

    def propfind(self, request, username, calendar_id):
        calendar = self._calendar(request, username, calendar_id)
        level, mode, requested = self._propfind_args(request)
        path = _calendar_path(username, calendar)
        multistatus = Multistatus()
        _calendar_properties(request, calendar, _writable(request, calendar)).add_to(
            multistatus, path, mode, requested
        )
        if level == "1":
            _add_objects(
                multistatus, path, calendar, calendar_masters(calendar), mode, requested
            )
        return multistatus.response()

    def get(self, request, username, calendar_id):
        calendar = self._calendar(request, username, calendar_id)
        return HttpResponse(render_feed(calendar), content_type=CONTENT_TYPE)

    def proppatch(self, request, username, calendar_id):
        calendar = self._calendar(request, username, calendar_id)
        root = parse_body(request.body)
        if root is None or root.tag != _d("propertyupdate"):
            raise DavError(400, message="Expected a propertyupdate body.")

        tags = {"name": _d("displayname"), "color": qname(APPLE_ICAL, "calendar-color")}
        updates = {}
        refused = []
        for action in root:
            for prop in action.iterfind(_d("prop")):
                for child in prop:
                    value = (
                        (child.text or "").strip() if action.tag == _d("set") else ""
                    )
                    if child.tag == tags["name"] and value:
                        updates["name"] = value[:255]
                    elif child.tag == tags["color"] and _palette_name(value):
                        updates["color"] = _palette_name(value)
                    else:
                        refused.append(child.tag)
        if not _writable(request, calendar):
            refused += [tags[key] for key in updates]
            updates = {}

        multistatus = Multistatus()
        if refused:
            # PROPPATCH is all or nothing (RFC 4918 9.2): what could have been
            # applied is reported as failing because of the rest.
            response = multistatus.add_propstat(
                _calendar_path(username, calendar), forbidden=refused
            )
            if updates:
                _failed_dependency(response, [tags[key] for key in updates])
            return multistatus.response()

        for key, value in updates.items():
            setattr(calendar, key, value)
        if updates:
            calendar.save(update_fields=[*updates, "updated_at"])
            invalidate("CalendarListView", user=request.user)
        multistatus.add_propstat(
            _calendar_path(username, calendar),
            found=[element(tags[key]) for key in updates],
        )
        return multistatus.response()

    def report(self, request, username, calendar_id):
        calendar = self._calendar(request, username, calendar_id)
        root = parse_body(request.body)
        if root is None:
            raise DavError(400, message="Expected a REPORT body.")
        path = _calendar_path(username, calendar)
        mode, requested = requested_properties(root)
        if root.tag == _cal("calendar-multiget"):
            return self._multiget(calendar, path, root, mode, requested)
        if root.tag == _cal("calendar-query"):
            return self._query(calendar, path, root, mode, requested)
        if root.tag == _d("sync-collection"):
            return self._sync(calendar, path, root, mode, requested)
        raise DavError(403, _d("supported-report"))

    def delete(self, request, username, calendar_id):
        self._calendar(request, username, calendar_id)
        raise DavError(403, message="Calendars are deleted from the web app.")

    def _multiget(self, calendar, path, root, mode, requested):
        masters, missing = [], []
        for node in root.iterfind(_d("href")):
            name = unquote((node.text or "").strip().rstrip("/").rsplit("/", 1)[-1])
            master = find_object(calendar, name) if name else None
            if master is None:
                missing.append(f"{path}{name}")
            else:
                masters.append(master)
        multistatus = Multistatus()
        _add_objects(multistatus, path, calendar, masters, mode, requested)
        for href in missing:
            multistatus.add_status(href, 404)
        return multistatus.response()

    def _query(self, calendar, path, root, mode, requested):
        masters = calendar_masters(calendar)
        calendar_filter = root.find(_cal("filter"))
        vcalendar = (
            calendar_filter.find(_cal("comp-filter"))
            if calendar_filter is not None
            else None
        )
        component = (
            vcalendar.find(_cal("comp-filter")) if vcalendar is not None else None
        )
        if component is not None:
            if component.get("name", "").upper() != "VEVENT":
                # VTODO and friends: this server holds events only.
                return Multistatus().response()
            time_range = component.find(_cal("time-range"))
            if time_range is not None:
                masters = masters_in_range(
                    masters,
                    _parse_utc(time_range.get("start")),
                    _parse_utc(time_range.get("end")),
                )
            for prop_filter in component.iterfind(_cal("prop-filter")):
                text_match = prop_filter.find(_cal("text-match"))
                if (
                    prop_filter.get("name", "").upper() == "UID"
                    and text_match is not None
                ):
                    uid = (text_match.text or "").strip()
                    masters = [
                        m
                        for m in masters
                        if uid.lower() in (m.ical_uid or str(m.uuid)).lower()
                    ]
        multistatus = Multistatus()
        _add_objects(multistatus, path, calendar, masters, mode, requested)
        return multistatus.response()

    def _sync(self, calendar, path, root, mode, requested):
        level = (root.findtext(_d("sync-level")) or "1").strip()
        if level != "1":
            raise DavError(403, _d("sync-traversal-supported"))
        revision = _parse_sync_token(
            (root.findtext(_d("sync-token")) or "").strip(), calendar
        )

        multistatus = Multistatus()
        if revision == 0:
            masters = list(calendar_masters(calendar))
            gone = []
        else:
            changes = list(changes_since(calendar.pk, revision))
            present = {
                master.pk: master
                for master in calendar_masters(calendar).filter(
                    uuid__in=[change.event_uuid for change in changes]
                )
            }
            masters = [
                present[c.event_uuid] for c in changes if c.event_uuid in present
            ]
            gone = [c.name for c in changes if c.event_uuid not in present]
        _add_objects(multistatus, path, calendar, masters, mode, requested)
        for name in gone:
            multistatus.add_status(f"{path}{name}", 404)
        multistatus.append(element(_d("sync-token"), _sync_token(calendar)))
        return multistatus.response()


def _failed_dependency(response, tags):
    propstat = ET.SubElement(response, _d("propstat"))
    ET.SubElement(propstat, _d("prop")).extend(ET.Element(tag) for tag in tags)
    ET.SubElement(propstat, _d("status")).text = status_line(424)


class CalendarObjectView(_CalDavView):
    def _resolve(self, request, username, calendar_id, name):
        calendar = _visible_calendar(request, username, calendar_id)
        master = find_object(calendar, name)
        etag = None
        if master is not None:
            etag = _object_etags(calendar, [master])[master.pk]
        return calendar, master, etag

    def get(self, request, username, calendar_id, name):
        _calendar, master, etag = self._resolve(request, username, calendar_id, name)
        if master is None:
            raise DavError(404)
        response = HttpResponse(
            render_object(master, list(master.exceptions.all())),
            content_type=CONTENT_TYPE,
        )
        response["ETag"] = etag
        return response

    def propfind(self, request, username, calendar_id, name):
        calendar, master, etag = self._resolve(request, username, calendar_id, name)
        if master is None:
            raise DavError(404)
        _level, mode, requested = self._propfind_args(request)
        multistatus = Multistatus()
        _object_properties(master, etag).add_to(
            multistatus, f"{_calendar_path(username, calendar)}{name}", mode, requested
        )
        return multistatus.response()

    def put(self, request, username, calendar_id, name):
        calendar, master, etag = self._resolve(request, username, calendar_id, name)
        _require_writable(request, calendar)
        if len(name) > 255:
            raise DavError(400, message="Resource name too long.")
        length = request.META.get("CONTENT_LENGTH") or "0"
        if not length.isdigit() or int(length) > MAX_RESOURCE_SIZE:
            raise DavError(403, _cal("max-resource-size"))
        check_preconditions(request, etag)
        if len(request.body) > MAX_RESOURCE_SIZE:
            raise DavError(403, _cal("max-resource-size"))
        try:
            parsed = parse_object(request.body, get_user_timezone(calendar.owner))
            store_object(calendar, name, parsed, master)
        except InvalidObject as exc:
            status = 409 if exc.condition == "no-uid-conflict" else 403
            raise DavError(status, _cal(exc.condition), str(exc)) from None
        except IntegrityError:
            # Another request created the same name or UID in the meantime:
            # the precondition this one was checked against no longer holds.
            raise DavError(412) from None
        # No ETag: the stored object is a re-rendering of what was sent, not
        # the same bytes, and a client must fetch it to learn its ETag.
        return HttpResponse(status=201 if master is None else 204)

    def delete(self, request, username, calendar_id, name):
        calendar, master, etag = self._resolve(request, username, calendar_id, name)
        _require_writable(request, calendar)
        if master is None:
            raise DavError(404)
        check_preconditions(request, etag)
        master.delete()
        return HttpResponse(status=204)
