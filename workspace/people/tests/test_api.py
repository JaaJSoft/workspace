from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.people.models import Person
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()


class PersonApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.other_team = Group.objects.create(name="other")
        self.alice.groups.add(self.team)
        self.client.force_authenticate(self.alice)

    def test_create_owned_person(self):
        response = self.client.post(
            "/api/v1/people",
            {
                "display_name": "Bob Martin",
                "emails": [{"value": "bob@example.com", "type": "work"}],
                "phones": [{"value": "+33612345678", "type": "cell"}],
                "addresses": [{"street": "1 rue de la Paix", "city": "Paris"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["scope"], "mine")
        self.assertEqual(response.data["addresses"][0]["type"], "home")
        self.assertEqual(response.data["addresses"][0]["postal_code"], "")
        person = Person.objects.get(uuid=response.data["uuid"])
        self.assertEqual(person.owner, self.alice)

    def test_create_group_person(self):
        response = self.client.post(
            "/api/v1/people",
            {"display_name": "Team contact", "scope": f"group:{self.team.id}"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["scope"], f"group:{self.team.id}")

    def test_create_in_foreign_group_is_400(self):
        response = self.client.post(
            "/api/v1/people",
            {"display_name": "Nope", "scope": f"group:{self.other_team.id}"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("scope", response.data)

    def test_invalid_email_is_400(self):
        response = self.client.post(
            "/api/v1/people",
            {"display_name": "Bob", "emails": [{"value": "not-an-email"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("emails", response.data)

    def test_unknown_type_is_400(self):
        response = self.client.post(
            "/api/v1/people",
            {"display_name": "Bob", "phones": [{"value": "123", "type": "pager"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("phones", response.data)

    def test_list_only_reachable(self):
        create_person(owner=self.alice, display_name="Mine")
        create_person(group=self.team, display_name="Teams")
        create_person(owner=self.bob, display_name="Bobs")
        create_person(group=self.other_team, display_name="Others")
        response = self.client.get("/api/v1/people")
        self.assertEqual(response.status_code, 200)
        names = [p["display_name"] for p in response.data["results"]]
        self.assertEqual(names, ["Mine", "Teams"])

    def test_search_matches_email(self):
        create_person(
            owner=self.alice,
            display_name="Bob",
            emails=[{"value": "bob@acme.com", "type": "work"}],
        )
        create_person(owner=self.alice, display_name="Carol")
        response = self.client.get("/api/v1/people", {"q": "ACME"})
        names = [p["display_name"] for p in response.data["results"]]
        self.assertEqual(names, ["Bob"])

    def test_scope_filter(self):
        create_person(owner=self.alice, display_name="Mine")
        create_person(group=self.team, display_name="Teams")
        response = self.client.get("/api/v1/people", {"scope": f"group:{self.team.id}"})
        names = [p["display_name"] for p in response.data["results"]]
        self.assertEqual(names, ["Teams"])
        response = self.client.get("/api/v1/people", {"scope": "mine"})
        names = [p["display_name"] for p in response.data["results"]]
        self.assertEqual(names, ["Mine"])

    def test_list_filter(self):
        bob = create_person(owner=self.alice, display_name="Bob")
        create_person(owner=self.alice, display_name="Carol")
        family = create_list(owner=self.alice, name="Family")
        add_members(family, [bob])
        response = self.client.get("/api/v1/people", {"list": str(family.uuid)})
        names = [p["display_name"] for p in response.data["results"]]
        self.assertEqual(names, ["Bob"])

    def test_list_filter_malformed_is_400(self):
        response = self.client.get("/api/v1/people", {"list": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_retrieve_unreachable_is_404(self):
        bobs = create_person(owner=self.bob, display_name="Bobs")
        response = self.client.get(f"/api/v1/people/{bobs.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_patch_moves_to_group(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"scope": f"group:{self.team.id}"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        person.refresh_from_db()
        self.assertEqual(person.group, self.team)
        self.assertIsNone(person.owner)

    def test_patch_to_foreign_group_is_400(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"scope": f"group:{self.other_team.id}"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_patch_linked_user(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"linked_user_id": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["linked_user"]["username"], "bob")
        self.assertEqual(
            response.data["linked_user"]["avatar_url"],
            f"/api/v1/users/{self.bob.id}/avatar",
        )

    def test_patch_duplicate_linked_user_is_400(self):
        create_person(owner=self.alice, display_name="Bob", linked_user=self.bob)
        person = create_person(owner=self.alice, display_name="Bob again")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"linked_user_id": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("linked_user_id", response.data)

    def test_delete(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.delete(f"/api/v1/people/{person.uuid}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Person.objects.filter(pk=person.pk).exists())

    def test_put_not_allowed(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.put(
            f"/api/v1/people/{person.uuid}", {"display_name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, 405)
