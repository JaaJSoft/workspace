import re
from itertools import batched

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from workspace.core.module_registry import registry
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()

HEADER_RE = r'<div[^>]*data-letter="([^"]+)"'
ROW_NAME_RE = r'<span class="font-medium truncate">([^<]*)</span>'


class PeopleModuleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@test.com", password="pass123"
        )

    def test_module_is_registered(self):
        module = registry.get("people")
        self.assertIsNotNone(module)
        self.assertEqual(module.url, "/people")

    def test_index_requires_login(self):
        response = self.client.get(reverse("people_ui:index"))
        self.assertEqual(response.status_code, 302)

    def test_index_renders(self):
        self.client.force_login(self.user)
        response = self.client.get("/people")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "People")


class PeopleIndexTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.team = Group.objects.create(name="team")
        self.user.groups.add(self.team)
        self.client.force_login(self.user)
        self.bob = create_person(owner=self.user, display_name="Bob Martin")
        self.carol = create_person(
            group=self.team, display_name="Carol Team", organization="ACME"
        )
        self.family = create_list(owner=self.user, name="Family")
        add_members(self.family, [self.bob])

    def test_page_lists_persons_and_sidebar(self):
        response = self.client.get("/people")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bob Martin")
        self.assertContains(response, "Carol Team")
        self.assertContains(response, "Family")
        self.assertContains(response, f'data-scope="group:{self.team.id}"')
        self.assertContains(response, 'id="person-list"')

    def test_fragment_under_alpine_request(self):
        response = self.client.get(
            "/people", {"q": "carol"}, HTTP_X_ALPINE_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Carol Team")
        self.assertNotContains(response, "Bob Martin")
        self.assertNotContains(response, "<html")

    def test_scope_and_list_filters(self):
        response = self.client.get("/people", {"scope": f"group:{self.team.id}"})
        self.assertContains(response, "Carol Team")
        self.assertNotContains(response, "Bob Martin")
        response = self.client.get("/people", {"list": str(self.family.uuid)})
        self.assertContains(response, "Bob Martin")
        self.assertNotContains(response, "Carol Team")

    def test_bad_filters_are_ignored(self):
        response = self.client.get("/people", {"list": "nope", "scope": "group:999"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bob Martin")
        # Only the filters the listing honoured are echoed back, or the page
        # would reopen filtered by something it never applied.
        self.assertEqual(response.context["scope"], "")
        self.assertEqual(response.context["list_uuid"], "")

    def test_unreachable_list_is_ignored(self):
        stranger = User.objects.create_user(username="mallory", password="x")
        foreign = create_list(owner=stranger, name="Theirs")
        response = self.client.get("/people", {"list": str(foreign.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bob Martin")
        self.assertEqual(response.context["list_uuid"], "")


class PersonLetterGroupingTests(TestCase):
    """One header per letter, accent-folded, with ``#`` last.

    The database collation is not the bucketing function: SQLite sorts
    BINARY (`Zoe` before `alain`) and PostgreSQL interleaves accents, so a
    listing ordered by the database and grouped in Python splits a letter
    into several headers.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.client.force_login(self.user)
        for name in ("4Front Studio", "alain", "Zoe", "Élodie", "Eddy"):
            create_person(owner=self.user, display_name=name)

    def _sections(self, response):
        """[(letter, [display_name, ...]), ...] in the order they render."""
        parts = re.split(HEADER_RE, response.content.decode())
        return [
            (letter, re.findall(ROW_NAME_RE, chunk))
            for letter, chunk in batched(parts[1:], 2, strict=False)
        ]

    def test_headers_are_unique_folded_and_hash_last(self):
        response = self.client.get("/people")
        sections = self._sections(response)
        self.assertEqual([letter for letter, _ in sections], ["A", "E", "Z", "#"])
        by_letter = dict(sections)
        self.assertEqual(by_letter["A"], ["alain"])
        self.assertEqual(by_letter["E"], ["Eddy", "Élodie"])
        self.assertEqual(by_letter["Z"], ["Zoe"])
        self.assertEqual(by_letter["#"], ["4Front Studio"])
