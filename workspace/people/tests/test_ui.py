import re
from itertools import batched
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escapejs

from workspace.core.module_registry import registry
from workspace.people.sections import PersonSection, section_registry
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()

HEADER_RE = r'<div[^>]*data-letter="([^"]+)"'
ROW_NAME_RE = r'<span class="font-medium truncate">([^<]*)</span>'
TEST_TEMPLATES = Path(__file__).parent / "templates"


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
        self.assertEqual(
            [g for g in response.context["groups_data"] if g["id"] == self.team.id],
            [{"id": self.team.id, "name": "team", "person_count": 1}],
        )
        self.assertContains(response, 'id="person-list"')

    def test_a_group_without_contacts_counts_zero(self):
        empty = Group.objects.create(name="empty")
        self.user.groups.add(empty)
        response = self.client.get("/people")
        counts = {g["name"]: g["person_count"] for g in response.context["groups_data"]}
        self.assertEqual(counts, {"empty": 0, "team": 1})

    def test_page_carries_the_personal_count_for_the_export_dialog(self):
        response = self.client.get("/people")
        self.assertEqual(response.context["mine_count"], 1)
        self.assertContains(response, 'id="people-mine-count-data"')

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

    def test_sidebar_lists_carry_their_scope(self):
        group_list = create_list(group=self.team, name="Clients")
        response = self.client.get("/people")
        scopes = {
            entry["name"]: entry["scope"] for entry in response.context["lists_data"]
        }
        self.assertEqual(scopes["Family"], "mine")
        self.assertEqual(scopes["Clients"], f"group:{self.team.id}")
        self.assertEqual(group_list.group, self.team)

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


class PersonPanelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.other = User.objects.create_user(username="bob", password="x")
        self.client.force_login(self.user)
        self.person = create_person(
            owner=self.user,
            display_name="Bob Martin",
            emails=[{"value": "bob@acme.com", "type": "work"}],
        )
        self.hidden = create_person(owner=self.other, display_name="Hidden")

    def tearDown(self):
        section_registry.unregister("fake")

    def test_panel_renders_person_and_actions(self):
        response = self.client.get(f"/people/{self.person.uuid}/panel")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bob Martin")
        self.assertContains(response, 'id="person-panel-data"')
        self.assertContains(response, 'id="person-panel-actions"')
        self.assertContains(response, '"id": "delete"')

    def test_panel_unreachable_is_404(self):
        response = self.client.get(f"/people/{self.hidden.uuid}/panel")
        self.assertEqual(response.status_code, 404)

    def test_panel_offers_only_the_lists_of_the_person_scope(self):
        family = create_list(owner=self.user, name="Family")
        add_members(family, [self.person])
        create_list(owner=self.user, name="Work")
        team = Group.objects.create(name="team")
        self.user.groups.add(team)
        create_list(group=team, name="Teammates")

        response = self.client.get(f"/people/{self.person.uuid}/panel")
        offered = {
            entry["name"]: entry["member"] for entry in response.context["lists_data"]
        }
        # A list only accepts members of its own scope, so a team list is not
        # offered for a personal contact, whatever the user can otherwise see.
        self.assertNotIn("Teammates", offered)
        self.assertIs(offered["Family"], True)
        self.assertIs(offered["Work"], False)

    def test_one_swap_target_in_the_page_and_in_the_fragment(self):
        # alpine-ajax replaces the first element carrying the id; a second one
        # would be swapped around silently.
        page = self.client.get("/people")
        self.assertEqual(page.content.decode().count('id="person-panel"'), 1)
        fragment = self.client.get(f"/people/{self.person.uuid}/panel")
        self.assertEqual(fragment.content.decode().count('id="person-panel"'), 1)

    def test_panel_includes_registered_sections(self):
        section_registry.register(
            PersonSection(
                slug="fake",
                label="Fake section",
                icon="star",
                template="people/tests/hello.html",
            )
        )
        with self.settings(
            TEMPLATES=[
                {
                    **settings.TEMPLATES[0],
                    "DIRS": [*settings.TEMPLATES[0]["DIRS"], TEST_TEMPLATES],
                }
            ]
        ):
            response = self.client.get(f"/people/{self.person.uuid}/panel")
        self.assertContains(response, "Fake section")
        self.assertContains(response, "Hello Bob Martin")

    def test_deep_link_with_a_malformed_uuid_opens_nothing(self):
        # The shell would otherwise ask /people/<garbage>/panel, get a 404 and
        # leave an empty panel open behind an error toast.
        response = self.client.get("/people", {"person": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["panel_person_uuid"], "")
        self.assertContains(response, "initialPerson: ''")

    def test_deep_link_opens_panel(self):
        response = self.client.get("/people", {"person": str(self.person.uuid)})
        # The config object goes through |escapejs, which escapes every hyphen
        # of the uuid: the browser reads the literal back as the uuid, but the
        # rendered source never carries the bare one.
        self.assertContains(response, f"initialPerson: '{escapejs(self.person.uuid)}'")
