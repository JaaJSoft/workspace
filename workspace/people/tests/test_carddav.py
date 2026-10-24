"""CardDAV end to end, the way a client walks it: discover, list, sync, write."""

import base64
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import Client, TestCase
from knox.models import AuthToken
from PIL import Image

from workspace.common.dav.auth import clear_auth_cache
from workspace.people.models import SOURCE_CARDDAV, Person, PersonList
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person, move_to_scope
from workspace.people.views.carddav import MAX_RESOURCE_SIZE

User = get_user_model()

D = "{DAV:}"
CR = "{urn:ietf:params:xml:ns:carddav}"
C = "{urn:ietf:params:xml:ns:caldav}"
CS = "{http://calendarserver.org/ns/}"

NAMESPACES = {
    "d": "DAV:",
    "card": "urn:ietf:params:xml:ns:carddav",
    "c": "urn:ietf:params:xml:ns:caldav",
    "cs": "http://calendarserver.org/ns/",
}
XMLNS = " ".join(f'xmlns:{p}="{uri}"' for p, uri in NAMESPACES.items())

IOS_CARDS = (Path(__file__).parent / "vcards" / "ios.vcf").read_text(encoding="utf-8")
JANE, FRIENDS = (f"BEGIN:VCARD{card}" for card in IOS_CARDS.split("BEGIN:VCARD")[1:])
JANE_UID = "1B7C0F3A-2E5D-4C8B-9F1E-3A2B1C0D9E8F"


def _propfind(*props):
    inner = "".join(f"<{p}/>" for p in props)
    return f"<d:propfind {XMLNS}><d:prop>{inner}</d:prop></d:propfind>".encode()


def _card(uid, name="Sam Smith", *lines):
    return "\r\n".join(
        [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"UID:{uid}",
            f"FN:{name}",
            "N:Smith;Sam;;;",
            *lines,
            "END:VCARD",
            "",
        ]
    )


def _jpeg(color=(200, 30, 30)):
    buf = BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format="JPEG")
    return buf.getvalue()


class CardDavTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="alice-pw-1"
        )
        cls.other = User.objects.create_user(username="bob", password="bob-pw-1")
        cls.team = Group.objects.create(name="Team")
        cls.user.groups.add(cls.team)
        cls.outsiders = Group.objects.create(name="Outsiders")
        cls.other.groups.add(cls.outsiders)

    def setUp(self):
        clear_auth_cache()
        # A DAV client sends no CSRF token: the endpoints must not need one.
        self.client = Client(enforce_csrf_checks=True)

    def tearDown(self):
        clear_auth_cache()
        cache.clear()

    book_path = "/carddav/addressbooks/alice/personal/"

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

    def put(self, name, body, book_path=None, **headers):
        if isinstance(body, str):
            body = body.encode()
        return self.dav(
            "PUT",
            f"{book_path or self.book_path}{name}",
            body,
            content_type="text/vcard",
            **headers,
        )

    def get(self, name, **headers):
        response = self.dav("GET", f"{self.book_path}{name}", **headers)
        self.assertEqual(response.status_code, 200, response.content)
        return response.content.decode()

    def etag_of(self, name):
        response = self.dav(
            "PROPFIND", f"{self.book_path}{name}", _propfind("d:getetag"), Depth="0"
        )
        return self.xml(response).findtext(f".//{D}getetag")

    def report(self, body, path=None):
        return self.dav("REPORT", path or self.book_path, body.encode(), Depth="1")


class DiscoveryTests(CardDavTestCase):
    def test_well_known_redirects_to_the_service(self):
        for method in ("GET", "PROPFIND"):
            with self.subTest(method=method):
                response = self.dav(method, "/.well-known/carddav", user=None)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/carddav/")

    def test_anonymous_is_challenged(self):
        response = self.dav("PROPFIND", "/carddav/", user=None)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response["WWW-Authenticate"])

    def test_options_advertises_addressbook(self):
        response = self.dav("OPTIONS", self.book_path)
        self.assertIn("addressbook", response["DAV"])
        self.assertIn("REPORT", response["Allow"])

    def test_principal_chain(self):
        root = self.dav(
            "PROPFIND", "/carddav/", _propfind("d:current-user-principal"), Depth="0"
        )
        principal = self.xml(root).findtext(f".//{D}current-user-principal/{D}href")
        self.assertEqual(principal, "/carddav/principals/alice/")

        response = self.dav(
            "PROPFIND", principal, _propfind("card:addressbook-home-set"), Depth="0"
        )
        self.assertEqual(
            self.xml(response).findtext(f".//{CR}addressbook-home-set/{D}href"),
            "/carddav/addressbooks/alice/",
        )

    def test_calendar_principal_points_at_the_contacts_too(self):
        # What a client given only the server address lands on (see wsgi.py).
        response = self.dav(
            "PROPFIND",
            "/caldav/principals/alice/",
            _propfind("card:addressbook-home-set", "c:calendar-home-set"),
            Depth="0",
        )
        tree = self.xml(response)
        self.assertEqual(
            tree.findtext(f".//{CR}addressbook-home-set/{D}href"),
            "/carddav/addressbooks/alice/",
        )
        self.assertEqual(
            tree.findtext(f".//{C}calendar-home-set/{D}href"),
            "/caldav/calendars/alice/",
        )

    def test_someone_elses_principal_and_home_are_not_found(self):
        for path in (
            "/carddav/principals/bob/",
            "/carddav/addressbooks/bob/",
            "/carddav/addressbooks/bob/personal/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.dav("PROPFIND", path, Depth="0").status_code, 404)

    def test_home_lists_the_personal_book_and_each_group_book(self):
        response = self.dav(
            "PROPFIND",
            "/carddav/addressbooks/alice/",
            _propfind(
                "d:resourcetype", "d:displayname", "d:current-user-privilege-set"
            ),
            Depth="1",
        )
        books = {
            href: r.findtext(f".//{D}displayname")
            for href, r in self.responses(response).items()
            if r.find(f".//{D}resourcetype/{CR}addressbook") is not None
        }
        self.assertEqual(
            books,
            {
                self.book_path: "My contacts",
                f"/carddav/addressbooks/alice/group-{self.team.pk}/": "Team",
            },
        )
        for href, entry in self.responses(response).items():
            if href in books:
                self.assertIsNotNone(entry.find(f".//{D}privilege/{D}write"))

    def test_a_group_book_of_a_group_the_user_is_not_in_is_not_found(self):
        for slug in (f"group-{self.outsiders.pk}", "group-999999", "group-x", "nope"):
            with self.subTest(slug=slug):
                response = self.dav(
                    "PROPFIND", f"/carddav/addressbooks/alice/{slug}/", Depth="0"
                )
                self.assertEqual(response.status_code, 404)

    def test_book_properties(self):
        response = self.dav(
            "PROPFIND",
            self.book_path,
            _propfind(
                "d:sync-token",
                "cs:getctag",
                "card:supported-address-data",
                "card:max-resource-size",
                "d:quota-used-bytes",
            ),
            Depth="0",
        )
        tree = self.xml(response)
        self.assertTrue(
            tree.findtext(f".//{D}sync-token").startswith(
                "urn:workspace:contacts-sync:"
            )
        )
        self.assertEqual(
            {
                e.get("version")
                for e in tree.iterfind(f".//{CR}supported-address-data/*")
            },
            {"3.0", "4.0"},
        )
        self.assertEqual(
            tree.findtext(f".//{CR}max-resource-size"), str(MAX_RESOURCE_SIZE)
        )
        missing = [
            p
            for propstat in tree.iter(f"{D}propstat")
            if propstat.findtext(f"{D}status").endswith("404 Not Found")
            for p in propstat.find(f"{D}prop")
        ]
        self.assertEqual([p.tag for p in missing], [f"{D}quota-used-bytes"])

    def test_infinite_depth_is_refused(self):
        response = self.dav(
            "PROPFIND", self.book_path, _propfind("d:getetag"), Depth="infinity"
        )
        self.assertEqual(response.status_code, 403)

    def test_api_token_authenticates_and_revoking_it_stops_syncing(self):
        instance, token = AuthToken.objects.create(self.user)
        self.assertEqual(
            self.dav("PROPFIND", self.book_path, password=token, Depth="0").status_code,
            207,
        )

        instance.delete()
        clear_auth_cache()  # what the cache TTL does on its own
        self.assertEqual(
            self.dav("PROPFIND", self.book_path, password=token, Depth="0").status_code,
            401,
        )

    def test_book_names_cannot_be_changed_from_a_client(self):
        response = self.dav(
            "PROPPATCH",
            self.book_path,
            f"<d:propertyupdate {XMLNS}><d:set><d:prop>"
            "<d:displayname>Mine</d:displayname></d:prop></d:set></d:propertyupdate>",
        )
        self.assertIn(b"403 Forbidden", response.content)

    def test_books_are_not_deleted(self):
        self.assertEqual(self.dav("DELETE", self.book_path).status_code, 403)


class CardTests(CardDavTestCase):
    def test_contact_created_on_a_phone_round_trips(self):
        created = self.put("jane.vcf", JANE, **{"If-None-Match": "*"})
        self.assertEqual(created.status_code, 201, created.content)
        person = Person.objects.get(owner=self.user)
        self.assertEqual(person.display_name, "Dr. Jane Marie Doe PhD")
        self.assertEqual(person.source, SOURCE_CARDDAV)
        self.assertEqual(person.dav_name, "jane.vcf")
        self.assertEqual(person.import_uid, JANE_UID)

        served = self.get("jane.vcf")
        self.assertTrue(served.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n"))
        # What the app does not display comes back as the phone sent it, but
        # for the case of names and parameters, which vCard ignores.
        for line in (
            f"UID:{JANE_UID}",
            "N:Doe;Jane;Marie;Dr.;PhD",
            "item2.URL;TYPE=pref:https://jane.example",
            "item2.X-ABLABEL:_$!<HomePage>!$_",
            "item1.X-ABADR:fr",
            "X-SOCIALPROFILE;TYPE=twitter:https://twitter.com/janedoe",
            "BDAY:1985-04-12",
        ):
            with self.subTest(line=line):
                self.assertIn(f"{line}\r\n", served)

    def test_putting_back_what_was_served_changes_nothing(self):
        self.put("jane.vcf", JANE)
        served = self.get("jane.vcf")
        self.assertEqual(
            self.put(
                "jane.vcf", served, **{"If-Match": self.etag_of("jane.vcf")}
            ).status_code,
            204,
        )
        self.assertEqual(self.get("jane.vcf"), served)
        self.assertEqual(Person.objects.count(), 1)

    def test_version_4_on_request(self):
        self.put("jane.vcf", JANE)
        served = self.get("jane.vcf", Accept="text/vcard; version=4.0")
        self.assertIn("VERSION:4.0\r\n", served)
        self.assertIn("BDAY:19850412\r\n", served)

    def test_contact_created_in_the_app_is_served_and_writable(self):
        person = create_person(
            owner=self.user,
            display_name="Zoe",
            emails=[{"value": "zoe@example.com", "type": "work"}],
        )
        name = f"{person.uuid}.vcf"
        served = self.get(name)
        self.assertIn(f"UID:urn:uuid:{person.uuid}\r\n", served)
        self.assertIn("EMAIL;TYPE=work:zoe@example.com\r\n", served)

        edited = served.replace("FN:Zoe", "FN:Zoe Q.")
        response = self.put(name, edited, **{"If-Match": self.etag_of(name)})
        self.assertEqual(response.status_code, 204, response.content)
        person.refresh_from_db()
        self.assertEqual(person.display_name, "Zoe Q.")
        self.assertEqual(person.import_uid, "")
        self.assertEqual(person.dav_name, "")

        self.assertEqual(self.dav("DELETE", f"{self.book_path}{name}").status_code, 204)
        self.assertFalse(Person.objects.filter(pk=person.pk).exists())

    def test_stale_if_match_does_not_overwrite(self):
        self.put("sam.vcf", _card("sam-1"))
        response = self.put(
            "sam.vcf", _card("sam-1", "Changed"), **{"If-Match": '"stale"'}
        )
        self.assertEqual(response.status_code, 412)
        self.assertEqual(Person.objects.get().display_name, "Sam Smith")

    def test_if_none_match_refuses_to_overwrite(self):
        self.put("sam.vcf", _card("sam-1"))
        response = self.put("sam.vcf", _card("sam-1"), **{"If-None-Match": "*"})
        self.assertEqual(response.status_code, 412)

    def test_stale_delete_is_refused(self):
        self.put("sam.vcf", _card("sam-1"))
        response = self.dav(
            "DELETE", f"{self.book_path}sam.vcf", **{"If-Match": '"stale"'}
        )
        self.assertEqual(response.status_code, 412)
        self.assertEqual(Person.objects.count(), 1)

    def test_invalid_bodies_name_the_broken_rule(self):
        for body in (b"not a vcard", _card("a") + _card("b"), b"\xff\xfe"):
            with self.subTest(body=body[:20]):
                response = self.put("bad.vcf", body)
                self.assertEqual(response.status_code, 403)
                self.assertIsNotNone(self.xml(response).find(f"{CR}valid-address-data"))
        self.assertFalse(Person.objects.exists())

    def test_uid_used_by_another_card_is_a_conflict(self):
        self.put("one.vcf", _card("same-uid"))
        for uid in ("same-uid", "SAME-UID", "urn:uuid:same-uid"):
            with self.subTest(uid=uid):
                response = self.put("two.vcf", _card(uid))
                self.assertEqual(response.status_code, 409)
                self.assertIsNotNone(self.xml(response).find(f"{CR}no-uid-conflict"))

    def test_our_own_uid_cannot_be_reused(self):
        person = create_person(owner=self.user, display_name="Zoe")
        response = self.put("copy.vcf", _card(f"urn:uuid:{person.uuid}"))
        self.assertEqual(response.status_code, 409)

    def test_uid_cannot_change(self):
        self.put("sam.vcf", _card("sam-1"))
        response = self.put("sam.vcf", _card("sam-2"))
        self.assertEqual(response.status_code, 409)

    def test_same_uid_in_another_book_is_no_conflict(self):
        create_person(owner=self.other, display_name="Bob's", import_uid="shared-uid")
        self.assertEqual(self.put("x.vcf", _card("shared-uid")).status_code, 201)

    def test_oversized_body_is_refused(self):
        body = _card("big", "Big", f"NOTE:{'x' * MAX_RESOURCE_SIZE}")
        response = self.put("big.vcf", body)
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.xml(response).find(f"{CR}max-resource-size"))

    def test_someone_elses_contact_is_out_of_reach(self):
        person = create_person(owner=self.other, display_name="Bob's friend")
        path = f"{self.book_path}{person.uuid}.vcf"
        self.assertEqual(self.dav("GET", path).status_code, 404)
        self.assertEqual(self.dav("DELETE", path).status_code, 404)
        group_person = create_person(group=self.outsiders, display_name="Outsider")
        path = f"/carddav/addressbooks/alice/group-{self.outsiders.pk}/{group_person.uuid}.vcf"
        self.assertEqual(self.dav("GET", path).status_code, 404)

    def test_group_book_is_shared_and_writable(self):
        book = f"/carddav/addressbooks/alice/group-{self.team.pk}/"
        response = self.put("sam.vcf", _card("sam-1"), book_path=book)
        self.assertEqual(response.status_code, 201)
        person = Person.objects.get()
        self.assertEqual((person.owner, person.group), (None, self.team))
        # The personal book does not see it.
        self.assertEqual(self.dav("GET", f"{self.book_path}sam.vcf").status_code, 404)

    def test_photo_is_stored_at_avatar_size_and_served_as_jpeg(self):
        photo = base64.b64encode(_jpeg()).decode()
        self.put(
            "sam.vcf", _card("sam-1", "Sam", f"PHOTO;ENCODING=b;TYPE=JPEG:{photo}")
        )
        person = Person.objects.get()
        self.assertTrue(person.has_avatar)

        served = self.get("sam.vcf")
        line = next(x for x in served.split("\r\n") if x.startswith("PHOTO"))
        self.assertTrue(line.startswith("PHOTO;ENCODING=b;TYPE=JPEG:"))
        with Image.open(BytesIO(base64.b64decode(line.split(":", 1)[1]))) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (256, 256)))

        # The served image sent back as is is not re-encoded.
        updated_at = person.updated_at
        self.put("sam.vcf", served)
        self.assertIn(line, self.get("sam.vcf"))
        person.refresh_from_db()
        self.assertGreater(person.updated_at, updated_at)

        # A card without the photo removes it.
        self.put("sam.vcf", _card("sam-1", "Sam"))
        person.refresh_from_db()
        self.assertFalse(person.has_avatar)
        self.assertNotIn("PHOTO", self.get("sam.vcf"))


class ListCardTests(CardDavTestCase):
    def test_group_card_before_its_members_keeps_them(self):
        # Order of upload is the device's business: the group may come first.
        self.assertEqual(self.put("friends.vcf", FRIENDS).status_code, 201)
        person_list = PersonList.objects.get()
        self.assertEqual(person_list.name, "Friends")
        self.assertEqual(person_list.members.count(), 0)
        served = self.get("friends.vcf")
        self.assertIn(f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{JANE_UID}\r\n", served)

        self.put("jane.vcf", JANE)
        jane = Person.objects.get()
        self.assertEqual(list(person_list.members.all()), [jane])
        person_list.refresh_from_db()
        self.assertEqual(
            person_list.pending_member_uids,
            ["urn:uuid:00000000-0000-0000-0000-00000000dead"],
        )
        served = self.get("friends.vcf")
        self.assertEqual(served.count(f"MEMBER:urn:uuid:{JANE_UID}"), 1)
        self.assertIn("X-ADDRESSBOOKSERVER-KIND:group\r\n", served)
        self.assertIn("UID:7D5E2B10-9A8C-4F3E-B2D1-5C6A7B8C9D0E\r\n", served)

    def test_list_from_the_app_is_served_with_its_members(self):
        person = create_person(owner=self.user, display_name="Zoe")
        person_list = create_list(owner=self.user, name="Family")
        add_members(person_list, [person])
        served = self.get(f"{person_list.uuid}.vcf")
        self.assertIn(f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{person.uuid}\r\n", served)
        served_v4 = self.get(f"{person_list.uuid}.vcf", Accept="text/vcard;version=4.0")
        self.assertIn("KIND:group\r\n", served_v4)
        self.assertIn(f"MEMBER:urn:uuid:{person.uuid}\r\n", served_v4)

    def test_editing_the_group_on_a_device_updates_the_list(self):
        zoe = create_person(owner=self.user, display_name="Zoe")
        sam = create_person(owner=self.user, display_name="Sam", import_uid="sam-1")
        person_list = create_list(owner=self.user, name="Family")
        add_members(person_list, [zoe])
        name = f"{person_list.uuid}.vcf"
        served = self.get(name)
        edited = served.replace("FN:Family", "FN:Kin").replace(
            f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{zoe.uuid}",
            "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:sam-1",
        )
        self.assertEqual(self.put(name, edited).status_code, 204)
        person_list.refresh_from_db()
        self.assertEqual(person_list.name, "Kin")
        self.assertEqual(list(person_list.members.all()), [sam])

    def test_a_group_named_like_another_list_gets_a_free_name(self):
        create_list(owner=self.user, name="Friends")
        self.assertEqual(self.put("friends.vcf", FRIENDS).status_code, 201)
        self.assertEqual(
            sorted(PersonList.objects.values_list("name", flat=True)),
            ["Friends", "Friends (2)"],
        )

    def test_a_contact_cannot_become_a_list(self):
        self.put("x.vcf", _card("x-1"))
        group = FRIENDS.replace("UID:7D5E2B10-9A8C-4F3E-B2D1-5C6A7B8C9D0E", "UID:x-1")
        response = self.put("x.vcf", group)
        self.assertEqual(response.status_code, 403)

    def test_deleting_the_group_keeps_the_contacts(self):
        self.put("jane.vcf", JANE)
        self.put("friends.vcf", FRIENDS)
        self.assertEqual(
            self.dav("DELETE", f"{self.book_path}friends.vcf").status_code, 204
        )
        self.assertFalse(PersonList.objects.exists())
        self.assertEqual(Person.objects.count(), 1)


class ReportTests(CardDavTestCase):
    def setUp(self):
        super().setUp()
        self.put("jane.vcf", JANE)
        self.put(
            "sam.vcf", _card("sam-1", "Sam Smith", "EMAIL;TYPE=WORK:sam@corp.example")
        )
        self.zoe = create_person(owner=self.user, display_name="Zoe")
        self.zoe_href = f"{self.book_path}{self.zoe.uuid}.vcf"

    def _query(self, filter_xml, limit=""):
        response = self.report(
            f"<card:addressbook-query {XMLNS}><d:prop><d:getetag/></d:prop>"
            f"{filter_xml}{limit}</card:addressbook-query>"
        )
        return set(self.responses(response))

    def test_depth_one_propfind_lists_cards_with_etags(self):
        response = self.dav(
            "PROPFIND", self.book_path, _propfind("d:getetag"), Depth="1"
        )
        entries = self.responses(response)
        self.assertEqual(
            set(entries),
            {
                self.book_path,
                f"{self.book_path}jane.vcf",
                f"{self.book_path}sam.vcf",
                self.zoe_href,
            },
        )
        self.assertTrue(entries[f"{self.book_path}jane.vcf"].findtext(f".//{D}getetag"))

    def test_multiget(self):
        response = self.report(
            f"<card:addressbook-multiget {XMLNS}>"
            '<d:prop><d:getetag/><card:address-data version="4.0"/></d:prop>'
            f"<d:href>{self.book_path}jane.vcf</d:href>"
            f"<d:href>{self.book_path}missing.vcf</d:href>"
            "</card:addressbook-multiget>"
        )
        entries = self.responses(response)
        data = entries[f"{self.book_path}jane.vcf"].findtext(f".//{CR}address-data")
        self.assertIn("VERSION:4.0", data)
        self.assertIn(f"UID:{JANE_UID}", data)
        self.assertIn(
            "404", entries[f"{self.book_path}missing.vcf"].findtext(f"{D}status")
        )

    def test_query_filters(self):
        jane = f"{self.book_path}jane.vcf"
        sam = f"{self.book_path}sam.vcf"
        self.assertEqual(self._query(""), {jane, sam, self.zoe_href})
        self.assertEqual(
            self._query(
                '<card:filter><card:prop-filter name="FN">'
                "<card:text-match>SMITH</card:text-match>"
                "</card:prop-filter></card:filter>"
            ),
            {sam},
        )
        self.assertEqual(
            self._query(
                '<card:filter><card:prop-filter name="EMAIL">'
                '<card:text-match match-type="equals">jane@acme.example</card:text-match>'
                "</card:prop-filter></card:filter>"
            ),
            {jane},
        )
        self.assertEqual(
            self._query(
                '<card:filter test="allof">'
                '<card:prop-filter name="EMAIL"><card:is-not-defined/></card:prop-filter>'
                '<card:prop-filter name="FN">'
                '<card:text-match match-type="starts-with">z</card:text-match>'
                "</card:prop-filter></card:filter>"
            ),
            {self.zoe_href},
        )
        self.assertEqual(
            self._query(
                '<card:filter><card:prop-filter name="FN">'
                '<card:text-match negate-condition="yes">a</card:text-match>'
                "</card:prop-filter></card:filter>"
            ),
            {self.zoe_href},
        )
        self.assertEqual(
            self._query(
                '<card:filter><card:prop-filter name="TEL">'
                '<card:param-filter name="TYPE">'
                "<card:text-match>fax</card:text-match>"
                "</card:param-filter></card:prop-filter></card:filter>"
            ),
            {jane},
        )
        self.assertEqual(
            len(
                self._query(
                    "", "<card:limit><card:nresults>2</card:nresults></card:limit>"
                )
            ),
            2,
        )

    def test_unsupported_report(self):
        response = self.report(
            '<c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav"/>'
        )
        self.assertEqual(response.status_code, 403)


class SyncTests(CardDavTestCase):
    def setUp(self):
        super().setUp()
        self.put("jane.vcf", JANE)
        self.zoe = create_person(owner=self.user, display_name="Zoe")
        self.zoe_href = f"{self.book_path}{self.zoe.uuid}.vcf"

    def _sync(self, token="", path=None):
        response = self.report(
            f'<d:sync-collection xmlns:d="DAV:">'
            f"<d:sync-token>{token}</d:sync-token><d:sync-level>1</d:sync-level>"
            "<d:prop><d:getetag/></d:prop></d:sync-collection>",
            path=path,
        )
        self.assertEqual(response.status_code, 207, response.content)
        tree = self.xml(response)
        entries = {
            r.findtext(f"{D}href"): (r.findtext(f"{D}status") or "changed")
            for r in tree.iter(f"{D}response")
        }
        return entries, tree.findtext(f"{D}sync-token")

    def test_sync_collection(self):
        jane = f"{self.book_path}jane.vcf"
        everything, token = self._sync()
        self.assertEqual(set(everything), {jane, self.zoe_href})

        nothing, same = self._sync(token)
        self.assertEqual(nothing, {})
        self.assertEqual(same, token)

        # Changes made in the app reach the phone on its next sync.
        self.zoe.display_name = "Zoe Q."
        self.zoe.save(update_fields=["display_name"])
        added = create_person(owner=self.user, display_name="New")
        self.dav("DELETE", jane)

        changes, newer = self._sync(token)
        self.assertNotEqual(newer, token)
        self.assertEqual(changes.pop(jane), "HTTP/1.1 404 Not Found")
        self.assertEqual(
            changes,
            {self.zoe_href: "changed", f"{self.book_path}{added.uuid}.vcf": "changed"},
        )

    def test_moving_a_contact_to_a_group_book_moves_it_between_books(self):
        group_book = f"/carddav/addressbooks/alice/group-{self.team.pk}/"
        _, personal_token = self._sync()
        _, group_token = self._sync(path=group_book)

        move_to_scope(self.zoe, group=self.team)

        changes, _ = self._sync(personal_token)
        self.assertEqual(changes, {self.zoe_href: "HTTP/1.1 404 Not Found"})
        changes, _ = self._sync(group_token, path=group_book)
        self.assertEqual(changes, {f"{group_book}{self.zoe.uuid}.vcf": "changed"})

    def test_list_membership_changed_in_the_app_reaches_the_list_card(self):
        person_list = create_list(owner=self.user, name="Family")
        list_href = f"{self.book_path}{person_list.uuid}.vcf"
        _, token = self._sync()

        add_members(person_list, [self.zoe])
        changes, token = self._sync(token)
        self.assertEqual(changes, {list_href: "changed"})

        self.zoe.delete()
        changes, _ = self._sync(token)
        self.assertEqual(
            changes, {list_href: "changed", self.zoe_href: "HTTP/1.1 404 Not Found"}
        )

    def test_repeated_syncs_report_no_spurious_change(self):
        _, token = self._sync()
        body = self.get("jane.vcf")
        etag = self.etag_of("jane.vcf")

        for _ in range(3):
            changes, token = self._sync(token)
            self.assertEqual(changes, {})
        self.assertEqual(self.etag_of("jane.vcf"), etag)
        self.assertEqual(self.get("jane.vcf"), body)
        self.assertEqual(Person.objects.count(), 2)

    def test_sync_with_a_foreign_token_is_refused(self):
        for token in ("garbage", "urn:workspace:contacts-sync:999999"):
            with self.subTest(token=token):
                response = self.report(
                    '<d:sync-collection xmlns:d="DAV:">'
                    f"<d:sync-token>{token}</d:sync-token><d:sync-level>1</d:sync-level>"
                    "<d:prop><d:getetag/></d:prop></d:sync-collection>"
                )
                self.assertEqual(response.status_code, 403)
                self.assertIsNotNone(self.xml(response).find(f"{D}valid-sync-token"))

    def test_ctag_and_etag_move_with_the_content(self):
        def ctag():
            response = self.dav(
                "PROPFIND", self.book_path, _propfind("cs:getctag"), Depth="0"
            )
            return self.xml(response).findtext(f".//{CS}getctag")

        before, etag = ctag(), self.etag_of(f"{self.zoe.uuid}.vcf")
        self.assertEqual(ctag(), before)
        self.zoe.notes = "Changed in the app"
        self.zoe.save(update_fields=["notes"])
        self.assertNotEqual(ctag(), before)
        self.assertNotEqual(self.etag_of(f"{self.zoe.uuid}.vcf"), etag)
