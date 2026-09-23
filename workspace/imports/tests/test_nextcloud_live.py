"""Imports against a real Nextcloud server.

Skipped unless WORKSPACE_NEXTCLOUD_URL points at a Nextcloud instance whose
admin password is in WORKSPACE_NEXTCLOUD_ADMIN_PASSWORD (the admin login
defaults to ``admin``, WORKSPACE_NEXTCLOUD_ADMIN_USER overrides it). The CI
job ``nextcloud-live`` runs one in a service container and also sets
WORKSPACE_NEXTCLOUD_TESTS=1, which turns a missing server into a failure
rather than a silent skip.

Each run provisions a fresh Nextcloud user, fills its address books over
CardDAV the way a phone would, and drives the real import code against it:
what the unit tests assume about Nextcloud's answers is checked here.
"""

import base64
import io
import os
import secrets
import unittest
from urllib.parse import urlparse

import httpx2
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from PIL import Image

from workspace.common.booleans import is_truthy
from workspace.imports.models import ImportJob
from workspace.imports.providers.webdav import DAV, parse_dav_xml
from workspace.imports.services import connections, jobs
from workspace.people.models import Person, PersonList

URL = os.environ.get("WORKSPACE_NEXTCLOUD_URL", "").rstrip("/")
ADMIN_USER = os.environ.get("WORKSPACE_NEXTCLOUD_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("WORKSPACE_NEXTCLOUD_ADMIN_PASSWORD", "")
REQUIRED = is_truthy(os.environ.get("WORKSPACE_NEXTCLOUD_TESTS"))

User = get_user_model()

_VCARD_TYPE = {"Content-Type": "text/vcard; charset=utf-8"}
_MKCOL_TEAM = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:mkcol xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    b"<d:set><d:prop>"
    b"<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>"
    b"<d:displayname>Team</d:displayname>"
    b"<card:addressbook-description>Shared by the office</card:addressbook-description>"
    b"</d:prop></d:set></d:mkcol>"
)
_PROPFIND_TYPES = (
    b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
    b"<d:prop><d:resourcetype/></d:prop></d:propfind>"
)


def _client(user, password):
    return httpx2.Client(
        base_url=URL, auth=httpx2.BasicAuth(user, password), timeout=60
    )


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _vcard(uid, name, *lines):
    body = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}", f"FN:{name}", *lines]
    return ("\r\n".join([*body, "END:VCARD"]) + "\r\n").encode()


@unittest.skipUnless(
    URL or REQUIRED, "set WORKSPACE_NEXTCLOUD_URL to run the live Nextcloud tests"
)
@override_settings(IMPORTS_ALLOWED_HOSTS=[urlparse(URL).hostname or ""])
class LiveNextcloudContactsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        if not (URL and ADMIN_PASSWORD):
            raise AssertionError(
                "WORKSPACE_NEXTCLOUD_TESTS=1 but WORKSPACE_NEXTCLOUD_URL or "
                "WORKSPACE_NEXTCLOUD_ADMIN_PASSWORD is not set."
            )
        cls.nc_user = f"ws-{secrets.token_hex(4)}"
        cls.nc_password = secrets.token_urlsafe(18)
        cls.home = f"/remote.php/dav/addressbooks/users/{cls.nc_user}"
        with _client(ADMIN_USER, ADMIN_PASSWORD) as admin:
            status = admin.get("/status.php").json()
            if not status.get("installed"):
                raise AssertionError(f"Nextcloud at {URL} is not installed: {status}")
            response = admin.post(
                "/ocs/v2.php/cloud/users",
                params={"format": "json"},
                data={"userid": cls.nc_user, "password": cls.nc_password},
                headers={"OCS-APIRequest": "true"},
            )
            if response.status_code != 200:
                raise AssertionError(
                    f"Could not create the Nextcloud user: {response.text[:500]}"
                )
        cls._seed()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with _client(ADMIN_USER, ADMIN_PASSWORD) as admin:
            admin.delete(
                f"/ocs/v2.php/cloud/users/{cls.nc_user}",
                headers={"OCS-APIRequest": "true"},
            )

    @classmethod
    def _seed(cls):
        cards = {
            "contacts/ann.vcf": _vcard(
                "ann",
                "Ann Archer",
                "EMAIL;TYPE=work:ann@example.org",
                "CATEGORIES:Friends,Climbing",
                f"PHOTO;ENCODING=b;TYPE=PNG:{_png((37, 99, 235))}",
            ),
            "contacts/bob.vcf": _vcard(
                "bob", "Bob Baker", "EMAIL:bob@example.org", "CATEGORIES:Friends"
            ),
            "contacts/carol.vcf": _vcard("carol", "Carol Chen", "BDAY:1990-04-12"),
            "team/dave.vcf": _vcard(
                "dave",
                "Dave Dupont",
                "CATEGORIES:Suppliers",
                f"PHOTO;ENCODING=b;TYPE=PNG:{_png((219, 39, 119))}",
            ),
            # Nextcloud serves a card's photo at ?photo: a link to it is the
            # "photo given by URL" case, on the connection's own origin.
            "team/eve.vcf": _vcard(
                "eve",
                "Eve Evans",
                "CATEGORIES:Clients",
                f"PHOTO;VALUE=uri:{URL}{cls.home}/team/dave.vcf?photo",
            ),
        }
        with _client(cls.nc_user, cls.nc_password) as nc:
            # The first request to the home creates the default "contacts" book.
            nc.request("PROPFIND", f"{cls.home}/", headers={"Depth": "0"})
            created = nc.request(
                "MKCOL",
                f"{cls.home}/team/",
                content=_MKCOL_TEAM,
                headers={"Content-Type": "application/xml"},
            )
            if created.status_code != 201:
                raise AssertionError(f"MKCOL team: HTTP {created.status_code}")
            for path, body in cards.items():
                put = nc.put(f"{cls.home}/{path}", content=body, headers=_VCARD_TYPE)
                if put.status_code not in (201, 204):
                    raise AssertionError(f"PUT {path}: HTTP {put.status_code}")

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.office = Group.objects.create(name="Office")
        self.user.groups.add(self.office)
        self.conn = connections.create_connection(
            self.user,
            provider="nextcloud",
            label="Nextcloud",
            base_url=URL,
            username=self.nc_user,
            secret=self.nc_password,
        )

    def tearDown(self):
        cache.clear()

    def _import(self):
        options = {
            "contacts": {
                "books": [
                    {"id": "/contacts", "target": "mine"},
                    {"id": "/team", "target": f"group:{self.office.pk}"},
                ]
            }
        }
        job = jobs.create_job(self.user, self.conn, ["contacts"], options)
        jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, ImportJob.Status.COMPLETED, job.error)
        self.assertFalse(job.stats["contacts"].get("failed"), job.stats)
        return job.stats["contacts"]

    def test_the_connection_offers_contacts(self):
        self.assertIn("contacts", self.conn.capabilities["kinds"])
        self.assertTrue(self.conn.capabilities.get("server_version"))

    def test_only_the_users_own_address_books_are_offered(self):
        books = connections.browse_address_books(self.conn)
        self.assertEqual(
            [(b.id, b.name, b.description) for b in books],
            [("/contacts", "Contacts", ""), ("/team", "Team", "Shared by the office")],
        )
        # The server does list generated books next to them, so the filter
        # that hides them is what the assertion above exercises.
        with _client(self.nc_user, self.nc_password) as nc:
            listing = nc.request(
                "PROPFIND",
                f"{self.home}/",
                content=_PROPFIND_TYPES,
                headers={"Depth": "1", "Content-Type": "application/xml"},
            )
        hrefs = [h.text for h in parse_dav_xml(listing.content).iter(f"{DAV}href")]
        self.assertTrue(any("-generated--" in href for href in hrefs), hrefs)

    def test_contacts_are_imported_then_only_changes_are_fetched(self):
        first = self._import()

        mine = {p.display_name: p for p in Person.objects.filter(owner=self.user)}
        team = {p.display_name: p for p in Person.objects.filter(group=self.office)}
        self.assertLessEqual({"Ann Archer", "Bob Baker", "Carol Chen"}, set(mine))
        self.assertEqual(set(team), {"Dave Dupont", "Eve Evans"})
        self.assertTrue(mine["Ann Archer"].has_avatar)
        self.assertFalse(mine["Bob Baker"].has_avatar)
        self.assertTrue(team["Dave Dupont"].has_avatar)
        self.assertTrue(team["Eve Evans"].has_avatar)
        # Nextcloud gives every new user an example contact whose image is
        # followed by empty PHOTO lines.
        if "Leon Green" in mine:
            self.assertTrue(mine["Leon Green"].has_avatar)

        lists = {
            (pl.name, "mine" if pl.owner_id else "team"): sorted(
                pl.members.values_list("display_name", flat=True)
            )
            for pl in PersonList.objects.all()
        }
        self.assertEqual(
            lists,
            {
                ("Friends", "mine"): ["Ann Archer", "Bob Baker"],
                ("Climbing", "mine"): ["Ann Archer"],
                ("Suppliers", "team"): ["Dave Dupont"],
                ("Clients", "team"): ["Eve Evans"],
            },
        )

        again = self._import()
        self.assertEqual(again["unchanged"], first["total_cards"])
        self.assertFalse(again.get("created") or again.get("updated"), again)

        with _client(self.nc_user, self.nc_password) as nc:
            nc.put(
                f"{self.home}/contacts/bob.vcf",
                content=_vcard("bob", "Robert Baker", "EMAIL:bob@example.org"),
                headers=_VCARD_TYPE,
            )
        changed = self._import()
        self.assertEqual(changed.get("updated"), 1, changed)
        self.assertEqual(changed["unchanged"], first["total_cards"] - 1)
        self.assertTrue(
            Person.objects.filter(owner=self.user, display_name="Robert Baker").exists()
        )
