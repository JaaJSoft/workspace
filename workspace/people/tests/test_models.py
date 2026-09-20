from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.people.models import Person, PersonList, search_text_for

User = get_user_model()


class PersonScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.group = Group.objects.create(name="team")

    def test_owner_only_is_valid(self):
        person = Person.objects.create(owner=self.user, display_name="Bob")
        self.assertIsNotNone(person.uuid)

    def test_group_only_is_valid(self):
        person = Person.objects.create(group=self.group, display_name="Bob")
        self.assertIsNotNone(person.uuid)

    def test_both_scopes_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Person.objects.create(owner=self.user, group=self.group, display_name="Bob")

    def test_no_scope_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Person.objects.create(display_name="Bob")

    def test_linked_user_unique_per_owner(self):
        linked = User.objects.create_user(username="bob", password="x")
        Person.objects.create(owner=self.user, display_name="Bob", linked_user=linked)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Person.objects.create(
                owner=self.user, display_name="Bob 2", linked_user=linked
            )

    def test_linked_user_unique_per_group(self):
        linked = User.objects.create_user(username="bob", password="x")
        Person.objects.create(group=self.group, display_name="Bob", linked_user=linked)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Person.objects.create(
                group=self.group, display_name="Bob 2", linked_user=linked
            )

    def test_same_linked_user_allowed_in_different_scopes(self):
        linked = User.objects.create_user(username="bob", password="x")
        Person.objects.create(owner=self.user, display_name="Bob", linked_user=linked)
        Person.objects.create(group=self.group, display_name="Bob", linked_user=linked)
        self.assertEqual(Person.objects.filter(linked_user=linked).count(), 2)


class SearchTextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")

    def test_search_text_concatenates_lowercased_fields(self):
        person = Person.objects.create(
            owner=self.user,
            display_name="Bob Martin",
            given_name="Bob",
            family_name="Martin",
            organization="ACME",
            emails=[{"value": "Bob@Example.com", "type": "work"}],
            phones=[{"value": "+33 6 12 34 56 78", "type": "cell"}],
        )
        self.assertEqual(
            person.search_text,
            "bob martin bob martin acme bob@example.com +33 6 12 34 56 78",
        )

    def test_search_text_updates_with_update_fields(self):
        person = Person.objects.create(owner=self.user, display_name="Bob")
        person.display_name = "Robert"
        person.save(update_fields=["display_name", "updated_at"])
        person.refresh_from_db()
        self.assertEqual(person.search_text, "robert")

    def test_search_text_is_capped(self):
        person = Person(owner=self.user, display_name="a" * 2000)
        self.assertEqual(len(search_text_for(person)), 1024)


class PersonListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")

    def test_name_unique_per_owner(self):
        PersonList.objects.create(owner=self.user, name="Family")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonList.objects.create(owner=self.user, name="Family")

    def test_deleting_person_removes_membership(self):
        person = Person.objects.create(owner=self.user, display_name="Bob")
        person_list = PersonList.objects.create(owner=self.user, name="Family")
        person_list.members.add(person)
        person.delete()
        self.assertEqual(person_list.members.count(), 0)

    def test_deleting_list_keeps_persons(self):
        person = Person.objects.create(owner=self.user, display_name="Bob")
        person_list = PersonList.objects.create(owner=self.user, name="Family")
        person_list.members.add(person)
        person_list.delete()
        self.assertTrue(Person.objects.filter(pk=person.pk).exists())
