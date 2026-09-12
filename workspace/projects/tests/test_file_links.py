"""Task-to-file links: creation, removal, per-viewer serialization."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.projects.models import TaskEvent, TaskFileLink
from workspace.projects.services.file_links import (
    file_links_for_task,
    link_files,
    unlink_file,
)
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class LinkFilesTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.doc = self._make_file(self.admin, "spec.md")

    def _make_file(self, owner, name, content=b"hello"):
        return FileService.create_file(
            owner,
            name,
            content=SimpleUploadedFile(name, content, content_type="text/plain"),
        )

    def test_link_creates_a_row_and_an_event_per_file(self):
        other = self._make_file(self.admin, "notes.txt")
        created = link_files(self.admin, self.task, [self.doc, other])
        self.assertEqual(len(created), 2)
        self.assertEqual(self.task.file_links.count(), 2)
        events = self.task.events.filter(type=TaskEvent.Type.FILE_LINKED)
        self.assertEqual(events.count(), 2)
        self.assertEqual(
            set(events.values_list("to_value", flat=True)), {"spec.md", "notes.txt"}
        )
        self.assertEqual(
            set(events.values_list("to_ref", flat=True)), {self.doc.uuid, other.uuid}
        )
        self.assertTrue(all(ev.actor == self.admin for ev in events))

    def test_linking_the_same_file_twice_is_a_no_op(self):
        link_files(self.admin, self.task, [self.doc])
        created = link_files(self.member, self.task, [self.doc])
        self.assertEqual(created, [])
        self.assertEqual(self.task.file_links.count(), 1)
        self.assertEqual(
            self.task.events.filter(type=TaskEvent.Type.FILE_LINKED).count(), 1
        )

    def test_unlink_removes_the_row_and_records_an_event(self):
        (link,) = link_files(self.admin, self.task, [self.doc])
        unlink_file(link, actor=self.member)
        self.assertFalse(TaskFileLink.objects.exists())
        event = self.task.events.get(type=TaskEvent.Type.FILE_UNLINKED)
        self.assertEqual(event.actor, self.member)
        self.assertEqual(event.to_value, "spec.md")
        self.assertEqual(event.to_ref, self.doc.uuid)

    def test_link_survives_a_rename_and_reads_the_live_name(self):
        link_files(self.admin, self.task, [self.doc])
        FileService.rename(self.doc, "renamed.md")
        (item,) = file_links_for_task(self.admin, self.task)
        self.assertEqual(item["name"], "renamed.md")

    def test_hard_deleting_the_file_drops_the_link(self):
        link_files(self.admin, self.task, [self.doc])
        FileService.hard_delete(self.doc)
        self.assertFalse(TaskFileLink.objects.exists())


class FileLinksForTaskTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        self.doc = FileService.create_file(
            self.admin,
            "spec.md",
            content=SimpleUploadedFile("spec.md", b"# spec", content_type="text/plain"),
        )
        link_files(self.admin, self.task, [self.doc])

    def test_owner_sees_the_link_with_the_file_summary(self):
        (item,) = file_links_for_task(self.admin, self.task)
        self.assertEqual(item["file_uuid"], str(self.doc.uuid))
        self.assertEqual(item["name"], "spec.md")
        self.assertEqual(item["size"], 6)
        self.assertEqual(item["added_by"], "admin1")
        self.assertFalse(item["in_trash"])
        self.assertEqual(
            item["download_url"], f"/api/v1/files/{self.doc.uuid}/download"
        )
        self.assertIn("type_icon", item)
        self.assertIn("type_color", item)

    def test_member_without_file_access_does_not_see_the_link(self):
        self.assertEqual(file_links_for_task(self.member, self.task), [])

    def test_member_with_a_share_sees_the_link(self):
        share_file(
            self.doc, target_user=self.member, permission="r", acting_user=self.admin
        )
        (item,) = file_links_for_task(self.member, self.task)
        self.assertEqual(item["file_uuid"], str(self.doc.uuid))

    def test_trashed_file_stays_visible_to_its_owner_only(self):
        share_file(
            self.doc, target_user=self.member, permission="rw", acting_user=self.admin
        )
        FileService.soft_delete(self.doc)
        (item,) = file_links_for_task(self.admin, self.task)
        self.assertTrue(item["in_trash"])
        self.assertEqual(file_links_for_task(self.member, self.task), [])
