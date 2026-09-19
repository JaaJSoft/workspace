from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.people.actions import PersonActionRegistry
from workspace.people.services.persons import create_person

User = get_user_model()


def ids(actions):
    return [a["id"] for a in actions]


class PersonActionRegistryTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.person = create_person(owner=self.alice, display_name="Bob")

    def test_without_groups_no_move(self):
        actions = PersonActionRegistry.get_available_actions(
            self.alice, self.person, has_groups=False
        )
        self.assertEqual(ids(actions), ["edit", "add_to_list", "delete"])

    def test_with_groups_move_offered(self):
        actions = PersonActionRegistry.get_available_actions(
            self.alice, self.person, has_groups=True
        )
        self.assertIn("move", ids(actions))

    def test_unlink_only_when_linked(self):
        self.person.linked_user = self.bob
        actions = PersonActionRegistry.get_available_actions(
            self.alice, self.person, has_groups=False
        )
        self.assertIn("unlink_user", ids(actions))

    def test_delete_is_bulk_and_danger(self):
        actions = PersonActionRegistry.get_available_actions(
            self.alice, self.person, has_groups=False
        )
        delete = next(a for a in actions if a["id"] == "delete")
        self.assertTrue(delete["bulk"])
        self.assertEqual(delete["category"], "danger")


class PersonActionsEndpointTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.client.force_authenticate(self.alice)
        self.mine = create_person(owner=self.alice, display_name="Mine")
        self.bobs = create_person(owner=self.bob, display_name="Bobs")

    def test_returns_map(self):
        response = self.client.post(
            "/api/v1/people/actions", {"uuids": [str(self.mine.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ids(response.data[str(self.mine.uuid)]), ["edit", "add_to_list", "delete"]
        )

    def test_group_member_gets_move(self):
        self.alice.groups.add(self.team)
        response = self.client.post(
            "/api/v1/people/actions", {"uuids": [str(self.mine.uuid)]}, format="json"
        )
        self.assertIn("move", ids(response.data[str(self.mine.uuid)]))

    def test_unreachable_is_404(self):
        response = self.client.post(
            "/api/v1/people/actions", {"uuids": [str(self.bobs.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_malformed_is_400(self):
        response = self.client.post(
            "/api/v1/people/actions", {"uuids": ["x"]}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/v1/people/actions", {"uuids": []}, format="json"
        )
        self.assertEqual(response.status_code, 400)
