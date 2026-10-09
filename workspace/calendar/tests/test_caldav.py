"""CalDAV end to end, the way a client walks it: discover, list, sync, write."""

import base64
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from knox.models import AuthToken

from workspace.calendar.models import Calendar, CalendarSubscription, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.common.dav.auth import clear_auth_cache

from .test_ical_objects import OVERRIDE, SERIES, _ics

User = get_user_model()

D = "{DAV:}"
C = "{urn:ietf:params:xml:ns:caldav}"
CS = "{http://calendarserver.org/ns/}"

PROPFIND_ALL = (
    b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:allprop/></d:propfind>'
)


def _propfind(*props):
    namespaces = {
        "d": "DAV:",
        "c": "urn:ietf:params:xml:ns:caldav",
        "cs": "http://calendarserver.org/ns/",
    }
    xmlns = " ".join(f'xmlns:{p}="{uri}"' for p, uri in namespaces.items())
    return f"<d:propfind {xmlns}><d:prop>{''.join(f'<{p}/>' for p in props)}</d:prop></d:propfind>".encode()


class CalDavTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="alice-pw-1"
        )
        cls.other = User.objects.create_user(username="bob", password="bob-pw-1")
        cls.calendar = Calendar.objects.create(name="Work", owner=cls.user)

    def setUp(self):
        clear_auth_cache()
        # A DAV client sends no CSRF token: the endpoints must not need one.
        self.client = Client(enforce_csrf_checks=True)

    def tearDown(self):
        clear_auth_cache()
        cache.clear()

    @property
    def calendar_path(self):
        return f"/caldav/calendars/alice/{self.calendar.uuid}/"

    def dav(
        self, method, path, body=b"", user="alice", password="alice-pw-1", **headers
    ):
        if user is not None:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        content_type = headers.pop("content_type", "application/xml; charset=utf-8")
        return self.client.generic(
            method, path, body, content_type=content_type, headers=headers
        )

    def xml(self, response):
        return ET.fromstring(response.content)

    def responses(self, response):
        """``{href: response element}`` of a multistatus answer."""
        self.assertEqual(response.status_code, 207, response.content)
        return {
            r.findtext(f"{D}href"): r for r in self.xml(response).iter(f"{D}response")
        }

    def put(self, name, body, **headers):
        return self.dav(
            "PUT",
            f"{self.calendar_path}{name}",
            body.encode(),
            content_type="text/calendar",
            **headers,
        )

    def etag_of(self, name):
        response = self.dav(
            "PROPFIND", f"{self.calendar_path}{name}", _propfind("d:getetag"), Depth="0"
        )
        return self.xml(response).findtext(f".//{D}getetag")


class DiscoveryTests(CalDavTestCase):
    def test_well_known_redirects_to_the_service(self):
        for method in ("GET", "PROPFIND"):
            with self.subTest(method=method):
                response = self.dav(method, "/.well-known/caldav", user=None)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/caldav/")

    def test_anonymous_is_challenged(self):
        response = self.dav("PROPFIND", "/caldav/", user=None)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response["WWW-Authenticate"])

    def test_wrong_password_is_challenged(self):
        self.assertEqual(
            self.dav("PROPFIND", "/caldav/", password="nope").status_code, 401
        )

    def test_options_advertises_calendar_access(self):
        response = self.dav("OPTIONS", self.calendar_path)
        self.assertIn("calendar-access", response["DAV"])
        self.assertIn("REPORT", response["Allow"])

    def test_principal_chain(self):
        root = self.dav(
            "PROPFIND", "/caldav/", _propfind("d:current-user-principal"), Depth="0"
        )
        principal = self.xml(root).findtext(f".//{D}current-user-principal/{D}href")
        self.assertEqual(principal, "/caldav/principals/alice/")

        response = self.dav(
            "PROPFIND",
            principal,
            _propfind(
                "c:calendar-home-set", "c:calendar-user-address-set", "d:displayname"
            ),
            Depth="0",
        )
        tree = self.xml(response)
        self.assertEqual(
            tree.findtext(f".//{C}calendar-home-set/{D}href"),
            "/caldav/calendars/alice/",
        )
        addresses = [
            h.text for h in tree.iterfind(f".//{C}calendar-user-address-set/{D}href")
        ]
        self.assertIn("mailto:alice@example.com", addresses)

    def test_someone_elses_principal_and_home_are_not_found(self):
        for path in ("/caldav/principals/bob/", "/caldav/calendars/bob/"):
            with self.subTest(path=path):
                self.assertEqual(self.dav("PROPFIND", path, Depth="0").status_code, 404)

    def test_home_lists_every_visible_calendar_with_its_rights(self):
        shared = Calendar.objects.create(name="Bob's", owner=self.other)
        CalendarSubscription.objects.create(user=self.user, calendar=shared)
        feed = Calendar.objects.create(name="Holidays", owner=self.user)
        ExternalCalendar.objects.create(calendar=feed, url="https://example.com/h.ics")
        Calendar.objects.create(name="Bob's private", owner=self.other)

        response = self.dav(
            "PROPFIND",
            "/caldav/calendars/alice/",
            _propfind(
                "d:resourcetype", "d:displayname", "d:current-user-privilege-set"
            ),
            Depth="1",
        )
        entries = self.responses(response)

        calendars = {
            r.findtext(f".//{D}displayname"): r
            for r in entries.values()
            if r.find(f".//{D}resourcetype/{C}calendar") is not None
        }
        self.assertEqual(set(calendars), {"Work", "Bob's", "Holidays"})

        def privileges(name):
            return {
                p.tag
                for p in calendars[name]
                .find(f".//{D}current-user-privilege-set")
                .iter()
                if p.tag.startswith(D)
                and not p.tag.endswith("privilege")
                and not p.tag.endswith("privilege-set")
            }

        self.assertIn(f"{D}write", privileges("Work"))
        self.assertEqual(privileges("Bob's"), {f"{D}read"})
        self.assertEqual(privileges("Holidays"), {f"{D}read"})

    def test_calendar_properties(self):
        response = self.dav(
            "PROPFIND",
            self.calendar_path,
            _propfind(
                "d:sync-token",
                "cs:getctag",
                "c:supported-calendar-component-set",
                "d:quota-used-bytes",
            ),
            Depth="0",
        )
        (entry,) = self.responses(response).values()
        self.assertTrue(
            entry.findtext(f".//{D}sync-token").startswith(
                "urn:workspace:calendar-sync:"
            )
        )
        self.assertIsNotNone(entry.findtext(f".//{CS}getctag"))
        self.assertEqual(entry.find(f".//{C}comp").get("name"), "VEVENT")
        statuses = [p.findtext(f"{D}status") for p in entry.iter(f"{D}propstat")]
        self.assertEqual(statuses, ["HTTP/1.1 200 OK", "HTTP/1.1 404 Not Found"])

    def test_unknown_calendar_is_not_found(self):
        others = Calendar.objects.create(name="Private", owner=self.other)
        for path in (
            f"/caldav/calendars/alice/{others.uuid}/",
            "/caldav/calendars/alice/00000000-0000-0000-0000-000000000000/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.dav("PROPFIND", path, Depth="0").status_code, 404)

    def test_infinite_depth_is_refused(self):
        response = self.dav(
            "PROPFIND", self.calendar_path, PROPFIND_ALL, Depth="infinity"
        )
        self.assertEqual(response.status_code, 403)

    def test_api_token_authenticates_and_revoking_it_stops_syncing(self):
        instance, token = AuthToken.objects.create(self.user)
        self.assertEqual(
            self.dav("PROPFIND", "/caldav/", password=token, Depth="0").status_code, 207
        )

        instance.delete()
        clear_auth_cache()  # what the cache TTL does on its own
        self.assertEqual(
            self.dav("PROPFIND", "/caldav/", password=token, Depth="0").status_code, 401
        )


class ObjectTests(CalDavTestCase):
    def test_create_read_update_delete(self):
        created = self.put(
            "series.ics", _ics(SERIES, OVERRIDE), **{"If-None-Match": "*"}
        )
        self.assertEqual(created.status_code, 201, created.content)
        master = Event.objects.get(calendar=self.calendar, dav_name="series.ics")
        self.assertEqual(master.source, Event.Source.CALDAV)

        got = self.dav("GET", f"{self.calendar_path}series.ics")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got["Content-Type"], "text/calendar; charset=utf-8")
        body = got.content.decode()
        self.assertIn("RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10", body)
        self.assertIn("RECURRENCE-ID;TZID=Europe/Paris:20261012T090000", body)
        self.assertIn("BEGIN:VALARM", body)
        etag = got["ETag"]
        self.assertEqual(self.etag_of("series.ics"), etag)

        renamed = _ics(
            [line.replace("Standup\\, daily", "Daily") for line in SERIES], OVERRIDE
        )
        self.assertEqual(
            self.put("series.ics", renamed, **{"If-Match": etag}).status_code, 204
        )
        new_etag = self.etag_of("series.ics")
        self.assertNotEqual(new_etag, etag)
        master.refresh_from_db()
        self.assertEqual(master.title, "Daily")

        stale = self.dav(
            "DELETE", f"{self.calendar_path}series.ics", **{"If-Match": etag}
        )
        self.assertEqual(stale.status_code, 412)
        deleted = self.dav(
            "DELETE", f"{self.calendar_path}series.ics", **{"If-Match": new_etag}
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Event.objects.filter(calendar=self.calendar).exists())
        self.assertEqual(
            self.dav("GET", f"{self.calendar_path}series.ics").status_code, 404
        )

    def test_stale_if_match_does_not_overwrite(self):
        self.put("a.ics", _ics(SERIES))
        response = self.put("a.ics", _ics(SERIES), **{"If-Match": '"stale"'})
        self.assertEqual(response.status_code, 412)

    def test_if_none_match_refuses_to_overwrite(self):
        self.put("a.ics", _ics(SERIES))
        self.assertEqual(
            self.put("a.ics", _ics(SERIES), **{"If-None-Match": "*"}).status_code, 412
        )

    def test_event_from_the_app_is_served_and_writable(self):
        native = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="From the web",
            start=datetime(2026, 10, 5, 9, tzinfo=UTC),
            end=datetime(2026, 10, 5, 10, tzinfo=UTC),
        )
        name = f"{native.uuid}.ics"
        got = self.dav("GET", f"{self.calendar_path}{name}")
        self.assertIn(f"UID:{native.uuid}", got.content.decode())

        edited = got.content.decode().replace("From the web", "From the phone")
        self.assertEqual(
            self.put(name, edited, **{"If-Match": got["ETag"]}).status_code, 204
        )
        native.refresh_from_db()
        self.assertEqual(native.title, "From the phone")
        self.assertEqual(Event.objects.filter(calendar=self.calendar).count(), 1)

    def test_invalid_bodies_name_the_broken_rule(self):
        todo = _ics(extra_components=[["BEGIN:VTODO", "UID:t", "END:VTODO"]])
        for body, condition in (
            ("not a calendar", "valid-calendar-data"),
            (todo, "supported-calendar-component"),
        ):
            with self.subTest(condition=condition):
                response = self.put("bad.ics", body)
                self.assertEqual(response.status_code, 403)
                self.assertIsNotNone(self.xml(response).find(f"{C}{condition}"))
        self.assertFalse(Event.objects.exists())

    def test_uid_used_by_another_object_is_a_conflict(self):
        self.put("a.ics", _ics(SERIES))
        response = self.put("b.ics", _ics(SERIES))
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(self.xml(response).find(f"{C}no-uid-conflict"))

    def test_oversized_body_is_refused(self):
        huge = _ics([*SERIES, "DESCRIPTION:" + "x" * (1024 * 1024)])
        response = self.put("big.ics", huge)
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.xml(response).find(f"{C}max-resource-size"))

    def test_read_only_calendars_refuse_writes(self):
        shared = Calendar.objects.create(name="Bob's", owner=self.other)
        CalendarSubscription.objects.create(user=self.user, calendar=shared)
        feed = Calendar.objects.create(name="Holidays", owner=self.user)
        ExternalCalendar.objects.create(calendar=feed, url="https://example.com/h.ics")
        for calendar in (shared, feed):
            with self.subTest(calendar=calendar.name):
                path = f"/caldav/calendars/alice/{calendar.uuid}/a.ics"
                response = self.dav(
                    "PUT", path, _ics(SERIES).encode(), content_type="text/calendar"
                )
                self.assertEqual(response.status_code, 403)
        self.assertFalse(Event.objects.exists())

    def test_calendar_collection_is_not_deleted(self):
        self.assertEqual(self.dav("DELETE", self.calendar_path).status_code, 403)
        self.assertTrue(Calendar.objects.filter(pk=self.calendar.pk).exists())

    def test_collection_get_exports_the_calendar(self):
        self.put("a.ics", _ics(SERIES))
        response = self.dav("GET", self.calendar_path)
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-WR-CALNAME:Work", response.content.decode())


class ReportTests(CalDavTestCase):
    def setUp(self):
        super().setUp()
        self.put("series.ics", _ics(SERIES, OVERRIDE))
        self.single = Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Dentist",
            start=datetime(2027, 3, 1, 9, tzinfo=UTC),
            end=datetime(2027, 3, 1, 10, tzinfo=UTC),
        )
        self.single_href = f"{self.calendar_path}{self.single.uuid}.ics"

    def report(self, body, path=None):
        return self.dav("REPORT", path or self.calendar_path, body.encode(), Depth="1")

    def test_depth_one_propfind_lists_objects_with_etags(self):
        response = self.dav(
            "PROPFIND", self.calendar_path, _propfind("d:getetag"), Depth="1"
        )
        entries = self.responses(response)
        self.assertEqual(
            set(entries),
            {self.calendar_path, f"{self.calendar_path}series.ics", self.single_href},
        )
        self.assertIsNotNone(entries[self.single_href].findtext(f".//{D}getetag"))

    def test_multiget(self):
        response = self.report(
            '<c:calendar-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            f"<d:href>{self.calendar_path}series.ics</d:href>"
            f"<d:href>{self.calendar_path}missing.ics</d:href>"
            "</c:calendar-multiget>"
        )
        entries = self.responses(response)
        data = entries[f"{self.calendar_path}series.ics"].findtext(
            f".//{C}calendar-data"
        )
        self.assertIn("UID:series-1", data)
        self.assertEqual(
            entries[f"{self.calendar_path}missing.ics"].findtext(f"{D}status"),
            "HTTP/1.1 404 Not Found",
        )

    def _query(self, component="VEVENT", time_range=""):
        return self.report(
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR">'
            f'<c:comp-filter name="{component}">{time_range}</c:comp-filter>'
            "</c:comp-filter></c:filter></c:calendar-query>"
        )

    def test_query_without_range_returns_every_event(self):
        self.assertEqual(len(self.responses(self._query())), 2)

    def test_query_with_time_range(self):
        response = self._query(
            time_range='<c:time-range start="20270201T000000Z" end="20270401T000000Z"/>'
        )
        self.assertEqual(set(self.responses(response)), {self.single_href})

    def test_query_for_todos_is_empty(self):
        self.assertEqual(self.responses(self._query("VTODO")), {})

    def test_query_with_a_broken_range_is_a_400(self):
        response = self._query(time_range='<c:time-range start="tomorrow"/>')
        self.assertEqual(response.status_code, 400)

    def test_unsupported_report(self):
        response = self.report(
            '<c:free-busy-query xmlns:c="urn:ietf:params:xml:ns:caldav"/>'
        )
        self.assertEqual(response.status_code, 403)

    def _sync(self, token=""):
        response = self.report(
            '<d:sync-collection xmlns:d="DAV:">'
            f"<d:sync-token>{token}</d:sync-token><d:sync-level>1</d:sync-level>"
            "<d:prop><d:getetag/></d:prop></d:sync-collection>"
        )
        self.assertEqual(response.status_code, 207, response.content)
        tree = self.xml(response)
        entries = {
            r.findtext(f"{D}href"): r.findtext(f"{D}status") or "changed"
            for r in tree.iter(f"{D}response")
        }
        return entries, tree.findtext(f"{D}sync-token")

    def test_sync_collection(self):
        everything, token = self._sync()
        self.assertEqual(
            set(everything), {f"{self.calendar_path}series.ics", self.single_href}
        )

        nothing, same = self._sync(token)
        self.assertEqual(nothing, {})
        self.assertEqual(same, token)

        # A change made in the app reaches the phone on its next sync.
        self.single.title = "Dentist (moved)"
        self.single.save(update_fields=["title"])
        Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="New",
            start=datetime(2027, 4, 1, 9, tzinfo=UTC),
        )
        self.dav("DELETE", f"{self.calendar_path}series.ics")

        changes, newer = self._sync(token)
        self.assertNotEqual(newer, token)
        self.assertEqual(
            changes.pop(f"{self.calendar_path}series.ics"), "HTTP/1.1 404 Not Found"
        )
        self.assertEqual(changes.pop(self.single_href), "changed")
        self.assertEqual(list(changes.values()), ["changed"])

    def test_repeated_syncs_report_no_spurious_change(self):
        _, token = self._sync()
        series = f"{self.calendar_path}series.ics"
        body = self.dav("GET", series).content.decode()
        etag = self.etag_of("series.ics")

        for _ in range(3):
            changes, token = self._sync(token)
            self.assertEqual(changes, {})
        self.assertEqual(self.etag_of("series.ics"), etag)
        self.assertEqual(
            Event.objects.filter(
                calendar=self.calendar, recurrence_parent=None
            ).count(),
            2,
        )
        self.assertEqual(
            self.dav("GET", series).content.decode().count("BEGIN:VEVENT"),
            body.count("BEGIN:VEVENT"),
        )

    def test_sync_with_a_foreign_token_is_refused(self):
        for token in ("garbage", "urn:workspace:calendar-sync:999999"):
            with self.subTest(token=token):
                response = self.report(
                    '<d:sync-collection xmlns:d="DAV:">'
                    f"<d:sync-token>{token}</d:sync-token><d:sync-level>1</d:sync-level>"
                    "<d:prop><d:getetag/></d:prop></d:sync-collection>"
                )
                self.assertEqual(response.status_code, 403)
                self.assertIsNotNone(self.xml(response).find(f"{D}valid-sync-token"))

    def test_ctag_moves_with_the_content(self):
        def ctag():
            response = self.dav(
                "PROPFIND", self.calendar_path, _propfind("cs:getctag"), Depth="0"
            )
            return self.xml(response).findtext(f".//{CS}getctag")

        before = ctag()
        self.single.delete()
        self.assertNotEqual(ctag(), before)


class ProppatchTests(CalDavTestCase):
    def proppatch(self, inner, path=None):
        body = (
            '<d:propertyupdate xmlns:d="DAV:" xmlns:i="http://apple.com/ns/ical/">'
            f"<d:set><d:prop>{inner}</d:prop></d:set></d:propertyupdate>"
        )
        return self.dav("PROPPATCH", path or self.calendar_path, body.encode())

    def test_rename_and_recolor(self):
        response = self.proppatch(
            "<d:displayname>Office</d:displayname><i:calendar-color>#E5484DFF</i:calendar-color>"
        )
        self.assertEqual(response.status_code, 207)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Office")
        self.assertEqual(self.calendar.color, "error")

    def test_unsupported_property_fails_the_whole_request(self):
        response = self.proppatch(
            "<d:displayname>Office</d:displayname><i:calendar-order>3</i:calendar-order>"
        )
        statuses = {
            p.findtext(f"{D}status") for p in self.xml(response).iter(f"{D}propstat")
        }
        self.assertEqual(
            statuses, {"HTTP/1.1 403 Forbidden", "HTTP/1.1 424 Failed Dependency"}
        )
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.name, "Work")

    def test_read_only_calendar_cannot_be_renamed(self):
        shared = Calendar.objects.create(name="Bob's", owner=self.other)
        CalendarSubscription.objects.create(user=self.user, calendar=shared)
        self.proppatch(
            "<d:displayname>Mine</d:displayname>",
            f"/caldav/calendars/alice/{shared.uuid}/",
        )
        shared.refresh_from_db()
        self.assertEqual(shared.name, "Bob's")
