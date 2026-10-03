from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.people.models import Person
from workspace.people.queries import person_with_email
from workspace.people.services.persons import create_person, promote_to_person

User = get_user_model()

URL = "/api/v1/people/promote"


class PersonWithEmailTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")

    def test_matches_case_insensitively(self):
        bob = create_person(
            owner=self.alice,
            display_name="Bob",
            emails=[{"value": "Bob@Example.com", "type": "work"}],
        )
        found = person_with_email(Person.objects.all(), "  bob@example.COM ")
        self.assertEqual(found, bob)

    def test_substring_of_another_address_is_not_a_match(self):
        create_person(
            owner=self.alice,
            display_name="Bob",
            emails=[{"value": "jimbob@example.com", "type": "work"}],
        )
        self.assertIsNone(person_with_email(Person.objects.all(), "bob@example.com"))

    def test_blank_email_matches_nothing(self):
        create_person(owner=self.alice, display_name="Bob")
        self.assertIsNone(person_with_email(Person.objects.all(), " "))


class PromoteServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.carol = User.objects.create_user(
            username="carol",
            password="x",
            email="carol@corp.com",
            first_name="Carol",
            last_name="Danvers",
        )
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)

    def test_creates_person_in_own_address_book(self):
        person, created = promote_to_person(
            self.alice, email="bob@example.com", name="Bob Martin"
        )
        self.assertTrue(created)
        self.assertEqual(person.owner, self.alice)
        self.assertEqual(person.display_name, "Bob Martin")
        self.assertEqual(person.emails, [{"value": "bob@example.com", "type": "other"}])
        self.assertIsNone(person.linked_user)

    def test_name_falls_back_to_email(self):
        person, _ = promote_to_person(self.alice, email="bob@example.com")
        self.assertEqual(person.display_name, "bob@example.com")

    def test_returns_existing_person_carrying_email(self):
        bob = create_person(
            owner=self.alice,
            display_name="Robert",
            emails=[
                {"value": "robert@home.org", "type": "home"},
                {"value": "Bob@Example.com", "type": "work"},
            ],
        )
        person, created = promote_to_person(
            self.alice, email="bob@example.com", name="Bob"
        )
        self.assertFalse(created)
        self.assertEqual(person, bob)
        self.assertEqual(Person.objects.count(), 1)

    def test_group_person_counts_as_existing(self):
        bob = create_person(
            group=self.team,
            display_name="Bob",
            emails=[{"value": "bob@example.com", "type": "work"}],
        )
        person, created = promote_to_person(self.alice, email="bob@example.com")
        self.assertFalse(created)
        self.assertEqual(person, bob)

    def test_someone_elses_person_does_not_count(self):
        dave = User.objects.create_user(username="dave", password="x")
        create_person(
            owner=dave,
            display_name="Bob",
            emails=[{"value": "bob@example.com", "type": "work"}],
        )
        person, created = promote_to_person(self.alice, email="bob@example.com")
        self.assertTrue(created)
        self.assertEqual(person.owner, self.alice)

    def test_account_creates_linked_person(self):
        person, created = promote_to_person(self.alice, account=self.carol)
        self.assertTrue(created)
        self.assertEqual(person.linked_user, self.carol)
        self.assertEqual(person.display_name, "Carol Danvers")
        self.assertEqual(person.given_name, "Carol")
        self.assertEqual(person.family_name, "Danvers")
        self.assertEqual(person.primary_email, "carol@corp.com")

    def test_account_without_name_uses_username(self):
        dave = User.objects.create_user(username="dave", password="x")
        person, _ = promote_to_person(self.alice, account=dave)
        self.assertEqual(person.display_name, "dave")
        self.assertEqual(person.emails, [])

    def test_account_returns_person_already_linked(self):
        linked = create_person(
            owner=self.alice, display_name="Cap", linked_user=self.carol
        )
        person, created = promote_to_person(self.alice, account=self.carol)
        self.assertFalse(created)
        self.assertEqual(person, linked)

    def test_account_returns_person_carrying_its_email(self):
        existing = create_person(
            owner=self.alice,
            display_name="Carol",
            emails=[{"value": "carol@corp.com", "type": "work"}],
        )
        person, created = promote_to_person(self.alice, account=self.carol)
        self.assertFalse(created)
        self.assertEqual(person, existing)


class PromoteApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.carol = User.objects.create_user(
            username="carol", password="x", email="carol@corp.com"
        )
        self.client.force_authenticate(self.alice)

    def test_creates_with_201(self):
        response = self.client.post(
            URL, {"email": "bob@example.com", "name": "Bob"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["display_name"], "Bob")
        self.assertEqual(response.data["scope"], "mine")
        self.assertEqual(response.data["emails"][0]["value"], "bob@example.com")

    def test_existing_person_answers_200(self):
        bob = create_person(
            owner=self.alice,
            display_name="Bob",
            emails=[{"value": "bob@example.com", "type": "work"}],
        )
        response = self.client.post(URL, {"email": "BOB@example.com"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["uuid"], str(bob.uuid))
        self.assertEqual(Person.objects.count(), 1)

    def test_account(self):
        response = self.client.post(URL, {"user_id": self.carol.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["linked_user"]["id"], self.carol.pk)

    def test_needs_email_or_user_id(self):
        response = self.client.post(URL, {"name": "Bob"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_refuses_both(self):
        response = self.client.post(
            URL, {"email": "bob@example.com", "user_id": self.carol.pk}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_refuses_malformed_email(self):
        response = self.client.post(URL, {"email": "not-an-email"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_refuses_inactive_account(self):
        self.carol.is_active = False
        self.carol.save()
        response = self.client.post(URL, {"user_id": self.carol.pk}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_anonymous_refused(self):
        self.client.force_authenticate(None)
        response = self.client.post(URL, {"email": "bob@example.com"}, format="json")
        self.assertIn(response.status_code, (401, 403))
