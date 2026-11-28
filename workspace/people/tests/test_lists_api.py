import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.people.models import PersonList
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person

User = get_user_model()


class PeopleApiTestCase(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.other_team = Group.objects.create(name="other")
        self.alice.groups.add(self.team)
        self.client.force_authenticate(self.alice)

    def same_answer(self, hidden, absent):
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )


class PersonListApiTests(PeopleApiTestCase):
    def test_create_list_defaults_to_ones_own_book(self):
        response = self.client.post(
            "/api/v1/people/lists", {"name": "Family"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["group"])
        self.assertEqual(response.data["member_count"], 0)
        read = self.client.get(f"/api/v1/people/lists/{response.data['uuid']}")
        self.assertEqual(read.data, response.data)

    def test_create_group_list(self):
        response = self.client.post(
            "/api/v1/people/lists",
            {"name": "Clients", "group": self.team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["group"], {"id": self.team.id, "name": "team"})
        self.assertEqual(PersonList.objects.get(name="Clients").group, self.team)

    def test_a_group_one_is_not_in_is_refused_like_one_that_does_not_exist(self):
        def refusal(group_id):
            response = self.client.post(
                "/api/v1/people/lists",
                {"name": "Nope", "group": group_id},
                format="json",
            )
            return response.status_code, response.content.replace(
                str(group_id).encode(), b"<id>"
            )

        absent = refusal(987654)
        self.assertEqual(absent[0], 400)
        self.assertEqual(refusal(self.other_team.id), absent)
        self.assertFalse(PersonList.objects.exists())

    def test_duplicate_name_is_400_on_the_name(self):
        create_list(owner=self.alice, name="Family")
        response = self.client.post(
            "/api/v1/people/lists", {"name": "Family"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["name"])
        # The same name in another address book is another list.
        response = self.client.post(
            "/api/v1/people/lists",
            {"name": "Family", "group": self.team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_rename_onto_a_taken_name_is_400_on_the_name(self):
        create_list(owner=self.alice, name="Family")
        friends = create_list(owner=self.alice, name="Friends")
        response = self.client.patch(
            f"/api/v1/people/lists/{friends.uuid}", {"name": "Family"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["name"])
        friends.refresh_from_db()
        self.assertEqual(friends.name, "Friends")

    def test_list_lists_with_counts(self):
        family = create_list(owner=self.alice, name="Family")
        add_members(family, [create_person(owner=self.alice, display_name="Bob")])
        create_list(owner=self.bob, name="Hidden")
        response = self.client.get("/api/v1/people/lists")
        self.assertEqual([item["name"] for item in response.data], ["Family"])
        self.assertEqual(response.data[0]["member_count"], 1)

    def test_filters(self):
        create_list(owner=self.alice, name="Family")
        create_list(group=self.team, name="Clients")

        def names(**params):
            response = self.client.get("/api/v1/people/lists", params)
            self.assertEqual(response.status_code, 200, response.data)
            return [item["name"] for item in response.data]

        self.assertEqual(names(group="none"), ["Family"])
        self.assertEqual(names(group=self.team.id), ["Clients"])
        self.assertEqual(names(group_name="Team"), ["Clients"])
        self.assertEqual(names(q="fam"), ["Family"])
        self.assertEqual(names(group=self.other_team.id), [])

    def test_rename_and_delete(self):
        family = create_list(owner=self.alice, name="Family")
        response = self.client.patch(
            f"/api/v1/people/lists/{family.uuid}",
            {"name": "Close family"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Close family")
        response = self.client.delete(f"/api/v1/people/lists/{family.uuid}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonList.objects.filter(pk=family.pk).exists())

    def test_a_list_cannot_change_address_book(self):
        family = create_list(owner=self.alice, name="Family")
        response = self.client.patch(
            f"/api/v1/people/lists/{family.uuid}",
            {"group": self.team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["group"])
        family.refresh_from_db()
        self.assertEqual(family.owner, self.alice)
        # Naming the book it is in changes nothing and is no error.
        response = self.client.patch(
            f"/api/v1/people/lists/{family.uuid}", {"group": None}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_a_list_out_of_reach_answers_like_one_that_does_not_exist(self):
        hidden = create_list(owner=self.bob, name="Hidden")
        for method, body in (("get", None), ("patch", {"name": "X"}), ("delete", None)):
            with self.subTest(method=method):
                call = getattr(self.client, method)
                answer = call(
                    f"/api/v1/people/lists/{hidden.uuid}", body, format="json"
                )
                absent = call(
                    f"/api/v1/people/lists/{uuid.uuid4()}", body, format="json"
                )
                self.assertEqual(answer.status_code, 404)
                self.same_answer(answer, absent)
        self.assertTrue(PersonList.objects.filter(pk=hidden.pk).exists())


class ListMembershipTests(PeopleApiTestCase):
    """Whether a contact is on a list: a link set and removed one at a time,
    or for several contacts through the batch."""

    def setUp(self):
        super().setUp()
        self.family = create_list(owner=self.alice, name="Family")
        self.person = create_person(owner=self.alice, display_name="Bob")

    def link(self, person_list, person):
        return f"/api/v1/people/lists/{person_list.uuid}/members/{person.uuid}"

    def test_put_and_delete_set_and_remove_the_link(self):
        for _ in range(2):
            response = self.client.put(self.link(self.family, self.person))
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b"")
        self.assertEqual(list(self.family.members.all()), [self.person])
        for _ in range(2):
            response = self.client.delete(self.link(self.family, self.person))
            self.assertEqual(response.status_code, 204)
        self.assertFalse(self.family.members.exists())

    def test_a_contact_of_another_address_book_is_refused(self):
        teams = create_person(group=self.team, display_name="Teams")
        response = self.client.put(self.link(self.family, teams))
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("Teams", response.data["detail"])
        self.assertFalse(self.family.members.exists())

    def test_either_end_out_of_reach_answers_like_one_that_does_not_exist(self):
        bobs = create_person(owner=self.bob, display_name="Bobs")
        hidden = create_list(owner=self.bob, name="Hidden")
        absent = self.client.put(
            f"/api/v1/people/lists/{uuid.uuid4()}/members/{uuid.uuid4()}"
        )
        self.assertEqual(absent.status_code, 404)
        for method in ("put", "delete"):
            call = getattr(self.client, method)
            for path in (self.link(self.family, bobs), self.link(hidden, self.person)):
                with self.subTest(method=method, path=path):
                    self.same_answer(call(path), absent)
        self.assertFalse(self.family.members.exists())
        self.assertFalse(hidden.members.exists())

    def batch(self, action, persons, person_list=None):
        return self.client.post(
            "/api/v1/people/batch",
            {
                "action": action,
                "uuids": [str(person.uuid) for person in persons],
                "data": {"list": str((person_list or self.family).uuid)},
            },
            format="json",
        )

    def test_batch_adds_and_removes_several_contacts(self):
        carol = create_person(owner=self.alice, display_name="Carol")
        response = self.batch("add_to_list", [self.person, carol])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [person["display_name"] for person in response.data], ["Bob", "Carol"]
        )
        self.assertEqual(self.family.members.count(), 2)
        response = self.batch("remove_from_list", [carol])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(self.family.members.all()), [self.person])

    def test_batch_is_all_or_nothing(self):
        teams = create_person(group=self.team, display_name="Teams")
        response = self.batch("add_to_list", [self.person, teams])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["uuids"])
        self.assertFalse(self.family.members.exists())

    def test_batch_refuses_a_contact_out_of_reach_like_one_that_does_not_exist(self):
        bobs = create_person(owner=self.bob, display_name="Bobs")

        def refusal(person_uuid):
            response = self.client.post(
                "/api/v1/people/batch",
                {
                    "action": "add_to_list",
                    "uuids": [str(self.person.uuid), str(person_uuid)],
                    "data": {"list": str(self.family.uuid)},
                },
                format="json",
            )
            return response.status_code, response.content.replace(
                str(person_uuid).encode(), b"<uuid>"
            )

        absent = refusal(uuid.uuid4())
        self.assertEqual(absent[0], 400)
        self.assertEqual(refusal(bobs.uuid), absent)
        self.assertFalse(self.family.members.exists())

    def test_batch_refuses_a_list_out_of_reach_like_one_that_does_not_exist(self):
        hidden = create_list(owner=self.bob, name="Hidden")

        def refusal(list_uuid):
            response = self.client.post(
                "/api/v1/people/batch",
                {
                    "action": "add_to_list",
                    "uuids": [str(self.person.uuid)],
                    "data": {"list": str(list_uuid)},
                },
                format="json",
            )
            return response.status_code, response.content.replace(
                str(list_uuid).encode(), b"<uuid>"
            )

        absent = refusal(uuid.uuid4())
        self.assertEqual(absent[0], 400)
        self.assertEqual(refusal(hidden.uuid), absent)
        self.assertFalse(hidden.members.exists())
