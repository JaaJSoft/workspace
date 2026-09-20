from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.people.models import Person, PersonList
from workspace.people.services.persons import (
    create_person,
    delete_person,
    move_to_scope,
    update_person,
)

User = get_user_model()


class PersonServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)

    def test_create_person_owned(self):
        person = create_person(owner=self.alice, display_name="Bob")
        self.assertEqual(person.owner, self.alice)
        self.assertIsNone(person.group)
        self.assertEqual(person.search_text, "bob")

    def test_update_person_writes_only_named_fields(self):
        person = create_person(owner=self.alice, display_name="Bob")
        Person.objects.filter(pk=person.pk).update(notes="written elsewhere")
        update_person(person, display_name="Robert")
        person.refresh_from_db()
        self.assertEqual(person.display_name, "Robert")
        self.assertEqual(person.notes, "written elsewhere")
        self.assertEqual(person.search_text, "robert")

    def test_move_to_group_drops_owner_and_foreign_lists(self):
        person = create_person(owner=self.alice, display_name="Bob")
        mine = PersonList.objects.create(owner=self.alice, name="Family")
        mine.members.add(person)
        move_to_scope(person, group=self.team)
        person.refresh_from_db()
        self.assertIsNone(person.owner)
        self.assertEqual(person.group, self.team)
        self.assertEqual(mine.members.count(), 0)

    def test_move_back_to_owner(self):
        person = create_person(group=self.team, display_name="Bob")
        move_to_scope(person, owner=self.alice)
        person.refresh_from_db()
        self.assertEqual(person.owner, self.alice)
        self.assertIsNone(person.group)

    def test_move_requires_exactly_one_scope(self):
        person = create_person(owner=self.alice, display_name="Bob")
        with self.assertRaises(ValueError):
            move_to_scope(person)
        with self.assertRaises(ValueError):
            move_to_scope(person, owner=self.alice, group=self.team)

    def test_delete_person(self):
        person = create_person(owner=self.alice, display_name="Bob")
        delete_person(person)
        self.assertFalse(Person.objects.filter(pk=person.pk).exists())
