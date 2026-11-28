import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile
from workspace.people.models import Person
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.persons import create_person, update_person

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
        self.assertIsNone(response.data["group"])
        # The answer of a create is the object a read answers.
        read = self.client.get(f"/api/v1/people/{response.data['uuid']}")
        self.assertEqual(read.data, response.data)
        self.assertEqual(response.data["addresses"][0]["type"], "home")
        self.assertEqual(response.data["addresses"][0]["postal_code"], "")
        person = Person.objects.get(uuid=response.data["uuid"])
        self.assertEqual(person.owner, self.alice)

    def test_create_group_person(self):
        response = self.client.post(
            "/api/v1/people",
            {"display_name": "Team contact", "group": self.team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["group"], {"id": self.team.id, "name": "team"})

    def test_a_group_one_is_not_in_is_refused_like_one_that_does_not_exist(self):
        def refusal(group_id):
            response = self.client.post(
                "/api/v1/people",
                {"display_name": "Nope", "group": group_id},
                format="json",
            )
            return response.status_code, response.content.replace(
                str(group_id).encode(), b"<id>"
            )

        absent = refusal(987654)
        self.assertEqual(absent[0], 400)
        self.assertEqual(refusal(self.other_team.id), absent)
        self.assertFalse(Person.objects.filter(display_name="Nope").exists())

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

    def test_patch_malformed_email_entry_is_400(self):
        person = create_person(
            owner=self.alice,
            display_name="Bob",
            emails=[{"value": "old@example.com", "type": "work"}],
        )
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}", {"emails": [{}]}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("emails", response.data)
        person.refresh_from_db()
        self.assertEqual(person.emails, [{"value": "old@example.com", "type": "work"}])

    def test_patch_malformed_phone_entry_is_400(self):
        person = create_person(
            owner=self.alice,
            display_name="Bob",
            phones=[{"value": "123", "type": "work"}],
        )
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"phones": [{"type": "work"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("phones", response.data)
        person.refresh_from_db()
        self.assertEqual(person.phones, [{"value": "123", "type": "work"}])

    def test_patch_partial_address_fills_defaults(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"addresses": [{"street": "x"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        person.refresh_from_db()
        self.assertEqual(
            person.addresses,
            [
                {
                    "street": "x",
                    "city": "",
                    "region": "",
                    "postal_code": "",
                    "country": "",
                    "type": "home",
                }
            ],
        )

    def test_patch_non_dict_extra_properties_is_400(self):
        person = create_person(owner=self.alice, display_name="Bob")
        for value in ("x", [1]):
            with self.subTest(value=value):
                response = self.client.patch(
                    f"/api/v1/people/{person.uuid}",
                    {"extra_properties": value},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("extra_properties", response.data)
        person.refresh_from_db()
        self.assertEqual(person.extra_properties, {})

    def test_patch_extra_properties_stores_the_mapping(self):
        person = create_person(owner=self.alice, display_name="Bob")
        properties = {"X-FOO": [{"value": "bar", "params": {}}]}
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"extra_properties": properties},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        person.refresh_from_db()
        self.assertEqual(person.extra_properties, properties)

    def test_list_only_reachable(self):
        create_person(owner=self.alice, display_name="Mine")
        create_person(group=self.team, display_name="Teams")
        create_person(owner=self.bob, display_name="Bobs")
        create_person(group=self.other_team, display_name="Others")
        response = self.client.get("/api/v1/people")
        self.assertEqual(response.status_code, 200)
        names = [p["display_name"] for p in response.data]
        self.assertEqual(names, ["Mine", "Teams"])

    def test_search_matches_email(self):
        create_person(
            owner=self.alice,
            display_name="Bob",
            emails=[{"value": "bob@acme.com", "type": "work"}],
        )
        create_person(owner=self.alice, display_name="Carol")
        response = self.client.get("/api/v1/people", {"q": "ACME"})
        names = [p["display_name"] for p in response.data]
        self.assertEqual(names, ["Bob"])

    def test_search_ignores_accents(self):
        create_person(owner=self.alice, display_name="Éloïse")
        create_person(owner=self.alice, display_name="Carol")
        response = self.client.get("/api/v1/people", {"q": "eloise"})
        names = [p["display_name"] for p in response.data]
        self.assertEqual(names, ["Éloïse"])

    def _names(self, **params):
        response = self.client.get("/api/v1/people", params)
        self.assertEqual(response.status_code, 200, response.data)
        return [p["display_name"] for p in response.data]

    def test_group_filter(self):
        create_person(owner=self.alice, display_name="Mine")
        create_person(group=self.team, display_name="Teams")
        self.assertEqual(self._names(group=self.team.id), ["Teams"])
        self.assertEqual(self._names(group_name="TEAM"), ["Teams"])
        # `none` is the caller's own address book.
        self.assertEqual(self._names(group="none"), ["Mine"])
        self.assertEqual(self._names(group=f"none,{self.team.id}"), ["Mine", "Teams"])

    def test_a_group_one_is_not_in_holds_nothing(self):
        create_person(group=self.other_team, display_name="Others")
        self.assertEqual(self._names(group=self.other_team.id), [])
        self.assertEqual(self._names(group=987654), [])
        self.assertEqual(self._names(group_name="other"), [])

    def test_group_filter_malformed_is_400(self):
        response = self.client.get("/api/v1/people", {"group": "team"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["group"])

    def test_the_list_is_paged(self):
        for name in ("Ada", "Bob", "Carol"):
            create_person(owner=self.alice, display_name=name)
        first = self.client.get("/api/v1/people", {"limit": 2, "count": "true"})
        self.assertEqual([p["display_name"] for p in first.data], ["Ada", "Bob"])
        self.assertEqual(first["X-Has-More"], "true")
        self.assertEqual(first["X-Total-Count"], "3")
        rest = self.client.get("/api/v1/people", {"limit": 2, "offset": 2})
        self.assertEqual([p["display_name"] for p in rest.data], ["Carol"])
        self.assertEqual(rest["X-Has-More"], "false")

    def test_ordering(self):
        create_person(owner=self.alice, display_name="Ada")
        create_person(owner=self.alice, display_name="Bob")
        self.assertEqual(self._names(ordering="-display_name"), ["Bob", "Ada"])
        self.assertEqual(self._names(ordering="-created_at"), ["Bob", "Ada"])

    def test_list_filter(self):
        bob = create_person(owner=self.alice, display_name="Bob")
        create_person(owner=self.alice, display_name="Carol")
        family = create_list(owner=self.alice, name="Family")
        add_members(family, [bob])
        self.assertEqual(self._names(list=str(family.uuid)), ["Bob"])
        self.assertEqual(self._names(list_name="family"), ["Bob"])

    def test_list_filter_malformed_is_400(self):
        response = self.client.get("/api/v1/people", {"list": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_group_and_list_filters_combine(self):
        bob = create_person(owner=self.alice, display_name="Bob")
        create_person(owner=self.alice, display_name="Carol")
        family = create_list(owner=self.alice, name="Family")
        add_members(family, [bob])
        self.assertEqual(self._names(group="none", list=str(family.uuid)), ["Bob"])
        self.assertEqual(self._names(group=self.team.id, list=str(family.uuid)), [])

    def test_retrieve_unreachable_is_404(self):
        bobs = create_person(owner=self.bob, display_name="Bobs")
        hidden = self.client.get(f"/api/v1/people/{bobs.uuid}")
        absent = self.client.get(f"/api/v1/people/{uuid.uuid4()}")
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_patch_moves_to_group(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"group": self.team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["group"], {"id": self.team.id, "name": "team"})
        person.refresh_from_db()
        self.assertEqual(person.group, self.team)
        self.assertIsNone(person.owner)

    def test_patch_moves_back_to_ones_own_book(self):
        person = create_person(group=self.team, display_name="Bob")
        family = create_list(group=self.team, name="Family")
        add_members(family, [person])
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}", {"group": None}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["group"])
        person.refresh_from_db()
        self.assertEqual(person.owner, self.alice)
        self.assertIsNone(person.group)
        # A list of the book it left no longer holds it.
        self.assertFalse(family.members.exists())

    def test_patch_that_names_no_group_leaves_the_contact_where_it_is(self):
        person = create_person(group=self.team, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}", {"title": "CTO"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        person.refresh_from_db()
        self.assertEqual(person.group, self.team)
        self.assertEqual(person.title, "CTO")

    def test_patch_to_foreign_group_is_400(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"group": self.other_team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["group"])
        person.refresh_from_db()
        self.assertEqual(person.owner, self.alice)

    def test_patch_move_into_linked_clash_is_400(self):
        person = create_person(
            owner=self.alice, display_name="Bob", linked_user=self.bob
        )
        create_person(group=self.team, display_name="Bob team", linked_user=self.bob)
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"group": self.team.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("linked_user", response.data)

    def test_a_move_and_a_link_in_one_request_are_judged_in_the_book_it_ends_in(self):
        """The account already has a contact in the book the contact leaves,
        and none in the one it goes to: nothing clashes where it lands."""
        create_person(owner=self.alice, display_name="Bob", linked_user=self.bob)
        person = create_person(owner=self.alice, display_name="Bob at work")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"group": self.team.id, "linked_user": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        person.refresh_from_db()
        self.assertEqual(person.group, self.team)
        self.assertEqual(person.linked_user, self.bob)

    def test_a_move_that_swaps_the_link_does_not_trip_on_the_one_it_drops(self):
        """The contact leaves its account behind as it moves: the contact the
        destination already holds for that account is no clash."""
        carol = User.objects.create_user(username="carol", password="x")
        create_person(group=self.team, display_name="Bob team", linked_user=self.bob)
        person = create_person(
            owner=self.alice, display_name="Bob", linked_user=self.bob
        )
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"group": self.team.id, "linked_user": carol.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        person.refresh_from_db()
        self.assertEqual(person.group, self.team)
        self.assertEqual(person.linked_user, carol)

    def test_patch_linked_user(self):
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"linked_user": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["linked_user"]["username"], "bob")
        self.assertEqual(
            response.data["linked_user"]["avatar_url"],
            f"/api/v1/users/{self.bob.id}/avatar",
        )

    def test_patch_linked_user_to_a_bot_is_400(self):
        BotProfile.objects.create(user=self.bob)
        person = create_person(owner=self.alice, display_name="Bob")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"linked_user": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("linked_user", response.data)
        person.refresh_from_db()
        self.assertIsNone(person.linked_user)

    def test_create_linked_to_a_bot_is_400(self):
        BotProfile.objects.create(user=self.bob)
        response = self.client.post(
            "/api/v1/people",
            {"display_name": "Bob", "linked_user": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("linked_user", response.data)
        self.assertFalse(Person.objects.filter(linked_user=self.bob).exists())

    def test_patch_duplicate_linked_user_is_400(self):
        create_person(owner=self.alice, display_name="Bob", linked_user=self.bob)
        person = create_person(owner=self.alice, display_name="Bob again")
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"linked_user": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("linked_user", response.data)

    def test_patch_linked_user_racing_a_clash_is_400(self):
        # `validate` saw no clash, then another request created one before the
        # write landed: the partial unique constraint is what refuses it, and
        # that refusal must read as a 400 on the field rather than a 500.
        person = create_person(owner=self.alice, display_name="Bob again")

        def racing_update(instance, **fields):
            create_person(owner=self.alice, display_name="Bob", linked_user=self.bob)
            return update_person(instance, **fields)

        with patch("workspace.people.views.persons.update_person", racing_update):
            response = self.client.patch(
                f"/api/v1/people/{person.uuid}",
                {"linked_user": self.bob.id},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("linked_user", response.data)

    def test_post_linked_user_race_is_400(self):
        def racing_create(**fields):
            Person.objects.create(
                owner=self.alice, display_name="Winner", linked_user=self.bob
            )
            return create_person(**fields)

        with patch("workspace.people.views.persons.create_person", racing_create):
            response = self.client.post(
                "/api/v1/people",
                {"display_name": "Loser", "linked_user": self.bob.id},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("linked_user", response.data)
        self.assertEqual(Person.objects.filter(display_name="Loser").count(), 0)

    def test_patch_clear_linked_user(self):
        person = create_person(
            owner=self.alice, display_name="Bob", linked_user=self.bob
        )
        response = self.client.patch(
            f"/api/v1/people/{person.uuid}",
            {"linked_user": None},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["linked_user"])
        person.refresh_from_db()
        self.assertIsNone(person.linked_user)

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
