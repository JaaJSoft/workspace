"""Sharing a file with a project.

A project share is resolved at read time through project access
(``user_project_ids``): individual members and members of the attached
groups can open the file, joining or leaving the project needs no row
change.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileEvent, FileShare
from workspace.files.services import FilePermission, FileService
from workspace.files.services.comments import mentionable_users
from workspace.files.services.sharing import share_file, unshare_file
from workspace.files.sse_provider import FilesSSEProvider, push_file_event
from workspace.notifications.models import Notification
from workspace.projects.services.members import add_member, remove_member
from workspace.projects.services.projects import create_project

User = get_user_model()


class ProjectShareFixtures:
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.member = User.objects.create_user(username="member", password="pw")
        self.grouped = User.objects.create_user(username="grouped", password="pw")
        self.outsider = User.objects.create_user(username="outsider", password="pw")
        self.project = create_project(self.owner, name="Website")
        self.membership = add_member(self.project, self.member)
        self.team = Group.objects.create(name="Team")
        self.grouped.groups.add(self.team)
        self.project.groups.add(self.team)
        self.file = FileService.create_file(
            self.owner, "doc.txt", content=ContentFile(b"hello"), mime_type="text/plain"
        )

    def _share_with_project(self, permission="ro"):
        return FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with_project=self.project,
            permission=permission,
        )


class ProjectShareConstraintTests(ProjectShareFixtures, TestCase):
    def test_project_share_row(self):
        share = self._share_with_project()
        self.assertIsNone(share.shared_with)
        self.assertIsNone(share.shared_with_group)
        self.assertEqual(share.target, self.project)

    def test_project_and_user_together_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            FileShare.objects.create(
                file=self.file,
                shared_by=self.owner,
                shared_with=self.member,
                shared_with_project=self.project,
            )

    def test_one_share_per_file_and_project(self):
        self._share_with_project()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._share_with_project()

    def test_deleting_the_project_drops_the_share(self):
        self._share_with_project()
        self.project.delete()
        self.assertFalse(FileShare.objects.exists())


class ProjectSharePermissionTests(ProjectShareFixtures, TestCase):
    def test_member_gets_view_through_the_project(self):
        self._share_with_project()
        self.assertEqual(
            FileService.get_permission(self.member, self.file), FilePermission.VIEW
        )

    def test_group_member_gets_write_through_the_project(self):
        self._share_with_project("rw")
        self.assertEqual(
            FileService.get_permission(self.grouped, self.file), FilePermission.WRITE
        )

    def test_outsider_gets_nothing(self):
        self._share_with_project("rw")
        self.assertIsNone(FileService.get_permission(self.outsider, self.file))

    def test_leaving_the_project_revokes_access(self):
        self._share_with_project()
        remove_member(self.membership)
        self.assertIsNone(FileService.get_permission(self.member, self.file))

    def test_accessible_files_include_the_project_share(self):
        self._share_with_project()
        accessible = File.objects.filter(FileService.accessible_files_q(self.member))
        self.assertIn(self.file, accessible)
        ids = set(FileService.accessible_file_ids(self.member))
        self.assertIn(self.file.pk, ids)
        self.assertNotIn(
            self.file,
            File.objects.filter(FileService.accessible_files_q(self.outsider)),
        )


class ProjectShareServiceTests(ProjectShareFixtures, TestCase):
    def test_share_file_creates_the_row_and_the_event(self):
        share, created, changed = share_file(
            self.file,
            target_project=self.project,
            permission="rw",
            acting_user=self.owner,
        )
        self.assertTrue(created)
        self.assertFalse(changed)
        self.assertEqual(share.shared_with_project, self.project)
        event = FileEvent.objects.get(file=self.file, action=FileEvent.Action.SHARED)
        self.assertEqual(event.metadata["shared_with_project_name"], "Website")
        self.assertEqual(event.metadata["shared_with_project_id"], str(self.project.pk))

    def test_share_file_updates_the_permission(self):
        share_file(
            self.file,
            target_project=self.project,
            permission="ro",
            acting_user=self.owner,
        )
        _, created, changed = share_file(
            self.file,
            target_project=self.project,
            permission="rw",
            acting_user=self.owner,
        )
        self.assertFalse(created)
        self.assertTrue(changed)
        self.assertEqual(
            FileShare.objects.get(shared_with_project=self.project).permission, "rw"
        )

    def test_unshare_file_removes_the_row(self):
        self._share_with_project()
        deleted = unshare_file(
            self.file, target_project=self.project, acting_user=self.owner
        )
        self.assertEqual(deleted, 1)
        self.assertFalse(FileShare.objects.exists())
        self.assertTrue(
            FileEvent.objects.filter(
                file=self.file, action=FileEvent.Action.UNSHARED
            ).exists()
        )

    def test_two_targets_rejected(self):
        with self.assertRaises(ValueError):
            share_file(
                self.file,
                target_group=self.team,
                target_project=self.project,
                permission="ro",
                acting_user=self.owner,
            )


class ProjectShareAPITests(ProjectShareFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/files/{self.file.uuid}/share"

    def test_owner_shares_with_a_project_they_can_open(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url, {"project": str(self.project.uuid), "permission": "rw"}
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        share = FileShare.objects.get()
        self.assertEqual(share.shared_with_project, self.project)
        self.assertEqual(share.permission, "rw")

    def test_share_notifies_the_project_members_but_the_actor(self):
        self.client.force_authenticate(self.owner)
        self.client.post(self.url, {"project": str(self.project.uuid)})
        recipients = set(
            Notification.objects.filter(origin="files").values_list(
                "recipient__username", flat=True
            )
        )
        self.assertEqual(recipients, {"member", "grouped"})

    def test_sharing_with_a_project_the_owner_cannot_open_is_404(self):
        secret = create_project(self.member, name="Secret")
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {"project": str(secret.uuid)})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(FileShare.objects.exists())

    def test_malformed_project_id_is_404(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(self.url, {"project": "not-a-uuid"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_two_targets_at_once_is_400(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self.url, {"project": str(self.project.uuid), "group": self.team.pk}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unshare_removes_the_project_share(self):
        self._share_with_project()
        self.client.force_authenticate(self.owner)
        resp = self.client.delete(self.url, {"project": str(self.project.uuid)})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(FileShare.objects.exists())

    def test_shares_lists_the_project_entry(self):
        self._share_with_project("rw")
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/shares")
        (entry,) = resp.data
        self.assertEqual(entry["type"], "project")
        self.assertEqual(entry["id"], str(self.project.uuid))
        self.assertEqual(entry["name"], "Website")
        self.assertEqual(entry["permission"], "rw")

    def test_member_reads_the_content_through_the_project_share(self):
        self._share_with_project()
        url = f"/api/v1/files/{self.file.uuid}/content"
        self.client.force_authenticate(self.member)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(url)
        self.assertIn(
            resp.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
        )


class ProjectShareFanOutTests(ProjectShareFixtures, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_mentionable_users_include_the_project_members(self):
        self._share_with_project()
        names = [u.username for u in mentionable_users(self.file)]
        self.assertEqual(names, ["grouped", "member", "owner"])

    def test_sse_event_reaches_the_project_members(self):
        self._share_with_project()
        member_stream = FilesSSEProvider(self.member, None)
        grouped_stream = FilesSSEProvider(self.grouped, None)
        outsider_stream = FilesSSEProvider(self.outsider, None)
        push_file_event(self.file, "file.updated", "owner")
        self.assertEqual(len(member_stream.poll("dirty")), 1)
        self.assertEqual(len(grouped_stream.poll("dirty")), 1)
        self.assertEqual(outsider_stream.poll("dirty"), [])
