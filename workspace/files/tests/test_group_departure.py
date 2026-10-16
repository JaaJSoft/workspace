"""Leaving a group costs a member their rights on its nodes, their own included.

Ownership used to grant MANAGE whatever the node, so a removed member kept
reading, downloading, sharing and creating inside the group folders they had
created - and through them, everything the remaining members put there later.
Their public links and shares on the group's nodes are revoked as they leave.
"""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import (
    FILE_FEED_Q,
    File,
    FileEvent,
    FileShare,
    FileShareLink,
)
from workspace.files.services import FilePermission, FileService
from workspace.files.services.events import recent_feed_events
from workspace.files.services.sharing import create_share_link, share_file
from workspace.files.tests import test_webdav
from workspace.notifications.models import Notification
from workspace.projects.services.projects import create_project

User = get_user_model()


class _DepartureFixture:
    def _fixture(self):
        self.alice = User.objects.create_user(username="alice", password="pass123")
        self.bob = User.objects.create_user(username="bob", password="pass123")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.root = FileService.create_folder(self.bob, "TeamRoot", group=self.team)
        self.alice_sub = FileService.create_folder(
            self.alice, "AliceSub", parent=self.root
        )
        self.alice_note = FileService.create_file(
            self.alice,
            "alice.txt",
            parent=self.alice_sub,
            content=ContentFile(b"mine", name="alice.txt"),
        )
        self.bob_secret = FileService.create_file(
            self.bob,
            "secret.txt",
            parent=self.alice_sub,
            content=ContentFile(b"TOPSECRET", name="secret.txt"),
        )

    def tearDown(self):
        cache.clear()


class OwnerPermissionTests(_DepartureFixture, TestCase):
    def setUp(self):
        self._fixture()

    def test_a_member_manages_the_group_nodes_they_created(self):
        self.assertEqual(
            FileService.get_permission(self.alice, self.alice_sub),
            FilePermission.MANAGE,
        )
        self.assertEqual(
            FileService.get_permission(self.alice, self.bob_secret),
            FilePermission.EDIT,
        )

    def test_a_former_member_loses_the_group_nodes_they_created(self):
        self.alice.groups.remove(self.team)

        permissions = FileService.get_permissions_bulk(
            self.alice, [self.alice_sub, self.alice_note, self.bob_secret]
        )

        self.assertEqual(set(permissions.values()), {None})

    def test_a_former_member_loses_their_trashed_group_nodes(self):
        FileService.soft_delete(self.alice_note, acting_user=self.alice)
        self.alice_note.refresh_from_db()
        self.alice.groups.remove(self.team)

        self.assertIsNone(FileService.get_permission(self.alice, self.alice_note))

    def test_access_querysets_drop_them_too(self):
        self.alice.groups.remove(self.team)
        group_nodes = {self.alice_sub.pk, self.alice_note.pk, self.bob_secret.pk}

        by_q = set(
            File.objects.filter(FileService.accessible_files_q(self.alice))
            .values_list("pk", flat=True)
            .distinct()
        )
        by_ids = set(FileService.accessible_file_ids(self.alice))

        self.assertFalse(by_q & group_nodes)
        self.assertFalse(by_ids & group_nodes)

    def test_personal_nodes_stay_managed(self):
        mine = FileService.create_folder(self.alice, "Mine")
        self.alice.groups.remove(self.team)

        self.assertEqual(
            FileService.get_permission(self.alice, mine), FilePermission.MANAGE
        )
        self.assertIn(mine.pk, set(FileService.accessible_file_ids(self.alice)))

    def test_dashboard_feed_skips_events_on_their_former_group_nodes(self):
        FileService.rename(self.alice_note, "renamed.txt", acting_user=self.bob)
        before = recent_feed_events(
            FILE_FEED_Q, viewer=self.alice, exclude_actor_id=self.alice.pk
        )
        self.alice.groups.remove(self.team)

        after = recent_feed_events(
            FILE_FEED_Q, viewer=self.alice, exclude_actor_id=self.alice.pk
        )

        self.assertIn(self.alice_note.pk, {event.file_id for event in before})
        self.assertNotIn(self.alice_note.pk, {event.file_id for event in after})


class FormerMemberApiTests(_DepartureFixture, APITestCase):
    def setUp(self):
        self._fixture()
        self.alice.groups.remove(self.team)
        self.client.force_authenticate(self.alice)

    def test_cannot_read_a_node_below_their_former_folder(self):
        response = self.client.get(f"/api/v1/files/{self.bob_secret.uuid}/content")

        self.assertEqual(response.status_code, 404)

    def test_cannot_download_their_former_folder(self):
        response = self.client.get(f"/api/v1/files/{self.alice_sub.uuid}/download")

        self.assertEqual(response.status_code, 404)

    def test_cannot_create_inside_their_former_folder(self):
        response = self.client.post(
            "/api/v1/files",
            {"name": "x", "node_type": "folder", "parent": str(self.alice_sub.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(File.objects.filter(name="x").exists())

    def test_cannot_create_a_public_link_on_their_former_folder(self):
        response = self.client.post(
            f"/api/v1/files/{self.alice_sub.uuid}/share-links", {}, format="json"
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(FileShareLink.objects.exists())

    def test_trash_neither_lists_nor_purges_their_former_group_nodes(self):
        FileService.soft_delete(self.alice_note, acting_user=self.bob)

        listed = self.client.get("/api/v1/files/trash")
        cleaned = self.client.delete("/api/v1/files/trash/clean?force=true")

        self.assertEqual(listed.json(), [])
        self.assertEqual(cleaned.json()["deleted"], 0)
        self.assertTrue(File.objects.filter(pk=self.alice_note.pk).exists())


class FormerMemberWebDAVTests(_DepartureFixture, TestCase):
    _request = test_webdav.WebDAVIntegrationTests._request

    def setUp(self):
        from workspace.files.webdav.app import create_webdav_app

        self._app = create_webdav_app()
        self._fixture()
        self.alice.groups.remove(self.team)
        self.auth = test_webdav._basic_auth_header("alice", "pass123")

    def test_cannot_reach_their_former_folder(self):
        code, _, _ = self._request("GET", "/TeamRoot/AliceSub/secret.txt")

        self.assertEqual(code, 404)


class PublicLinkRevocationTests(_DepartureFixture, APITestCase):
    def setUp(self):
        self._fixture()
        self.link = create_share_link(self.alice_sub, acting_user=self.alice)
        self.mine = FileService.create_folder(self.alice, "Mine")
        self.personal_link = create_share_link(self.mine, acting_user=self.alice)
        self.bob_link = create_share_link(self.root, acting_user=self.bob)

    def _assert_only_alices_group_link_revoked(self):
        remaining = set(FileShareLink.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {self.personal_link.pk, self.bob_link.pk})
        response = self.client.get(f"/api/v1/files/shared/{self.link.token}")
        self.assertEqual(response.status_code, 404)

    def test_removing_the_user_from_the_group_revokes_their_links_there(self):
        self.alice.groups.remove(self.team)

        self._assert_only_alices_group_link_revoked()

    def test_removing_the_member_from_the_group_side_revokes_them_too(self):
        self.team.user_set.remove(self.alice)

        self._assert_only_alices_group_link_revoked()

    def test_clearing_the_users_groups_revokes_them(self):
        self.alice.groups.clear()

        self._assert_only_alices_group_link_revoked()

    def test_resetting_the_users_groups_revokes_them(self):
        other = Group.objects.create(name="Other")
        self.alice.groups.set([other])

        self._assert_only_alices_group_link_revoked()

    def test_clearing_the_group_revokes_every_members_links(self):
        self.team.user_set.clear()

        remaining = set(FileShareLink.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {self.personal_link.pk})

    def test_links_survive_a_membership_that_stays(self):
        other = Group.objects.create(name="Other")
        self.alice.groups.add(other)
        self.alice.groups.remove(other)

        self.assertEqual(FileShareLink.objects.count(), 3)


class ShareRevocationTests(_DepartureFixture, TestCase):
    def setUp(self):
        self._fixture()
        self.carol = User.objects.create_user(username="carol", password="pass123")
        self.friends = Group.objects.create(name="Friends")
        self.carol.groups.add(self.friends)
        self.project = create_project(self.carol, name="Site")
        rw = FileShare.Permission.READ_WRITE
        self.to_user, _, _ = share_file(
            self.alice_note,
            target_user=self.carol,
            permission=rw,
            acting_user=self.alice,
        )
        self.to_group, _, _ = share_file(
            self.bob_secret,
            target_group=self.friends,
            permission=rw,
            acting_user=self.alice,
        )
        self.to_project, _, _ = share_file(
            self.alice_note,
            target_project=self.project,
            permission=rw,
            acting_user=self.alice,
        )
        mine = FileService.create_file(
            self.alice, "mine.txt", content=ContentFile(b"x", name="mine.txt")
        )
        self.personal, _, _ = share_file(
            mine, target_user=self.carol, permission=rw, acting_user=self.alice
        )
        self.bobs, _, _ = share_file(
            self.bob_secret, target_user=self.carol, permission=rw, acting_user=self.bob
        )
        Notification.objects.all().delete()

    def _assert_only_alices_group_shares_revoked(self):
        remaining = set(FileShare.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {self.personal.pk, self.bobs.pk})
        self.assertIsNone(FileService.get_permission(self.carol, self.alice_note))

    def test_removing_the_user_from_the_group_revokes_their_shares_there(self):
        self.alice.groups.remove(self.team)

        self._assert_only_alices_group_shares_revoked()

    def test_removing_the_member_from_the_group_side_revokes_them_too(self):
        self.team.user_set.remove(self.alice)

        self._assert_only_alices_group_shares_revoked()

    def test_clearing_the_users_groups_revokes_them(self):
        self.alice.groups.clear()

        self._assert_only_alices_group_shares_revoked()

    def test_resetting_the_users_groups_revokes_them(self):
        self.alice.groups.set([self.friends])

        self._assert_only_alices_group_shares_revoked()

    def test_clearing_the_group_revokes_every_members_shares(self):
        self.team.user_set.clear()

        remaining = set(FileShare.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {self.personal.pk})

    def test_shares_survive_a_membership_that_stays(self):
        other = Group.objects.create(name="Other")
        self.alice.groups.add(other)
        self.alice.groups.remove(other)

        self.assertEqual(FileShare.objects.count(), 5)

    def test_each_revocation_is_recorded_and_notified_like_an_unshare(self):
        self.alice.groups.remove(self.team)

        unshared = FileEvent.objects.filter(action=FileEvent.Action.UNSHARED)
        self.assertEqual(
            sorted((e.file_id, e.actor_id) for e in unshared),
            sorted(
                [
                    (self.alice_note.pk, None),
                    (self.alice_note.pk, None),
                    (self.bob_secret.pk, None),
                ]
            ),
        )
        self.assertIn(
            {
                "shared_with_group_id": self.friends.pk,
                "shared_with_group_name": "Friends",
            },
            [e.metadata for e in unshared],
        )
        notified = Notification.objects.filter(recipient=self.carol)
        self.assertEqual(notified.count(), 3)
        self.assertTrue(all(n.actor_id == self.alice.pk for n in notified))
        self.assertFalse(Notification.objects.exclude(recipient=self.carol).exists())


class FormerMemberSharingMigrationTests(_DepartureFixture, TestCase):
    """Links and shares made by members who left before revocation existed."""

    class _SchemaEditor:
        class connection:
            alias = "default"

    def test_the_migration_revokes_a_former_members_group_sharing_only(self):
        migration = importlib.import_module(
            "workspace.files.migrations.0070_revoke_former_member_shares"
        )
        self._fixture()
        stale = create_share_link(self.alice_sub, acting_user=self.alice)
        personal = create_share_link(
            FileService.create_folder(self.alice, "Mine"), acting_user=self.alice
        )
        member = create_share_link(self.root, acting_user=self.bob)
        carol = User.objects.create_user(username="carol", password="pass123")
        stale_share, _, _ = share_file(
            self.alice_note, target_user=carol, permission="ro", acting_user=self.alice
        )
        personal_share, _, _ = share_file(
            FileService.create_file(
                self.alice, "mine.txt", content=ContentFile(b"x", name="mine.txt")
            ),
            target_user=carol,
            permission="ro",
            acting_user=self.alice,
        )
        member_share, _, _ = share_file(
            self.bob_secret, target_user=carol, permission="ro", acting_user=self.bob
        )
        # Left without the hook running, as before it existed.
        User.groups.through.objects.filter(user=self.alice, group=self.team).delete()

        migration.revoke_former_member_sharing(apps, self._SchemaEditor)

        remaining = set(FileShareLink.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {personal.pk, member.pk})
        self.assertFalse(FileShareLink.objects.filter(pk=stale.pk).exists())
        self.assertEqual(
            set(FileShare.objects.values_list("pk", flat=True)),
            {personal_share.pk, member_share.pk},
        )
        self.assertFalse(FileShare.objects.filter(pk=stale_share.pk).exists())
