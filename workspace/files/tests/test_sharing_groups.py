"""Sharing a file with an ``auth.Group``.

A group share is resolved at read time through the viewer's group
membership: joining the group grants access, leaving it revokes it, and no
row is written on either transition.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileEvent, FileShare
from workspace.files.services import FilePermission, FileService
from workspace.files.services.comments import mentionable_users
from workspace.files.services.sharing import share_file, unshare_file
from workspace.files.sse_provider import FilesSSEProvider, push_file_event
from workspace.notifications.models import Notification

User = get_user_model()


class GroupShareFixtures:
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.member = User.objects.create_user(username="member", password="pw")
        self.outsider = User.objects.create_user(username="outsider", password="pw")
        self.team = Group.objects.create(name="Team")
        self.member.groups.add(self.team)
        self.file = FileService.create_file(
            self.owner, "doc.txt", content=ContentFile(b"hello"), mime_type="text/plain"
        )

    def _share_with_team(self, permission="ro"):
        return FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with_group=self.team,
            permission=permission,
        )


class FileShareTargetConstraintTests(GroupShareFixtures, TestCase):
    def test_group_share_row(self):
        share = self._share_with_team()
        self.assertIsNone(share.shared_with)
        self.assertEqual(share.shared_with_group, self.team)

    def test_exactly_one_target_both_set_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            FileShare.objects.create(
                file=self.file,
                shared_by=self.owner,
                shared_with=self.member,
                shared_with_group=self.team,
            )

    def test_exactly_one_target_none_set_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            FileShare.objects.create(file=self.file, shared_by=self.owner)

    def test_one_share_per_file_and_group(self):
        self._share_with_team()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._share_with_team()

    def test_one_share_per_file_and_user_still_enforced(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.member
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            FileShare.objects.create(
                file=self.file, shared_by=self.owner, shared_with=self.member
            )

    def test_a_user_share_and_a_group_share_coexist_on_one_file(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.member
        )
        self._share_with_team()
        self.assertEqual(FileShare.objects.filter(file=self.file).count(), 2)

    def test_deleting_the_group_removes_the_share(self):
        self._share_with_team()
        self.team.delete()
        self.assertEqual(FileShare.objects.count(), 0)

    def test_reaching_selects_user_and_group_shares(self):
        direct = FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.outsider
        )
        via_group = self._share_with_team()
        self.assertEqual(set(FileShare.objects.reaching(self.member)), {via_group})
        self.assertEqual(set(FileShare.objects.reaching(self.outsider)), {direct})
        self.assertEqual(set(FileShare.objects.reaching(self.owner)), set())


class GroupSharePermissionTests(GroupShareFixtures, TestCase):
    def test_ro_group_share_grants_view_to_members(self):
        self._share_with_team("ro")
        self.assertEqual(
            FileService.get_permission(self.member, self.file), FilePermission.VIEW
        )

    def test_rw_group_share_grants_write_to_members(self):
        self._share_with_team("rw")
        self.assertEqual(
            FileService.get_permission(self.member, self.file), FilePermission.WRITE
        )

    def test_non_member_gets_nothing(self):
        self._share_with_team("rw")
        self.assertIsNone(FileService.get_permission(self.outsider, self.file))

    def test_leaving_the_group_revokes_access(self):
        self._share_with_team("rw")
        self.member.groups.remove(self.team)
        self.assertIsNone(FileService.get_permission(self.member, self.file))

    def test_joining_the_group_grants_access(self):
        self._share_with_team("rw")
        self.outsider.groups.add(self.team)
        self.assertEqual(
            FileService.get_permission(self.outsider, self.file),
            FilePermission.WRITE,
        )

    def test_highest_permission_wins_across_user_and_group_shares(self):
        self._share_with_team("ro")
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.member,
            permission="rw",
        )
        self.assertEqual(
            FileService.get_permission(self.member, self.file), FilePermission.WRITE
        )

    def test_trashed_file_is_not_reachable_through_a_group_share(self):
        self._share_with_team("rw")
        FileService.soft_delete(self.file, acting_user=self.owner)
        self.file.refresh_from_db()
        self.assertIsNone(FileService.get_permission(self.member, self.file))

    def test_bulk_resolution_mixes_targets_in_one_pass(self):
        other = File.objects.create(
            owner=self.owner, name="other.txt", node_type=File.NodeType.FILE
        )
        self._share_with_team("ro")
        FileShare.objects.create(
            file=other, shared_by=self.owner, shared_with=self.member, permission="rw"
        )
        perms = FileService.get_permissions_bulk(self.member, [self.file, other])
        self.assertEqual(perms[self.file.pk], FilePermission.VIEW)
        self.assertEqual(perms[other.pk], FilePermission.WRITE)


class GroupShareAccessQuerysetTests(GroupShareFixtures, TestCase):
    def test_accessible_files_q_includes_group_shared_file(self):
        self._share_with_team()
        qs = File.objects.filter(FileService.accessible_files_q(self.member))
        self.assertIn(self.file, qs)
        qs = File.objects.filter(FileService.accessible_files_q(self.outsider))
        self.assertNotIn(self.file, qs)

    def test_accessible_file_ids_includes_group_shared_file(self):
        self._share_with_team()
        self.assertIn(self.file.pk, set(FileService.accessible_file_ids(self.member)))
        self.assertNotIn(
            self.file.pk, set(FileService.accessible_file_ids(self.outsider))
        )

    def test_no_duplicates_when_shared_with_user_and_group(self):
        self._share_with_team()
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.member
        )
        qs = File.objects.filter(FileService.accessible_files_q(self.member))
        self.assertEqual(qs.distinct().count(), 1)
        self.assertEqual(len(list(FileService.accessible_file_ids(self.member))), 1)


class GroupShareServiceTests(GroupShareFixtures, TestCase):
    def test_share_file_records_group_in_event(self):
        share, created, changed = share_file(
            self.file,
            target_group=self.team,
            permission="rw",
            acting_user=self.owner,
        )
        self.assertTrue(created)
        self.assertFalse(changed)
        self.assertEqual(share.shared_with_group, self.team)
        ev = FileEvent.objects.get(file=self.file, action=FileEvent.Action.SHARED)
        self.assertEqual(ev.metadata["shared_with_group_id"], self.team.pk)
        self.assertEqual(ev.metadata["shared_with_group_name"], "Team")
        self.assertEqual(ev.metadata["permission"], "rw")
        self.assertNotIn("shared_with_id", ev.metadata)

    def test_share_file_updates_group_permission(self):
        share_file(
            self.file, target_group=self.team, permission="ro", acting_user=self.owner
        )
        share, created, changed = share_file(
            self.file, target_group=self.team, permission="rw", acting_user=self.owner
        )
        self.assertFalse(created)
        self.assertTrue(changed)
        self.assertEqual(share.permission, "rw")
        ev = FileEvent.objects.get(
            file=self.file, action=FileEvent.Action.SHARE_PERMISSION_CHANGED
        )
        self.assertEqual(ev.metadata["shared_with_group_name"], "Team")
        self.assertEqual(ev.metadata["old_permission"], "ro")
        self.assertEqual(ev.metadata["new_permission"], "rw")

    def test_unshare_file_group(self):
        self._share_with_team()
        self.assertEqual(
            unshare_file(self.file, target_group=self.team, acting_user=self.owner), 1
        )
        self.assertEqual(FileShare.objects.count(), 0)
        ev = FileEvent.objects.get(file=self.file, action=FileEvent.Action.UNSHARED)
        self.assertEqual(ev.metadata["shared_with_group_name"], "Team")

    def test_unshare_group_leaves_user_share_alone(self):
        self._share_with_team()
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.member
        )
        unshare_file(self.file, target_group=self.team, acting_user=self.owner)
        self.assertEqual(FileShare.objects.filter(shared_with=self.member).count(), 1)

    def test_exactly_one_target_required(self):
        with self.assertRaises(ValueError):
            share_file(self.file, permission="ro", acting_user=self.owner)
        with self.assertRaises(ValueError):
            share_file(
                self.file,
                target_user=self.member,
                target_group=self.team,
                permission="ro",
                acting_user=self.owner,
            )


class GroupShareAPITests(GroupShareFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.url = f"/api/v1/files/{self.file.uuid}/share"
        self.client.force_authenticate(user=self.owner)

    def tearDown(self):
        cache.clear()

    def test_share_with_group(self):
        resp = self.client.post(
            self.url, {"group": self.team.pk, "permission": "rw"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data, {"shared": True, "permission": "rw"})
        share = FileShare.objects.get(file=self.file)
        self.assertEqual(share.shared_with_group, self.team)
        self.assertIsNone(share.shared_with)

    def test_share_with_group_default_ro(self):
        resp = self.client.post(self.url, {"group": self.team.pk}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(FileShare.objects.get(file=self.file).permission, "ro")

    def test_share_with_group_updates_permission(self):
        self._share_with_team("ro")
        resp = self.client.post(
            self.url, {"group": self.team.pk, "permission": "rw"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(FileShare.objects.get(file=self.file).permission, "rw")

    def test_share_with_group_the_sharer_is_not_in(self):
        self.assertFalse(self.owner.groups.filter(pk=self.team.pk).exists())
        resp = self.client.post(self.url, {"group": self.team.pk}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_share_with_unknown_group(self):
        resp = self.client.post(self.url, {"group": 999999}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_share_with_malformed_group(self):
        resp = self.client.post(self.url, {"group": "abc"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_share_requires_exactly_one_target(self):
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        resp = self.client.post(
            self.url,
            {"shared_with": self.member.pk, "group": self.team.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_share_folder_with_group_rejected(self):
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        resp = self.client.post(
            f"/api/v1/files/{folder.uuid}/share",
            {"group": self.team.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_share_with_group_notifies_members_but_not_the_sharer(self):
        self.owner.groups.add(self.team)
        resp = self.client.post(self.url, {"group": self.team.pk}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        recipients = set(
            Notification.objects.filter(origin="files").values_list(
                "recipient_id", flat=True
            )
        )
        self.assertEqual(recipients, {self.member.pk})

    def test_unshare_group(self):
        self._share_with_team()
        resp = self.client.delete(self.url, {"group": self.team.pk}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {"shared": False})
        self.assertEqual(FileShare.objects.count(), 0)

    def test_unshare_group_not_shared(self):
        resp = self.client.delete(self.url, {"group": self.team.pk}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_shares_list_carries_a_type_discriminator(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.outsider,
            permission="rw",
        )
        self._share_with_team("ro")
        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/shares")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_type = {entry["type"]: entry for entry in resp.data}
        self.assertEqual(set(by_type), {"user", "group"})
        self.assertEqual(by_type["user"]["id"], self.outsider.pk)
        self.assertEqual(by_type["user"]["username"], "outsider")
        self.assertEqual(by_type["user"]["permission"], "rw")
        self.assertEqual(by_type["group"]["id"], self.team.pk)
        self.assertEqual(by_type["group"]["name"], "Team")
        self.assertEqual(by_type["group"]["permission"], "ro")
        self.assertIn("shared_at", by_type["group"])

    def test_shared_with_me_lists_group_shared_file(self):
        self._share_with_team()
        self.client.force_authenticate(user=self.member)
        resp = self.client.get("/api/v1/files/shared-with-me")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([f["uuid"] for f in resp.data], [str(self.file.uuid)])

    def test_shared_with_me_lists_a_doubly_shared_file_once(self):
        self._share_with_team()
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.member
        )
        self.client.force_authenticate(user=self.member)
        resp = self.client.get("/api/v1/files/shared-with-me")
        self.assertEqual(len(resp.data), 1)

    def test_shared_with_me_empty_for_non_member(self):
        self._share_with_team()
        self.client.force_authenticate(user=self.outsider)
        resp = self.client.get("/api/v1/files/shared-with-me")
        self.assertEqual(resp.data, [])

    def test_member_can_read_content_and_outsider_cannot(self):
        self._share_with_team()
        url = f"/api/v1/files/{self.file.uuid}/content"
        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.outsider)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_rw_member_can_write_content_ro_member_cannot(self):
        self._share_with_team("ro")
        url = f"/api/v1/files/{self.file.uuid}"
        self.client.force_authenticate(user=self.member)

        def upload():
            payload = SimpleUploadedFile(
                "doc.txt", b"changed", content_type="text/plain"
            )
            return self.client.patch(url, {"content": payload}, format="multipart")

        self.assertEqual(upload().status_code, status.HTTP_404_NOT_FOUND)
        FileShare.objects.filter(file=self.file).update(permission="rw")
        self.assertEqual(upload().status_code, status.HTTP_200_OK)

    def test_deleting_the_file_notifies_group_members(self):
        self._share_with_team()
        resp = self.client.delete(f"/api/v1/files/{self.file.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.member, title__contains="deleted"
            ).exists()
        )


class GroupShareUITests(GroupShareFixtures, TestCase):
    def test_shared_view_lists_group_shared_file(self):
        self._share_with_team()
        self.client.force_login(self.member)
        resp = self.client.get("/files?shared=1")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "doc.txt")

    def test_properties_panel_lists_group_share(self):
        self._share_with_team("rw")
        self.client.force_login(self.owner)
        resp = self.client.get(
            reverse("files_ui:properties", kwargs={"uuid": self.file.uuid})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Team")


class GroupShareFanOutTests(GroupShareFixtures, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_sse_event_reaches_group_members(self):
        self._share_with_team()
        member_stream = FilesSSEProvider(self.member, None)
        outsider_stream = FilesSSEProvider(self.outsider, None)
        push_file_event(self.file, "file.updated", "owner")
        self.assertEqual(len(member_stream.poll("dirty")), 1)
        self.assertEqual(outsider_stream.poll("dirty"), [])

    def test_group_members_are_mentionable(self):
        self._share_with_team()
        self.assertEqual(
            {u.pk for u in mentionable_users(self.file)},
            {self.owner.pk, self.member.pk},
        )

    def test_inactive_group_member_is_not_mentionable(self):
        self._share_with_team()
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        self.assertEqual({u.pk for u in mentionable_users(self.file)}, {self.owner.pk})
