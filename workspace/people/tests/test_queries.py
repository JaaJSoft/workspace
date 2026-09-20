from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.people.models import Person, PersonList
from workspace.people.queries import (
    can_edit,
    reachable_list,
    reachable_person,
    user_person_lists,
    user_persons,
)

User = get_user_model()


class UserPersonsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.other_team = Group.objects.create(name="other")
        self.alice.groups.add(self.team)
        self.mine = Person.objects.create(owner=self.alice, display_name="Mine")
        self.bobs = Person.objects.create(owner=self.bob, display_name="Bobs")
        self.teams = Person.objects.create(group=self.team, display_name="Teams")
        self.others = Person.objects.create(
            group=self.other_team, display_name="Others"
        )

    def test_sees_own_and_group_persons_only(self):
        self.assertEqual(
            set(user_persons(self.alice).values_list("uuid", flat=True)),
            {self.mine.uuid, self.teams.uuid},
        )

    def test_other_user_sees_nothing_of_alice(self):
        self.assertEqual(
            set(user_persons(self.bob).values_list("uuid", flat=True)),
            {self.bobs.uuid},
        )

    def test_reachable_person(self):
        self.assertEqual(reachable_person(self.alice, self.teams.uuid), self.teams)
        self.assertIsNone(reachable_person(self.alice, self.bobs.uuid))
        self.assertIsNone(reachable_person(self.alice, self.others.uuid))

    def test_can_edit(self):
        self.assertTrue(can_edit(self.alice, self.mine))
        self.assertTrue(can_edit(self.alice, self.teams))
        self.assertFalse(can_edit(self.alice, self.bobs))
        self.assertFalse(can_edit(self.alice, self.others))


class UserPersonListsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)
        self.mine = PersonList.objects.create(owner=self.alice, name="Family")
        self.teams = PersonList.objects.create(group=self.team, name="Clients")
        self.bobs = PersonList.objects.create(owner=self.bob, name="Family")

    def test_sees_own_and_group_lists(self):
        self.assertEqual(
            set(user_person_lists(self.alice).values_list("uuid", flat=True)),
            {self.mine.uuid, self.teams.uuid},
        )

    def test_reachable_list(self):
        self.assertEqual(reachable_list(self.alice, self.teams.uuid), self.teams)
        self.assertIsNone(reachable_list(self.alice, self.bobs.uuid))
