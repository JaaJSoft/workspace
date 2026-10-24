"""Every write to a contact or a list reaches the CardDAV change log."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.people.models import AddressBook, AddressBookChange
from workspace.people.services.lists import add_members, create_list, remove_members
from workspace.people.services.persons import (
    create_person,
    delete_person,
    move_to_scope,
    update_person,
)
from workspace.people.services.sync_log import (
    book_revision,
    changes_since,
    object_revisions,
)
from workspace.people.services.vcard_import import import_vcards

User = get_user_model()


class SyncLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="alice", password="pw")
        cls.team = Group.objects.create(name="Team")
        cls.user.groups.add(cls.team)

    def revision(self):
        return book_revision(self.user.pk, None)

    def changed_since(self, revision, owner_id=None, group_id=None):
        if owner_id is None and group_id is None:
            owner_id = self.user.pk
        return {c.object_uuid for c in changes_since(owner_id, group_id, revision)}

    def test_a_book_starts_at_revision_zero(self):
        self.assertEqual(self.revision(), 0)
        self.assertFalse(AddressBook.objects.exists())

    def test_create_and_update_stamp_the_person(self):
        person = create_person(owner=self.user, display_name="Zoe")
        first = self.revision()
        self.assertEqual(object_revisions(self.user.pk, None), {person.pk: first})

        update_person(person, display_name="Zoe Q.")
        self.assertGreater(self.revision(), first)
        self.assertEqual(self.changed_since(first), {person.pk})

    def test_move_reports_the_card_gone_from_the_old_book(self):
        person = create_person(owner=self.user, display_name="Zoe")
        before = self.revision()
        move_to_scope(person, group=self.team)

        (gone,) = changes_since(self.user.pk, None, before)
        self.assertEqual((gone.object_uuid, gone.name), (person.pk, f"{person.pk}.vcf"))
        self.assertEqual(self.changed_since(0, group_id=self.team.pk), {person.pk})

    def test_deleting_a_person_stamps_it_and_its_lists(self):
        person = create_person(owner=self.user, display_name="Zoe")
        person_list = create_list(owner=self.user, name="Family")
        add_members(person_list, [person])
        before, person_id = self.revision(), person.pk

        delete_person(person)
        self.assertEqual(self.changed_since(before), {person_id, person_list.pk})

    def test_membership_changes_stamp_the_list_from_either_side(self):
        zoe = create_person(owner=self.user, display_name="Zoe")
        person_list = create_list(owner=self.user, name="Family")
        steps = [
            lambda: add_members(person_list, [zoe]),
            lambda: remove_members(person_list, [zoe]),
            lambda: zoe.lists.add(person_list),
            lambda: zoe.lists.clear(),
            lambda: person_list.members.add(zoe),
            lambda: person_list.members.clear(),
        ]
        for step in steps:
            before = self.revision()
            step()
            self.assertEqual(self.changed_since(before), {person_list.pk})

    def test_adding_a_member_already_there_changes_nothing(self):
        zoe = create_person(owner=self.user, display_name="Zoe")
        person_list = create_list(owner=self.user, name="Family")
        add_members(person_list, [zoe])
        before = self.revision()
        add_members(person_list, [zoe])
        self.assertEqual(self.revision(), before)

    def test_a_new_member_uid_stamps_its_lists(self):
        zoe = create_person(owner=self.user, display_name="Zoe")
        person_list = create_list(owner=self.user, name="Family")
        add_members(person_list, [zoe])
        before = self.revision()
        update_person(zoe, import_uid="zoe-from-a-file")
        self.assertEqual(self.changed_since(before), {zoe.pk, person_list.pk})

    def test_an_import_reaches_the_log(self):
        report = import_vcards(
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sam\r\nUID:sam-1\r\nEND:VCARD\r\n",
            owner=self.user,
        )
        self.assertEqual(self.changed_since(0), {report.persons[0].pk})

    def test_deleting_the_owner_or_the_group_cascades_cleanly(self):
        create_person(owner=self.user, display_name="Zoe")
        team_person = create_person(group=self.team, display_name="Shared")
        team_list = create_list(group=self.team, name="Crew")
        add_members(team_list, [team_person])

        self.team.delete()
        self.user.delete()
        self.assertFalse(AddressBook.objects.exists())
        self.assertFalse(AddressBookChange.objects.exists())
