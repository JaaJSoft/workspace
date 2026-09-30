from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.services.address_book import resolve_invitees
from workspace.people.models import Person, PersonList

User = get_user_model()


class AddressBookFixture:
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")

        self.linked = Person.objects.create(
            owner=self.user,
            display_name="Bob Builder",
            emails=[{"value": "bob@home.example", "type": "home"}],
            linked_user=self.bob,
        )
        self.external = Person.objects.create(
            owner=self.user,
            display_name="Ada Lovelace",
            emails=[
                {"value": "Ada@Example.com", "type": "work"},
                {"value": "ada@home.example", "type": "home"},
            ],
        )
        self.phone_only = Person.objects.create(
            owner=self.user,
            display_name="Carol",
            phones=[{"value": "+33 1 23 45 67 89", "type": "cell"}],
        )
        self.family = PersonList.objects.create(owner=self.user, name="Family")
        self.family.members.add(self.linked, self.external, self.phone_only)


class ResolveInviteesTests(AddressBookFixture, TestCase):
    def test_linked_person_resolves_to_the_account_not_a_guest(self):
        resolved = resolve_invitees(self.user, person_ids=[self.linked.uuid])
        self.assertEqual(resolved.users, [self.bob])
        self.assertEqual(resolved.guests, [])

    def test_unlinked_person_resolves_to_a_guest_on_the_first_email(self):
        resolved = resolve_invitees(self.user, person_ids=[self.external.uuid])
        self.assertEqual(resolved.users, [])
        self.assertEqual(
            resolved.guests, [{"email": "ada@example.com", "name": "Ada Lovelace"}]
        )

    def test_person_without_account_or_email_is_skipped(self):
        resolved = resolve_invitees(self.user, person_ids=[self.phone_only.uuid])
        self.assertEqual(resolved.skipped, [self.phone_only])

    def test_list_resolves_every_member(self):
        resolved = resolve_invitees(self.user, list_ids=[self.family.uuid])
        self.assertEqual(resolved.users, [self.bob])
        self.assertEqual([g["email"] for g in resolved.guests], ["ada@example.com"])
        self.assertEqual(resolved.skipped, [self.phone_only])

    def test_person_picked_twice_resolves_once(self):
        resolved = resolve_invitees(
            self.user,
            person_ids=[self.linked.uuid, self.external.uuid],
            list_ids=[self.family.uuid],
        )
        self.assertEqual(resolved.users, [self.bob])
        self.assertEqual(len(resolved.guests), 1)

    def test_two_persons_linked_to_one_account_resolve_once(self):
        group = Group.objects.create(name="Team")
        self.user.groups.add(group)
        shared = Person.objects.create(
            group=group, display_name="Bob (team)", linked_user=self.bob
        )
        resolved = resolve_invitees(
            self.user, person_ids=[self.linked.uuid, shared.uuid]
        )
        self.assertEqual(resolved.users, [self.bob])

    def test_self_is_never_an_invitee(self):
        me = Person.objects.create(
            owner=self.user, display_name="Me", linked_user=self.user
        )
        resolved = resolve_invitees(self.user, person_ids=[me.uuid])
        self.assertEqual(resolved.users, [])
        self.assertEqual(resolved.guests, [])

    def test_deactivated_account_falls_back_to_the_email(self):
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        resolved = resolve_invitees(self.user, person_ids=[self.linked.uuid])
        self.assertEqual(resolved.users, [])
        self.assertEqual([g["email"] for g in resolved.guests], ["bob@home.example"])

    def test_someone_elses_persons_and_lists_are_ignored(self):
        resolved = resolve_invitees(
            self.stranger,
            person_ids=[self.external.uuid],
            list_ids=[self.family.uuid],
        )
        self.assertEqual(
            (resolved.users, resolved.guests, resolved.skipped), ([], [], [])
        )


class InviteeResolveApiTests(AddressBookFixture, APITestCase):
    url = "/api/v1/events/invitees"

    def test_requires_authentication(self):
        resp = self.client.post(self.url, {"list_ids": []}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_something_to_resolve(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_malformed_uuid(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {"person_ids": ["nope"]}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resolves_a_list(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url, {"list_ids": [str(self.family.uuid)]}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([u["username"] for u in resp.data["users"]], ["bob"])
        self.assertEqual(
            resp.data["guests"], [{"email": "ada@example.com", "name": "Ada Lovelace"}]
        )
        self.assertEqual(
            resp.data["skipped"],
            [{"uuid": str(self.phone_only.uuid), "display_name": "Carol"}],
        )
