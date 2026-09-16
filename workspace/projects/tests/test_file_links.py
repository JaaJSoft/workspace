"""Task-to-file links: sharing with the project, pinning, unlinking."""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from workspace.files.models import FileShare
from workspace.files.services import FilePermission, FileService
from workspace.files.services.sharing import share_file
from workspace.projects.models import TaskEvent, TaskFileLink
from workspace.projects.services.file_links import (
    file_links_for_task,
    link_files,
    unlink_file,
)
from workspace.projects.services.members import ProjectRuleError
from workspace.projects.services.tasks import create_task, delete_task

from .base import ProjectTestMixin


def make_file(owner, name, content=b"hello"):
    return FileService.create_file(
        owner,
        name,
        content=SimpleUploadedFile(name, content, content_type="text/plain"),
    )


class LinkFilesTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.doc = make_file(self.admin, "spec.md")

    def test_link_shares_the_file_with_the_project_and_pins_it(self):
        (link,) = link_files(self.admin, self.task, [self.doc])
        share = FileShare.objects.get()
        self.assertEqual(share.file, self.doc)
        self.assertEqual(share.shared_with_project, self.project)
        self.assertEqual(share.permission, "ro")
        self.assertEqual(share.shared_by, self.admin)
        self.assertEqual(link.share, share)
        self.assertEqual(link.created_by, self.admin)
        # The member never had a share of their own: the project share is
        # what lets them open it.
        self.assertEqual(
            FileService.get_permission(self.member, self.doc), FilePermission.VIEW
        )

    def test_link_with_write_permission(self):
        link_files(self.admin, self.task, [self.doc], permission="rw")
        self.assertEqual(
            FileService.get_permission(self.member, self.doc), FilePermission.WRITE
        )

    def test_link_records_an_event_per_file(self):
        other = make_file(self.admin, "notes.txt")
        link_files(self.admin, self.task, [self.doc, other])
        events = self.task.events.filter(type=TaskEvent.Type.FILE_LINKED)
        self.assertEqual(
            set(events.values_list("to_value", flat=True)), {"spec.md", "notes.txt"}
        )
        self.assertEqual(
            set(events.values_list("to_ref", flat=True)), {self.doc.uuid, other.uuid}
        )

    def test_existing_project_share_is_reused_with_its_own_permission(self):
        share_file(
            self.doc,
            target_project=self.project,
            permission="rw",
            acting_user=self.admin,
        )
        (link,) = link_files(self.admin, self.task, [self.doc], permission="ro")
        self.assertEqual(FileShare.objects.count(), 1)
        self.assertEqual(link.share.permission, "rw")

    def test_two_tasks_share_one_project_share(self):
        other_task = create_task(self.project, self.admin, title="Review it")
        link_files(self.admin, self.task, [self.doc])
        link_files(self.admin, other_task, [self.doc])
        self.assertEqual(FileShare.objects.count(), 1)
        self.assertEqual(TaskFileLink.objects.count(), 2)

    def test_linking_the_same_file_twice_is_a_no_op(self):
        link_files(self.admin, self.task, [self.doc])
        created = link_files(self.admin, self.task, [self.doc])
        self.assertEqual(created, [])
        self.assertEqual(self.task.file_links.count(), 1)

    def test_same_file_twice_in_one_call_creates_one_link(self):
        created = link_files(self.admin, self.task, [self.doc, self.doc])
        self.assertEqual(len(created), 1)

    def test_a_file_the_user_cannot_share_is_refused_whole(self):
        # A read-only share lets the member open the file, not share it on.
        share_file(
            self.doc, target_user=self.member, permission="ro", acting_user=self.admin
        )
        mine = make_file(self.member, "mine.txt")
        with self.assertRaises(ProjectRuleError):
            link_files(self.member, self.task, [mine, self.doc])
        self.assertFalse(TaskFileLink.objects.exists())
        self.assertFalse(
            FileShare.objects.filter(shared_with_project=self.project).exists()
        )

    def test_ownership_follows_what_the_share_service_created(self):
        # A share landing between the pre-check and share_file (a concurrent
        # link on SQLite, where the row lock is a no-op) must not be owned:
        # the service reports it as not created.
        share = FileShare.objects.create(
            file=self.doc, shared_by=self.admin, shared_with_project=self.project
        )
        with (
            patch(
                "workspace.projects.services.file_links.FileShare.objects.select_for_update"
            ) as locked,
            patch(
                "workspace.projects.services.file_links.share_file",
                return_value=(share, False, False),
            ),
        ):
            locked.return_value.filter.return_value.first.return_value = None
            (link,) = link_files(self.admin, self.task, [self.doc])
        self.assertFalse(link.owns_share)

    def test_a_write_share_is_not_enough_to_link(self):
        # Sharing on needs the files "share" action, which a share never grants.
        share_file(
            self.doc, target_user=self.member, permission="rw", acting_user=self.admin
        )
        with self.assertRaises(ProjectRuleError):
            link_files(self.member, self.task, [self.doc])


class UnlinkFileTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.doc = make_file(self.admin, "spec.md")
        (self.link,) = link_files(self.admin, self.task, [self.doc])

    def test_unlinking_the_last_link_revokes_the_project_share(self):
        unlink_file(self.link, actor=self.member)
        self.assertFalse(TaskFileLink.objects.exists())
        self.assertFalse(FileShare.objects.exists())
        self.assertIsNone(FileService.get_permission(self.member, self.doc))
        event = self.task.events.get(type=TaskEvent.Type.FILE_UNLINKED)
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.to_value, "spec.md")

    def test_unlinking_keeps_the_share_while_another_task_links_it(self):
        other_task = create_task(self.project, self.admin, title="Review it")
        link_files(self.admin, other_task, [self.doc])
        unlink_file(self.link, actor=self.admin)
        self.assertEqual(TaskFileLink.objects.count(), 1)
        self.assertEqual(FileShare.objects.count(), 1)

    def test_a_share_that_predates_the_link_survives_the_last_unlink(self):
        other = make_file(self.admin, "brief.txt")
        share_file(
            other, target_project=self.project, permission="ro", acting_user=self.admin
        )
        (link,) = link_files(self.admin, self.task, [other])
        self.assertFalse(link.owns_share)
        unlink_file(link, actor=self.admin)
        self.assertTrue(
            FileShare.objects.filter(
                file=other, shared_with_project=self.project
            ).exists()
        )

    def test_share_ownership_follows_the_remaining_link(self):
        # The first link created the share; once it goes, the surviving link
        # takes the revocation over so the share never outlives every link.
        other_task = create_task(self.project, self.admin, title="Review it")
        (second,) = link_files(self.admin, other_task, [self.doc])
        self.assertTrue(self.link.owns_share)
        self.assertFalse(second.owns_share)
        unlink_file(self.link, actor=self.admin)
        second.refresh_from_db()
        self.assertTrue(second.owns_share)
        unlink_file(second, actor=self.admin)
        self.assertFalse(FileShare.objects.exists())

    def test_deleting_the_task_revokes_the_share_its_link_created(self):
        delete_task(self.task, actor=self.admin)
        self.assertFalse(TaskFileLink.objects.exists())
        self.assertFalse(FileShare.objects.exists())

    def test_deleting_the_task_keeps_a_share_that_predates_the_link(self):
        other = make_file(self.admin, "brief.txt")
        share_file(
            other, target_project=self.project, permission="ro", acting_user=self.admin
        )
        link_files(self.admin, self.task, [other])
        delete_task(self.task, actor=self.admin)
        self.assertTrue(
            FileShare.objects.filter(
                file=other, shared_with_project=self.project
            ).exists()
        )

    def test_revoking_the_share_drops_the_link(self):
        FileShare.objects.get().delete()
        self.assertFalse(TaskFileLink.objects.exists())

    def test_hard_deleting_the_file_drops_the_link(self):
        FileService.hard_delete(self.doc)
        self.assertFalse(TaskFileLink.objects.exists())


class FileLinksForTaskTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.doc = make_file(self.admin, "spec.md", b"# spec")
        link_files(self.admin, self.task, [self.doc], permission="rw")

    def test_item_summarizes_the_live_file(self):
        (item,) = file_links_for_task(self.task)
        self.assertEqual(item["file_uuid"], str(self.doc.uuid))
        self.assertEqual(item["name"], "spec.md")
        self.assertEqual(item["size"], 6)
        self.assertEqual(item["permission"], "rw")
        self.assertEqual(item["added_by"], "admin1")
        self.assertFalse(item["in_trash"])
        self.assertEqual(
            item["download_url"], f"/api/v1/files/{self.doc.uuid}/download"
        )
        self.assertIn("type_icon", item)

    def test_rename_follows_through(self):
        FileService.rename(self.doc, "renamed.md")
        (item,) = file_links_for_task(self.task)
        self.assertEqual(item["name"], "renamed.md")

    def test_trashed_file_stays_listed_and_flagged(self):
        FileService.soft_delete(self.doc)
        (item,) = file_links_for_task(self.task)
        self.assertTrue(item["in_trash"])
