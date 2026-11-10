from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileShare
from workspace.files.services import trash, tree
from workspace.files.services.refusals import Refusal, Refused
from workspace.files.services.trash import purge_item, restore_item, trash_item
from workspace.notifications.models import Notification

User = get_user_model()


def _file(owner, name, parent=None, group=None):
    return tree.create_file(
        owner, name, parent=parent, group=group, content=ContentFile(b"x", name=name)
    )


def _reload(node):
    return File.objects.filter(pk=node.pk).first()


class TrashFixture(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.team = Group.objects.create(name="Design")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.team_root = tree.create_folder(self.alice, "Design", group=self.team)

    def assertRefused(self, reason, action, *args):
        with self.assertRaises(Refused) as caught:
            action(*args)
        self.assertEqual(caught.exception.reason, reason)


class TrashItemTests(TrashFixture):
    def test_a_folder_goes_to_the_trash_with_what_it_holds(self):
        folder = tree.create_folder(self.alice, "Docs")
        inner = _file(self.alice, "a.txt", folder)

        trash_item(self.alice, folder)

        self.assertIsNotNone(_reload(folder).deleted_at)
        self.assertIsNotNone(_reload(inner).deleted_at)

    def test_people_it_was_shared_with_are_told(self):
        report = _file(self.alice, "report.txt")
        FileShare.objects.create(
            file=report,
            shared_by=self.alice,
            shared_with=self.bob,
            permission=FileShare.Permission.READ_ONLY,
        )

        with self.captureOnCommitCallbacks(execute=True):
            trash_item(self.alice, report)

        notice = Notification.objects.get(recipient=self.bob, origin="files")
        self.assertEqual(notice.title, 'alice deleted "report.txt"')

    def test_whoever_trashed_it_is_not_told(self):
        report = _file(self.alice, "report.txt")
        FileShare.objects.create(
            file=report,
            shared_by=self.alice,
            shared_with_group=self.team,
            permission=FileShare.Permission.READ_ONLY,
        )

        with self.captureOnCommitCallbacks(execute=True):
            trash_item(self.alice, report)

        self.assertFalse(Notification.objects.filter(recipient=self.alice).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.bob).exists())

    def test_a_read_only_share_cannot_trash(self):
        report = _file(self.alice, "report.txt")
        FileShare.objects.create(
            file=report,
            shared_by=self.alice,
            shared_with=self.bob,
            permission=FileShare.Permission.READ_ONLY,
        )

        self.assertRefused(Refusal.NOT_ALLOWED, trash_item, self.bob, report)
        self.assertIsNone(_reload(report).deleted_at)

    def test_a_groups_root_folder_stays(self):
        self.assertRefused(Refusal.NOT_ALLOWED, trash_item, self.alice, self.team_root)
        self.assertIsNone(_reload(self.team_root).deleted_at)

    def test_a_file_someone_else_is_editing_stays(self):
        report = _file(self.alice, "report.txt", self.team_root, self.team)
        File.objects.filter(pk=report.pk).update(
            locked_by=self.bob,
            locked_at=timezone.now(),
            lock_expires_at=timezone.now() + timedelta(minutes=5),
        )
        report.refresh_from_db()

        self.assertRefused(Refusal.LOCKED, trash_item, self.alice, report)
        self.assertIsNone(_reload(report).deleted_at)

    def test_what_is_already_in_the_trash_is_not_trashed_again(self):
        report = _file(self.alice, "report.txt")
        trash.soft_delete(report, acting_user=self.alice)

        self.assertRefused(Refusal.NOT_ALLOWED, trash_item, self.alice, report)


class RestoreItemTests(TrashFixture):
    def test_a_trashed_file_comes_back(self):
        report = _file(self.alice, "report.txt")
        trash.soft_delete(report, acting_user=self.alice)
        report.refresh_from_db()

        restore_item(self.alice, report)

        self.assertIsNone(_reload(report).deleted_at)

    def test_a_member_restores_what_another_member_trashed(self):
        theirs = _file(self.bob, "theirs.txt", self.team_root, self.team)
        trash.soft_delete(theirs, acting_user=self.bob)
        theirs.refresh_from_db()

        restore_item(self.alice, theirs)

        self.assertIsNone(_reload(theirs).deleted_at)

    def test_a_former_member_cannot_restore(self):
        theirs = _file(self.bob, "theirs.txt", self.team_root, self.team)
        trash.soft_delete(theirs, acting_user=self.bob)
        theirs.refresh_from_db()
        self.alice.groups.remove(self.team)

        self.assertRefused(Refusal.NOT_ALLOWED, restore_item, self.alice, theirs)
        self.assertIsNotNone(_reload(theirs).deleted_at)

    def test_a_live_file_is_not_restored(self):
        report = _file(self.alice, "report.txt")

        self.assertRefused(Refusal.NOT_ALLOWED, restore_item, self.alice, report)


class PurgeItemTests(TrashFixture):
    def test_a_trashed_folder_is_gone_with_what_it_held(self):
        folder = tree.create_folder(self.alice, "Docs")
        inner = _file(self.alice, "a.txt", folder)
        trash.soft_delete(folder, acting_user=self.alice)
        folder.refresh_from_db()

        purge_item(self.alice, folder)

        self.assertIsNone(_reload(folder))
        self.assertIsNone(_reload(inner))

    def test_a_member_purges_what_another_member_trashed(self):
        theirs = _file(self.bob, "theirs.txt", self.team_root, self.team)
        trash.soft_delete(theirs, acting_user=self.bob)
        theirs.refresh_from_db()

        purge_item(self.alice, theirs)

        self.assertIsNone(_reload(theirs))

    def test_a_live_file_is_not_purged(self):
        report = _file(self.alice, "report.txt")

        self.assertRefused(Refusal.NOT_ALLOWED, purge_item, self.alice, report)
        self.assertIsNotNone(_reload(report))

    def test_someone_elses_trash_is_out_of_reach(self):
        theirs = _file(self.bob, "theirs.txt")
        trash.soft_delete(theirs, acting_user=self.bob)
        theirs.refresh_from_db()

        self.assertRefused(Refusal.NOT_ALLOWED, purge_item, self.alice, theirs)
        self.assertIsNotNone(_reload(theirs))
