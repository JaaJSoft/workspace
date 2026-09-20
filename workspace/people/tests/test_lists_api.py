from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.people.models import PersonList
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()


class PersonListApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)
        self.client.force_authenticate(self.alice)

    def test_create_list_defaults_to_mine(self):
        response = self.client.post(
            "/api/v1/people/lists", {"name": "Family"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["scope"], "mine")
        self.assertEqual(response.data["member_count"], 0)

    def test_create_group_list(self):
        response = self.client.post(
            "/api/v1/people/lists",
            {"name": "Clients", "scope": f"group:{self.team.id}"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_duplicate_name_is_400(self):
        create_list(owner=self.alice, name="Family")
        response = self.client.post(
            "/api/v1/people/lists", {"name": "Family"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_list_lists_with_counts(self):
        family = create_list(owner=self.alice, name="Family")
        add_members(family, [create_person(owner=self.alice, display_name="Bob")])
        create_list(owner=self.bob, name="Hidden")
        response = self.client.get("/api/v1/people/lists")
        self.assertEqual([item["name"] for item in response.data], ["Family"])
        self.assertEqual(response.data[0]["member_count"], 1)

    def test_rename_and_delete(self):
        family = create_list(owner=self.alice, name="Family")
        response = self.client.patch(
            f"/api/v1/people/lists/{family.uuid}",
            {"name": "Close family"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.delete(f"/api/v1/people/lists/{family.uuid}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonList.objects.filter(pk=family.pk).exists())

    def test_patch_scope_is_refused(self):
        family = create_list(owner=self.alice, name="Family")
        response = self.client.patch(
            f"/api/v1/people/lists/{family.uuid}",
            {"scope": f"group:{self.team.id}"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("scope", response.data)
        family.refresh_from_db()
        self.assertEqual(family.owner, self.alice)

    def test_unreachable_list_is_404(self):
        hidden = create_list(owner=self.bob, name="Hidden")
        response = self.client.get(f"/api/v1/people/lists/{hidden.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_add_and_remove_members(self):
        family = create_list(owner=self.alice, name="Family")
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.post(
            f"/api/v1/people/lists/{family.uuid}/members",
            {"uuids": [str(person.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["member_count"], 1)
        response = self.client.delete(
            f"/api/v1/people/lists/{family.uuid}/members",
            {"uuids": [str(person.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["member_count"], 0)

    def test_add_member_from_other_scope_is_400(self):
        family = create_list(owner=self.alice, name="Family")
        teams = create_person(group=self.team, display_name="Teams")
        response = self.client.post(
            f"/api/v1/people/lists/{family.uuid}/members",
            {"uuids": [str(teams.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_add_unreachable_member_is_404(self):
        family = create_list(owner=self.alice, name="Family")
        bobs = create_person(owner=self.bob, display_name="Bobs")
        response = self.client.post(
            f"/api/v1/people/lists/{family.uuid}/members",
            {"uuids": [str(bobs.uuid)]},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
