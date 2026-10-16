"""A member who loses the right to share an album loses the links and the
shares they made on it."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.notifications.models import Notification
from workspace.photos.models import Album, AlbumItem, AlbumLink, AlbumShare
from workspace.photos.queries import get_album_role
from workspace.photos.services.album_revocation import revoke_lapsed_sharing
from workspace.photos.services.album_sharing import (
    create_link,
    leave_album,
    share_album,
    unshare_album,
)
from workspace.photos.services.albums import add_items, create_album
from workspace.projects.models import Project, ProjectMember
from workspace.projects.services.members import remove_member

from .images import make_photo

User = get_user_model()

MANAGER = AlbumShare.Role.MANAGER
VIEWER = AlbumShare.Role.VIEWER


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


class RevocationTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.carol = User.objects.create_user(username="carol", password="p")
        self.photo = make_photo(self.owner, "beach.jpg", _at(14))
        self.album = create_album(self.owner, "Trip", files=[self.photo])

    def tearDown(self):
        cache.clear()

    def share(self, role=MANAGER, *, by=None, **target):
        target = target or {"user": self.bob}
        return share_album(
            self.album, role=role, acting_user=by or self.owner, **target
        )[0]

    def link(self, user, album=None):
        return create_link(album or self.album, acting_user=user, allow_download=True)

    def assertRevoked(self, link):
        self.assertFalse(AlbumLink.objects.filter(pk=link.pk).exists())
        self.assertEqual(
            self.client.get(f"/photos/shared/{link.token}").status_code, 404
        )

    def assertKept(self, link):
        self.assertTrue(AlbumLink.objects.filter(pk=link.pk).exists())
        self.assertEqual(
            self.client.get(f"/photos/shared/{link.token}").status_code, 200
        )


class LosingAShareTests(RevocationTestCase):
    def test_an_unshared_manager_loses_their_links_and_shares(self):
        self.share()
        bobs_link = self.link(self.bob)
        owners_link = self.link(self.owner)
        self.share(VIEWER, by=self.bob, user=self.carol)
        Notification.objects.all().delete()

        unshare_album(self.album, acting_user=self.owner, user=self.bob)

        self.assertRevoked(bobs_link)
        self.assertKept(owners_link)
        self.assertFalse(AlbumShare.objects.filter(album=self.album).exists())
        self.assertIsNone(get_album_role(self.carol, self.album))
        self.assertIn(
            "no longer shared with you",
            Notification.objects.get(recipient=self.carol).title,
        )

    def test_photos_added_later_never_reach_a_removed_managers_link(self):
        self.share()
        link = self.link(self.bob)
        unshare_album(self.album, acting_user=self.owner, user=self.bob)
        add_items(
            self.album,
            [make_photo(self.owner, "later.jpg", _at(16))],
            added_by=self.owner,
        )

        response = self.client.get(f"/photos/shared/{link.token}")

        self.assertEqual(response.status_code, 404)
        api = self.client.get(f"/api/v1/photos/shared/{link.token}/download")
        self.assertEqual(api.status_code, 404)

    def test_leaving_revokes_what_one_handed_out(self):
        self.share()
        link = self.link(self.bob)
        self.share(VIEWER, by=self.bob, user=self.carol)

        leave_album(self.album, self.bob)

        self.assertRevoked(link)
        self.assertIsNone(get_album_role(self.carol, self.album))

    def test_a_manager_made_viewer_loses_their_links(self):
        self.share()
        link = self.link(self.bob)

        self.share(VIEWER)

        self.assertRevoked(link)

    def test_revocation_follows_the_chain_of_shares(self):
        self.share()
        self.share(MANAGER, by=self.bob, user=self.carol)
        carols_link = self.link(self.carol)

        unshare_album(self.album, acting_user=self.owner, user=self.bob)

        self.assertRevoked(carols_link)

    def test_another_share_still_letting_them_share_keeps_the_links(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        self.share()
        self.share(MANAGER, group=team)
        link = self.link(self.bob)

        unshare_album(self.album, acting_user=self.owner, user=self.bob)

        self.assertKept(link)

    def test_unsharing_a_viewer_leaves_the_managers_links(self):
        self.share()
        link = self.link(self.bob)
        self.share(VIEWER, user=self.carol)

        unshare_album(self.album, acting_user=self.owner, user=self.carol)

        self.assertKept(link)

    def test_the_service_wants_albums_or_users(self):
        with self.assertRaises(ValueError):
            revoke_lapsed_sharing()


class LosingAGroupTests(RevocationTestCase):
    def setUp(self):
        super().setUp()
        self.team = Group.objects.create(name="Team")
        self.owner.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.group_album = Album.objects.create(
            owner=self.owner, group=self.team, title="Team album"
        )
        AlbumItem.objects.create(
            album=self.group_album, file=self.photo, added_by=self.owner, position=1
        )

    def test_every_way_out_of_a_group_album_revokes_the_link(self):
        ways_out = {
            "user.groups.remove": lambda: self.bob.groups.remove(self.team),
            "group.user_set.remove": lambda: self.team.user_set.remove(self.bob),
            "user.groups.clear": lambda: self.bob.groups.clear(),
        }
        for name, leave in ways_out.items():
            with self.subTest(name):
                self.bob.groups.add(self.team)
                link = self.link(self.bob, self.group_album)
                share_album(
                    self.group_album, role=VIEWER, acting_user=self.bob, user=self.carol
                )

                leave()

                self.assertRevoked(link)
                self.assertIsNone(get_album_role(self.carol, self.group_album))

    def test_clearing_a_group_revokes_every_former_members_link(self):
        bobs = self.link(self.bob, self.group_album)
        owners = self.link(self.owner, self.group_album)

        self.team.user_set.clear()

        self.assertRevoked(bobs)
        self.assertRevoked(owners)

    def test_leaving_a_group_the_album_is_shared_with(self):
        self.share(MANAGER, group=self.team)
        link = self.link(self.bob)

        self.bob.groups.remove(self.team)

        self.assertRevoked(link)
        self.assertKept(self.link(self.owner))

    def test_the_members_who_stay_keep_their_links(self):
        link = self.link(self.owner, self.group_album)

        self.bob.groups.remove(self.team)

        self.assertKept(link)


class LosingAProjectTests(RevocationTestCase):
    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(
            name="Website", created_by=self.owner, key="P5"
        )
        ProjectMember.objects.create(
            project=self.project, user=self.owner, role=ProjectMember.Role.ADMIN
        )

    def test_a_member_removed_from_the_project(self):
        member = ProjectMember.objects.create(project=self.project, user=self.bob)
        self.share(MANAGER, project=self.project)
        link = self.link(self.bob)

        remove_member(member)

        self.assertRevoked(link)

    def test_a_group_taken_off_the_project(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        self.project.groups.add(team)
        self.share(MANAGER, project=self.project)
        link = self.link(self.bob)

        self.project.groups.remove(team)

        self.assertRevoked(link)

    def test_clearing_the_projects_groups(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        self.project.groups.add(team)
        self.share(MANAGER, project=self.project)
        link = self.link(self.bob)

        self.project.groups.clear()

        self.assertRevoked(link)


class LinkCheckTests(RevocationTestCase):
    """A link is refused once its maker may no longer share the album, even
    when no hook revoked it (a group or a project deleted outright)."""

    def test_a_link_whose_maker_lost_the_role_without_a_hook(self):
        self.share()
        link = self.link(self.bob)
        AlbumShare.objects.filter(album=self.album).delete()

        self.assertEqual(
            self.client.get(f"/photos/shared/{link.token}").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/api/v1/photos/shared/{link.token}/download").status_code,
            404,
        )
        verify = self.client.post(
            f"/api/v1/photos/shared/{link.token}/verify",
            {"password": "x"},
            content_type="application/json",
        )
        self.assertEqual(verify.status_code, 404)

    def test_a_deleted_project_takes_its_managers_links_out_of_service(self):
        project = Project.objects.create(
            name="Website", created_by=self.owner, key="P6"
        )
        ProjectMember.objects.create(project=project, user=self.bob)
        self.share(MANAGER, project=project)
        link = self.link(self.bob)

        project.delete()

        self.assertEqual(
            self.client.get(f"/photos/shared/{link.token}").status_code, 404
        )
