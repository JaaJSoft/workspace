"""Sharing albums: who can open one, in which role, what they see in it, and
the endpoints that manage the members."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.notifications.models import Notification
from workspace.photos.actions import AlbumActionRegistry
from workspace.photos.models import Album, AlbumItem, AlbumShare, HiddenFile
from workspace.photos.queries import (
    CONTRIBUTOR,
    MANAGER,
    OWNER,
    VIEWER,
    album_files,
    album_members,
    album_roles,
    direct_share_album_ids,
    get_album_role,
    link_files,
    shared_albums,
    user_albums,
)
from workspace.photos.services.album_sharing import (
    leave_album,
    share_album,
    unshare_album,
)
from workspace.photos.services.albums import add_items, create_album
from workspace.projects.models import Project, ProjectMember

from .images import make_photo

User = get_user_model()

API = "/api/v1/photos/albums"


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


class SharingTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.carol = User.objects.create_user(username="carol", password="p")
        self.outsider = User.objects.create_user(username="outsider", password="p")
        self.photo = make_photo(self.owner, "beach.jpg", _at(14))
        self.album = create_album(self.owner, "Trip", files=[self.photo])

    def tearDown(self):
        cache.clear()

    def share(self, role=AlbumShare.Role.VIEWER, **target):
        target = target or {"user": self.bob}
        return share_album(self.album, role=role, acting_user=self.owner, **target)[0]


class AlbumRoleTests(SharingTestCase):
    def test_owner_and_outsider(self):
        self.assertEqual(get_album_role(self.owner, self.album), OWNER)
        self.assertIsNone(get_album_role(self.outsider, self.album))

    def test_a_share_grants_its_role(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)

        self.assertEqual(get_album_role(self.bob, self.album), CONTRIBUTOR)
        self.assertIn(self.album, user_albums(self.bob))
        self.assertIn(self.album, shared_albums(self.bob))
        self.assertNotIn(self.album, shared_albums(self.owner))

    def test_group_share_follows_membership(self):
        team = Group.objects.create(name="Team")
        self.share(AlbumShare.Role.VIEWER, group=team)
        self.assertIsNone(get_album_role(self.bob, self.album))

        self.bob.groups.add(team)
        self.assertEqual(get_album_role(self.bob, self.album), VIEWER)

        self.bob.groups.remove(team)
        self.assertIsNone(get_album_role(self.bob, self.album))

    def test_project_share_follows_membership(self):
        project = Project.objects.create(
            name="Website", created_by=self.carol, key="P1"
        )
        self.share(AlbumShare.Role.CONTRIBUTOR, project=project)
        self.assertIsNone(get_album_role(self.bob, self.album))

        ProjectMember.objects.create(project=project, user=self.bob)
        self.assertEqual(get_album_role(self.bob, self.album), CONTRIBUTOR)

    def test_the_highest_role_wins(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        self.share(AlbumShare.Role.VIEWER)
        self.share(AlbumShare.Role.MANAGER, group=team)

        self.assertEqual(get_album_role(self.bob, self.album), MANAGER)
        self.assertEqual(
            album_roles(self.bob, [self.album]), {self.album.uuid: MANAGER}
        )

    def test_album_roles_in_bulk(self):
        other = create_album(self.carol, "Carol's")
        share_album(
            other, role=AlbumShare.Role.VIEWER, acting_user=self.carol, user=self.bob
        )
        self.share(AlbumShare.Role.CONTRIBUTOR)
        mine = create_album(self.bob, "Mine")
        nobody = create_album(self.outsider, "Private")

        roles = album_roles(self.bob, [self.album, other, mine, nobody])

        self.assertEqual(
            roles,
            {self.album.uuid: CONTRIBUTOR, other.uuid: VIEWER, mine.uuid: OWNER},
        )

    def test_group_album_gives_its_members_the_owner_role(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        album = Album.objects.create(owner=self.owner, group=team, title="Team")

        self.assertEqual(get_album_role(self.bob, album), OWNER)
        self.assertEqual(album_roles(self.bob, [album]), {album.uuid: OWNER})
        self.assertIsNone(get_album_role(self.owner, album))

    def test_direct_shares_are_the_ones_one_can_leave(self):
        team = Group.objects.create(name="Team")
        self.carol.groups.add(team)
        self.share(AlbumShare.Role.VIEWER)
        self.share(AlbumShare.Role.VIEWER, group=team)

        self.assertEqual(
            direct_share_album_ids(self.bob, [self.album]), {self.album.uuid}
        )
        self.assertEqual(direct_share_album_ids(self.carol, [self.album]), set())

    def test_members_of_every_kind(self):
        team = Group.objects.create(name="Team")
        self.carol.groups.add(team)
        project = Project.objects.create(
            name="Website", created_by=self.carol, key="P2"
        )
        member = User.objects.create_user(username="dev", password="p")
        ProjectMember.objects.create(project=project, user=member)
        gone = User.objects.create_user(username="gone", password="p", is_active=False)
        self.share(AlbumShare.Role.VIEWER)
        self.share(AlbumShare.Role.VIEWER, group=team)
        self.share(AlbumShare.Role.VIEWER, project=project)
        self.share(AlbumShare.Role.VIEWER, user=gone)

        self.assertEqual(
            set(album_members(self.album).values_list("username", flat=True)),
            {"owner", "bob", "carol", "dev"},
        )


class AlbumVisibilityTests(SharingTestCase):
    """What a member sees: the items their contributors vouch for."""

    def test_a_member_sees_the_owners_photos_they_cannot_open_in_files(self):
        self.share()

        self.assertIsNone(FileService.get_permission(self.bob, self.photo))
        self.assertEqual(list(album_files(self.bob, self.album)), [self.photo])

    def test_an_outsider_sees_nothing(self):
        self.assertEqual(list(album_files(self.outsider, self.album)), [])

    def test_a_trashed_photo_leaves_the_album_and_comes_back_on_restore(self):
        self.share()
        File.objects.filter(pk=self.photo.pk).update(deleted_at=timezone.now())
        self.assertEqual(list(album_files(self.bob, self.album)), [])
        self.assertEqual(list(link_files(self.album)), [])

        File.objects.filter(pk=self.photo.pk).update(deleted_at=None)
        self.assertEqual(list(album_files(self.bob, self.album)), [self.photo])

    def test_a_hard_deleted_photo_takes_its_item_along(self):
        File.objects.filter(pk=self.photo.pk).delete()

        self.assertFalse(AlbumItem.objects.filter(album=self.album).exists())

    def test_a_photo_shared_with_the_owner_only_stays_theirs(self):
        # The owner may put a photo Carol shared with them in their album;
        # it is not theirs to pass on, so Bob never sees it.
        carols = make_photo(self.carol, "carol.jpg", _at(15))
        share_file(
            carols, target_user=self.owner, permission="ro", acting_user=self.carol
        )
        add_items(self.album, [carols], added_by=self.owner)
        self.share()

        self.assertEqual(set(album_files(self.owner, self.album)), {self.photo, carols})
        self.assertEqual(list(album_files(self.bob, self.album)), [self.photo])
        self.assertEqual(list(link_files(self.album)), [self.photo])

    def test_a_member_who_can_open_the_photo_sees_it_anyway(self):
        carols = make_photo(self.carol, "carol.jpg", _at(15))
        share_file(
            carols, target_user=self.owner, permission="ro", acting_user=self.carol
        )
        share_file(
            carols, target_user=self.bob, permission="ro", acting_user=self.carol
        )
        add_items(self.album, [carols], added_by=self.owner)
        self.share()

        self.assertEqual(set(album_files(self.bob, self.album)), {self.photo, carols})

    def test_a_group_photo_is_vouched_for_while_its_contributor_is_in_the_group(self):
        team = Group.objects.create(name="Team")
        self.owner.groups.add(team)
        group_photo = make_photo(self.owner, "team.jpg", _at(16))
        File.objects.filter(pk=group_photo.pk).update(group=team)
        add_items(self.album, [group_photo], added_by=self.owner)
        self.share()
        self.assertIn(group_photo, album_files(self.bob, self.album))

        self.owner.groups.remove(team)
        self.assertNotIn(group_photo, album_files(self.bob, self.album))

    def test_contributions_stay_after_their_contributor_leaves(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        bobs = make_photo(self.bob, "bob.jpg", _at(17))
        add_items(self.album, [bobs], added_by=self.bob)

        leave_album(self.album, self.bob)

        self.assertIn(bobs, album_files(self.owner, self.album))

    def test_leaving_may_take_the_contributions_along(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        bobs = make_photo(self.bob, "bob.jpg", _at(17))
        add_items(self.album, [bobs], added_by=self.bob)

        self.assertTrue(leave_album(self.album, self.bob, remove_items=True))

        self.assertNotIn(bobs, album_files(self.owner, self.album))
        self.assertIn(self.photo, album_files(self.owner, self.album))

    def test_an_item_whose_contributor_is_gone_is_vouched_for_by_nobody(self):
        self.share()
        AlbumItem.objects.filter(album=self.album).update(added_by=None)

        self.assertEqual(list(album_files(self.bob, self.album)), [])
        self.assertEqual(list(album_files(self.owner, self.album)), [self.photo])

    def test_what_a_member_hid_stays_hidden_for_them_alone(self):
        self.share()
        HiddenFile.objects.create(owner=self.owner, file=self.photo)

        self.assertEqual(list(album_files(self.owner, self.album)), [])
        self.assertEqual(list(album_files(self.bob, self.album)), [self.photo])


class RoleActionMatrixTests(SharingTestCase):
    """Which album action each role is offered: the menus and the endpoints
    both read this."""

    EXPECTED = {
        OWNER: {
            "rename",
            "edit_description",
            "change_sort",
            "add_items",
            "remove_items",
            "set_cover",
            "share",
            "download",
            "delete",
        },
        MANAGER: {
            "rename",
            "edit_description",
            "change_sort",
            "add_items",
            "remove_items",
            "set_cover",
            "share",
            "download",
            "delete",
            "leave",
        },
        CONTRIBUTOR: {"add_items", "remove_items", "download", "leave"},
        VIEWER: {"download", "leave"},
        None: set(),
    }

    def actions(self, role, *, direct=True):
        return {
            a["id"]
            for a in AlbumActionRegistry.get_available_actions(
                self.bob, self.album, role=role, direct=direct
            )
        }

    def test_each_role(self):
        for role, expected in self.EXPECTED.items():
            with self.subTest(role=role):
                self.assertEqual(self.actions(role), expected)

    def test_reorder_needs_manual_order_and_a_manager(self):
        self.album.sort_mode = Album.SortMode.MANUAL
        for role in (OWNER, MANAGER):
            self.assertIn("reorder", self.actions(role))
        for role in (CONTRIBUTOR, VIEWER):
            self.assertNotIn("reorder", self.actions(role))

    def test_a_viewer_only_downloads_while_the_album_allows_it(self):
        self.album.allow_download = False

        self.assertNotIn("download", self.actions(VIEWER))
        self.assertIn("download", self.actions(CONTRIBUTOR))

    def test_only_a_share_by_name_can_be_left(self):
        self.assertNotIn("leave", self.actions(VIEWER, direct=False))


class SharesApiTests(SharingTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def url(self, album=None):
        return f"{API}/{(album or self.album).uuid}/shares"

    def post(self, body, album=None):
        return self.client.post(self.url(album), body, content_type="application/json")

    def test_share_with_a_user_notifies_them(self):
        response = self.post({"shared_with": self.bob.pk, "role": "contributor"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["role"], "contributor")
        share = AlbumShare.objects.get(album=self.album)
        self.assertEqual((share.shared_with, share.shared_by), (self.bob, self.owner))
        notification = Notification.objects.get(recipient=self.bob)
        self.assertEqual(notification.album, self.album)
        self.assertIn('shared the album "Trip"', notification.title)
        self.assertEqual(notification.url, f"/photos/albums/{self.album.uuid}")

    def test_share_with_a_group_notifies_its_members_but_the_sharer(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        self.carol.groups.add(team)
        self.owner.groups.add(team)

        self.assertEqual(
            self.post({"group": team.pk, "role": "viewer"}).status_code, 201
        )

        self.assertEqual(
            set(Notification.objects.values_list("recipient__username", flat=True)),
            {"bob", "carol"},
        )

    def test_share_with_a_project_the_caller_can_open(self):
        project = Project.objects.create(
            name="Website", created_by=self.owner, key="P3"
        )
        ProjectMember.objects.create(project=project, user=self.owner)
        ProjectMember.objects.create(project=project, user=self.bob)
        other = Project.objects.create(name="Secret", created_by=self.carol, key="P4")

        ok = self.post({"project": str(project.uuid), "role": "viewer"})
        refused = self.post({"project": str(other.uuid), "role": "viewer"})

        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["type"], "project")
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(get_album_role(self.bob, self.album), VIEWER)

    def test_changing_a_role_notifies_without_a_second_share(self):
        self.share()

        response = self.post({"shared_with": self.bob.pk, "role": "manager"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AlbumShare.objects.get().role, "manager")
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.bob, title__contains="now manager"
            ).exists()
        )

    def test_the_same_role_again_notifies_nobody(self):
        self.share()
        Notification.objects.all().delete()

        self.assertEqual(
            self.post({"shared_with": self.bob.pk, "role": "viewer"}).status_code, 200
        )
        self.assertFalse(Notification.objects.exists())

    def test_refusals(self):
        cases = [
            ({"shared_with": self.owner.pk, "role": "viewer"}, 400),
            ({"shared_with": 999999, "role": "viewer"}, 404),
            ({"group": 999999, "role": "viewer"}, 404),
            ({"shared_with": self.bob.pk, "role": "owner"}, 400),
            ({"shared_with": self.bob.pk, "group": 1, "role": "viewer"}, 400),
            ({"role": "viewer"}, 400),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, expected)
        self.assertFalse(AlbumShare.objects.exists())

    def test_the_owner_cannot_be_made_a_member(self):
        self.client.force_login(self.bob)
        self.share(AlbumShare.Role.MANAGER)

        response = self.post({"shared_with": self.owner.pk, "role": "viewer"})

        self.assertEqual(response.status_code, 400)

    def test_list_members(self):
        team = Group.objects.create(name="Team")
        self.share(AlbumShare.Role.CONTRIBUTOR)
        self.share(AlbumShare.Role.VIEWER, group=team)

        data = self.client.get(self.url()).json()

        self.assertEqual(data["owner"]["username"], "owner")
        self.assertEqual(
            [(s["type"], s["role"]) for s in data["shares"]],
            [("user", "contributor"), ("group", "viewer")],
        )
        self.assertTrue(data["allow_download"])

    def test_a_group_album_lists_its_group_as_owner(self):
        team = Group.objects.create(name="Team")
        self.owner.groups.add(team)
        album = Album.objects.create(owner=self.owner, group=team, title="Team")

        owner = self.client.get(self.url(album)).json()["owner"]

        self.assertEqual(owner, {"type": "group", "id": team.pk, "name": "Team"})

    def test_every_member_reads_the_list_but_only_managers_change_it(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        self.client.force_login(self.bob)

        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(
            self.post({"shared_with": self.carol.pk, "role": "viewer"}).status_code, 403
        )
        self.assertEqual(
            self.client.delete(
                self.url(),
                {"shared_with": self.bob.pk},
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_a_manager_shares_further(self):
        self.share(AlbumShare.Role.MANAGER)
        self.client.force_login(self.bob)

        response = self.post({"shared_with": self.carol.pk, "role": "viewer"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(get_album_role(self.carol, self.album), VIEWER)

    def test_an_outsider_gets_a_404(self):
        self.client.force_login(self.outsider)

        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(
            self.post({"shared_with": self.carol.pk, "role": "viewer"}).status_code, 404
        )

    def test_remove_a_member_notifies_them(self):
        self.share()
        Notification.objects.all().delete()

        response = self.client.delete(
            self.url(), {"shared_with": self.bob.pk}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_album_role(self.bob, self.album))
        self.assertIn("removed you", Notification.objects.get(recipient=self.bob).title)
        again = self.client.delete(
            self.url(), {"shared_with": self.bob.pk}, content_type="application/json"
        )
        self.assertEqual(again.status_code, 404)

    def test_a_deactivated_member_is_removed_but_never_added(self):
        self.share()
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])

        response = self.client.delete(
            self.url(), {"shared_with": self.bob.pk}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(AlbumShare.objects.filter(album=self.album).exists())
        added = self.post({"shared_with": self.bob.pk, "role": "viewer"})
        self.assertEqual(added.status_code, 404)

    def test_remove_a_project(self):
        project = Project.objects.create(
            name="Website", created_by=self.owner, key="P5"
        )
        ProjectMember.objects.create(project=project, user=self.owner)
        ProjectMember.objects.create(project=project, user=self.bob)
        self.share(AlbumShare.Role.VIEWER, project=project)

        response = self.client.delete(
            self.url(), {"project": str(project.uuid)}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_album_role(self.bob, self.album))

    def test_the_first_share_starts_the_notification_clock(self):
        self.assertIsNone(self.album.notified_at)

        self.post({"shared_with": self.bob.pk, "role": "viewer"})

        self.album.refresh_from_db()
        self.assertIsNotNone(self.album.notified_at)

    def test_the_service_wants_exactly_one_target(self):
        with self.assertRaises(ValueError):
            unshare_album(self.album, acting_user=self.owner)


class LeaveApiTests(SharingTestCase):
    def test_leave_a_direct_share(self):
        self.share()
        self.client.force_login(self.bob)

        response = self.client.post(
            f"{API}/{self.album.uuid}/leave", {}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 204)
        self.assertIsNone(get_album_role(self.bob, self.album))

    def test_leave_with_the_photos_one_added(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        bobs = make_photo(self.bob, "bob.jpg", _at(17))
        add_items(self.album, [bobs], added_by=self.bob)
        self.client.force_login(self.bob)

        self.client.post(
            f"{API}/{self.album.uuid}/leave",
            {"remove_items": True},
            content_type="application/json",
        )

        self.assertFalse(AlbumItem.objects.filter(file=bobs).exists())

    def test_neither_the_owner_nor_a_group_member_can_leave(self):
        team = Group.objects.create(name="Team")
        self.carol.groups.add(team)
        self.share(AlbumShare.Role.VIEWER, group=team)
        for user in (self.owner, self.carol):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(f"{API}/{self.album.uuid}/leave")
                self.assertEqual(response.status_code, 403)


class MemberItemsApiTests(SharingTestCase):
    """Adding and removing photos as an invited member."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.bob)

    def post(self, path, files):
        return self.client.post(
            f"{API}/{self.album.uuid}/{path}",
            {"files": [str(f.uuid) for f in files]},
            content_type="application/json",
        )

    def test_a_contributor_adds_their_own_photos(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        bobs = make_photo(self.bob, "bob.jpg", _at(17))

        response = self.post("items", [bobs])

        self.assertEqual(response.json(), {"added": 1})
        self.assertEqual(AlbumItem.objects.get(file=bobs).added_by, self.bob)

    def test_a_contributor_cannot_add_a_photo_shared_with_them(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        carols = make_photo(self.carol, "carol.jpg", _at(18))
        share_file(
            carols, target_user=self.bob, permission="ro", acting_user=self.carol
        )

        self.assertEqual(self.post("items", [carols]).status_code, 400)

    def test_a_viewer_cannot_add(self):
        self.share(AlbumShare.Role.VIEWER)
        bobs = make_photo(self.bob, "bob.jpg", _at(17))

        self.assertEqual(self.post("items", [bobs]).status_code, 403)

    def test_a_contributor_removes_what_they_added_and_nothing_else(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        bobs = make_photo(self.bob, "bob.jpg", _at(17))
        add_items(self.album, [bobs], added_by=self.bob)

        self.assertEqual(self.post("items/remove", [bobs, self.photo]).status_code, 403)
        self.assertEqual(self.post("items/remove", [bobs]).json(), {"removed": 1})
        self.assertTrue(AlbumItem.objects.filter(file=self.photo).exists())

    def test_a_manager_removes_any_photo(self):
        self.share(AlbumShare.Role.MANAGER)

        self.assertEqual(self.post("items/remove", [self.photo]).json(), {"removed": 1})

    def test_a_viewer_cannot_remove(self):
        self.share(AlbumShare.Role.VIEWER)

        self.assertEqual(self.post("items/remove", [self.photo]).status_code, 403)

    def test_a_manager_edits_and_a_contributor_does_not(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        url = f"{API}/{self.album.uuid}"
        body = {"title": "Renamed"}

        refused = self.client.patch(url, body, content_type="application/json")
        self.share(AlbumShare.Role.MANAGER)
        accepted = self.client.patch(url, body, content_type="application/json")

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["role"], "manager")

    def test_the_download_setting_answers_to_the_share_action(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        url = f"{API}/{self.album.uuid}"
        body = {"allow_download": False}

        self.assertEqual(
            self.client.patch(url, body, content_type="application/json").status_code,
            403,
        )
        self.client.force_login(self.owner)
        response = self.client.patch(url, body, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.album.refresh_from_db()
        self.assertFalse(self.album.allow_download)

    def test_the_album_list_names_the_role(self):
        self.share(AlbumShare.Role.CONTRIBUTOR)
        File.objects.filter(pk=self.photo.pk).update(has_thumbnail=True)

        data = self.client.get(API).json()

        self.assertEqual(
            [(a["title"], a["role"], a["owner"]) for a in data],
            [("Trip", "contributor", "owner")],
        )
        self.assertEqual(
            data[0]["cover_url"],
            f"{API}/{self.album.uuid}/files/{self.photo.uuid}/thumbnail",
        )


class ShareNotificationPatchTests(SharingTestCase):
    def test_sharing_with_a_user_without_an_account_left_notifies_nobody(self):
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])

        with patch(
            "workspace.photos.services.album_notifications.notify_many"
        ) as notify_many:
            self.share()

        self.assertEqual(notify_many.call_args.kwargs["recipients"], [])
