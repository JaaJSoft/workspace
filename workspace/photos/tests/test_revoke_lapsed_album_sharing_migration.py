"""The data migration that revokes the album links and shares left behind by
people who lost the right to share the album."""

import importlib
from datetime import UTC, datetime

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.common.tests.migrations import schema_editor_stub
from workspace.photos.models import Album, AlbumLink, AlbumShare
from workspace.photos.services.album_sharing import create_link, share_album
from workspace.photos.services.albums import create_album
from workspace.projects.models import Project, ProjectMember

from .images import make_photo

migration = importlib.import_module(
    "workspace.photos.migrations.0021_revoke_lapsed_album_sharing"
)

User = get_user_model()
Membership = User.groups.through

MANAGER = AlbumShare.Role.MANAGER
VIEWER = AlbumShare.Role.VIEWER


class RevokeLapsedAlbumSharingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.carol = User.objects.create_user(username="carol", password="p")
        self.dave = User.objects.create_user(username="dave", password="p")
        photo = make_photo(self.owner, "a.jpg", datetime(2024, 7, 14, tzinfo=UTC))
        self.album = create_album(self.owner, "Trip", files=[photo])

    def tearDown(self):
        cache.clear()

    def share(self, role, *, by, **target):
        return share_album(self.album, role=role, acting_user=by, **target)[0]

    def run_migration(self):
        migration.revoke_lapsed_album_sharing(apps, schema_editor_stub())

    def test_what_a_removed_manager_left_behind_goes_down_the_chain(self):
        bobs_share = self.share(MANAGER, by=self.owner, user=self.bob)
        create_link(self.album, acting_user=self.bob)
        self.share(MANAGER, by=self.bob, user=self.carol)
        create_link(self.album, acting_user=self.carol)
        self.share(VIEWER, by=self.carol, user=self.dave)
        owners_link = create_link(self.album, acting_user=self.owner)
        # Removed before the rule: the row went, nothing it handed out did.
        AlbumShare.objects.filter(pk=bobs_share.pk).delete()

        self.run_migration()

        self.assertEqual(list(AlbumLink.objects.all()), [owners_link])
        self.assertFalse(AlbumShare.objects.exists())

    def test_a_former_member_of_a_group_album(self):
        team = Group.objects.create(name="Team")
        self.owner.groups.add(team)
        self.bob.groups.add(team)
        group_album = Album.objects.create(owner=self.owner, group=team, title="Team")
        bobs_link = create_link(group_album, acting_user=self.bob)
        owners_link = create_link(group_album, acting_user=self.owner)
        Membership.objects.filter(user=self.bob).delete()

        self.run_migration()

        self.assertFalse(AlbumLink.objects.filter(pk=bobs_link.pk).exists())
        self.assertTrue(AlbumLink.objects.filter(pk=owners_link.pk).exists())

    def test_managers_through_a_group_or_a_project_keep_their_links(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        self.share(MANAGER, by=self.owner, group=team)
        project = Project.objects.create(name="Site", created_by=self.owner, key="P1")
        ProjectMember.objects.create(project=project, user=self.carol)
        self.share(MANAGER, by=self.owner, project=project)
        via_project_group = Group.objects.create(name="Site team")
        self.dave.groups.add(via_project_group)
        project.groups.add(via_project_group)
        kept = [
            create_link(self.album, acting_user=user)
            for user in (self.owner, self.bob, self.carol, self.dave)
        ]

        self.run_migration()

        self.assertEqual(set(AlbumLink.objects.all()), set(kept))
        self.assertEqual(AlbumShare.objects.count(), 2)

    def test_a_member_who_left_the_project(self):
        project = Project.objects.create(name="Site", created_by=self.owner, key="P2")
        ProjectMember.objects.create(project=project, user=self.carol)
        self.share(MANAGER, by=self.owner, project=project)
        link = create_link(self.album, acting_user=self.carol)
        ProjectMember.objects.filter(user=self.carol).update(left_at=timezone.now())

        self.run_migration()

        self.assertFalse(AlbumLink.objects.filter(pk=link.pk).exists())

    def test_a_viewer_never_shares(self):
        self.share(VIEWER, by=self.owner, user=self.bob)
        link = create_link(self.album, acting_user=self.bob)

        self.run_migration()

        self.assertFalse(AlbumLink.objects.filter(pk=link.pk).exists())
