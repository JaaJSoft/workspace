from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from workspace.core.module_registry import registry
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()


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
        self.assertContains(response, "team")
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
