"""Additions to a shared album are announced once a burst is over, one
notification per contributor, merged while unread."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.services.sharing import share_file
from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import mark_source_read
from workspace.photos.models import Album, AlbumItem, AlbumShare
from workspace.photos.services.album_notifications import (
    ADDITIONS_STREAM,
    additions_cache_key,
    announce_additions,
    schedule_additions_notification,
)
from workspace.photos.services.album_sharing import share_album
from workspace.photos.services.albums import add_items, create_album

from .images import make_photo

User = get_user_model()

TASK = "workspace.photos.tasks.notify_album_additions.apply_async"


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


class AdditionsTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.carol = User.objects.create_user(username="carol", password="p")
        self.album = create_album(self.owner, "Trip")
        for user, role in ((self.bob, "contributor"), (self.carol, "viewer")):
            share_album(self.album, role=role, acting_user=self.owner, user=user)
        Notification.objects.all().delete()

    def tearDown(self):
        cache.clear()

    def add(self, user, *names, day=14):
        photos = [make_photo(user, name, _at(day)) for name in names]
        add_items(self.album, photos, added_by=user)
        return photos

    def additions(self, user):
        return Notification.objects.filter(recipient=user, stream=ADDITIONS_STREAM)


class ScheduleTests(AdditionsTestCase):
    def test_one_task_per_burst(self):
        with patch(TASK) as apply_async:
            schedule_additions_notification(self.album)
            schedule_additions_notification(self.album)

        apply_async.assert_called_once_with(args=[str(self.album.uuid)], countdown=60)

    @override_settings(PHOTOS_ALBUM_NOTIFY_WINDOW_SECONDS=5)
    def test_the_window_is_a_setting(self):
        with patch(TASK) as apply_async:
            schedule_additions_notification(self.album)

        self.assertEqual(apply_async.call_args.kwargs["countdown"], 5)

    def test_an_album_nobody_else_opens_has_nobody_to_tell(self):
        private = create_album(self.owner, "Private")

        with patch(TASK) as apply_async:
            schedule_additions_notification(private)

        apply_async.assert_not_called()

    def test_a_group_album_tells_its_group(self):
        team = Group.objects.create(name="Team")
        album = Album.objects.create(owner=self.owner, group=team, title="Team")

        with patch(TASK) as apply_async:
            schedule_additions_notification(album)

        apply_async.assert_called_once()

    def test_a_broker_failure_frees_the_election(self):
        with patch(TASK, side_effect=RuntimeError("down")):
            schedule_additions_notification(self.album)

        self.assertIsNone(cache.get(additions_cache_key(self.album.uuid)))

    def test_adding_elects_once_the_transaction_commits(self):
        bobs = make_photo(self.bob, "bob.jpg", _at(14))

        with patch(TASK) as apply_async, self.captureOnCommitCallbacks(execute=True):
            add_items(self.album, [bobs], added_by=self.bob)

        apply_async.assert_called_once()


class AnnounceTests(AdditionsTestCase):
    def test_each_contributor_is_announced_to_the_other_members(self):
        self.add(self.bob, "a.jpg", "b.jpg")
        self.add(self.owner, "c.jpg")

        announce_additions(self.album.uuid)

        self.assertEqual(
            list(self.additions(self.carol).values_list("title", flat=True)),
            ['bob and owner added 3 photos to "Trip"'],
        )
        self.assertEqual(
            list(self.additions(self.owner).values_list("title", flat=True)),
            ['bob added 2 photos to "Trip"'],
        )
        self.assertEqual(
            list(self.additions(self.bob).values_list("title", flat=True)),
            ['owner added 1 photo to "Trip"'],
        )
        notification = self.additions(self.carol).first()
        self.assertEqual(notification.album, self.album)
        self.assertEqual(notification.url, f"/photos/albums/{self.album.uuid}")

    def test_three_contributors_or_more_are_counted(self):
        dave = User.objects.create_user(username="dave", password="p")
        share_album(self.album, role="contributor", acting_user=self.owner, user=dave)
        self.add(self.bob, "a.jpg")
        self.add(self.owner, "b.jpg")
        self.add(dave, "c.jpg")

        announce_additions(self.album.uuid)

        self.assertEqual(
            self.additions(self.carol).get().title,
            'bob and 2 others added 3 photos to "Trip"',
        )

    def test_what_was_announced_is_not_announced_again(self):
        self.add(self.bob, "a.jpg")
        announce_additions(self.album.uuid)
        Notification.objects.all().delete()

        announce_additions(self.album.uuid)

        self.assertFalse(Notification.objects.exists())

    def test_an_unread_announcement_takes_the_next_burst(self):
        self.add(self.bob, "a.jpg")
        announce_additions(self.album.uuid)
        self.add(self.bob, "b.jpg", "c.jpg", day=15)
        announce_additions(self.album.uuid)

        notification = self.additions(self.carol).get()
        self.assertEqual(notification.title, 'bob added 2 photos to "Trip"')

    def test_a_read_announcement_is_followed_by_a_new_one(self):
        self.add(self.bob, "a.jpg")
        announce_additions(self.album.uuid)
        mark_source_read(self.carol, self.album)
        self.add(self.bob, "b.jpg", day=15)
        announce_additions(self.album.uuid)

        self.assertEqual(self.additions(self.carol).count(), 2)

    def test_photos_added_before_the_first_share_are_never_announced(self):
        private = create_album(self.owner, "Later")
        add_items(
            private, [make_photo(self.owner, "old.jpg", _at(10))], added_by=self.owner
        )
        share_album(private, role="viewer", acting_user=self.owner, user=self.carol)
        Notification.objects.all().delete()

        announce_additions(private.uuid)

        self.assertFalse(Notification.objects.exists())

    def test_a_photo_nobody_else_may_see_is_not_announced(self):
        stranger = User.objects.create_user(username="stranger", password="p")
        theirs = make_photo(stranger, "theirs.jpg", _at(14))
        share_file(
            theirs, target_user=self.owner, permission="ro", acting_user=stranger
        )
        add_items(self.album, [theirs], added_by=self.owner)

        announce_additions(self.album.uuid)

        self.assertFalse(Notification.objects.exists())

    def test_a_deleted_album_frees_the_election(self):
        cache.set(additions_cache_key(self.album.uuid), 1)
        uuid = self.album.uuid
        self.album.delete()

        announce_additions(uuid)

        self.assertIsNone(cache.get(additions_cache_key(uuid)))

    def test_an_add_landing_while_it_ran_elects_the_next_task(self):
        photos = self.add(self.bob, "a.jpg")
        AlbumItem.objects.filter(file=photos[0]).update(
            added_at=timezone.now() + timedelta(minutes=5)
        )

        with patch(TASK) as apply_async:
            announce_additions(self.album.uuid)

        apply_async.assert_called_once()
        self.assertFalse(Notification.objects.exists())

    def test_a_lone_member_hears_nothing_of_their_own_photos(self):
        AlbumShare.objects.filter(album=self.album).delete()
        team = Group.objects.create(name="Solo")
        share_album(self.album, role="viewer", acting_user=self.owner, group=team)
        Notification.objects.all().delete()
        self.add(self.owner, "a.jpg")

        announce_additions(self.album.uuid)

        self.assertFalse(Notification.objects.exists())

    def test_the_task_runs_the_announcement(self):
        from workspace.photos.tasks import notify_album_additions

        self.add(self.bob, "a.jpg")

        notify_album_additions(str(self.album.uuid))

        self.assertTrue(self.additions(self.carol).exists())


class OpeningTheAlbumReadsItsNotificationsTests(AdditionsTestCase):
    def test_opening_the_album_page(self):
        self.add(self.bob, "a.jpg")
        announce_additions(self.album.uuid)
        self.client.force_login(self.carol)

        self.client.get(f"/photos/albums/{self.album.uuid}")

        self.assertFalse(
            Notification.objects.filter(
                recipient=self.carol, read_at__isnull=True
            ).exists()
        )
