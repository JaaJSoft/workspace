from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.people.services.lists import (
    ScopeMismatch,
    add_members,
    create_list,
    remove_members,
    rename_list,
)
from workspace.people.services.persons import create_person

User = get_user_model()


class ListServiceTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.team = Group.objects.create(name="team")
        self.alice.groups.add(self.team)
        self.mine = create_person(owner=self.alice, display_name="Mine")
        self.teams = create_person(group=self.team, display_name="Teams")

    def test_create_and_rename(self):
        person_list = create_list(owner=self.alice, name="Family")
        rename_list(person_list, "Close family")
        person_list.refresh_from_db()
        self.assertEqual(person_list.name, "Close family")

    def test_add_members_same_scope(self):
        person_list = create_list(owner=self.alice, name="Family")
        add_members(person_list, [self.mine])
        self.assertEqual(list(person_list.members.all()), [self.mine])

    def test_add_member_from_other_scope_refused(self):
        person_list = create_list(owner=self.alice, name="Family")
        with self.assertRaises(ScopeMismatch):
            add_members(person_list, [self.teams])
        self.assertEqual(person_list.members.count(), 0)

    def test_remove_members(self):
        person_list = create_list(group=self.team, name="Clients")
        add_members(person_list, [self.teams])
        remove_members(person_list, [self.teams])
        self.assertEqual(person_list.members.count(), 0)
