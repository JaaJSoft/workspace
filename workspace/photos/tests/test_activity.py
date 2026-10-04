"""Album activity in the feed: albums shared and photos added."""

from datetime import UTC, date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.core.activity_registry import activity_registry
from workspace.files.models import File
from workspace.files.services.sharing import share_file
from workspace.photos.activity import PhotosActivityProvider
from workspace.photos.models import AlbumItem
from workspace.photos.services.album_sharing import share_album
from workspace.photos.services.albums import add_items, create_album

from .images import make_photo

User = get_user_model()


def _at(day):
    return datetime(2024, 7, day, 12, tzinfo=UTC)


class PhotosActivityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.outsider = User.objects.create_user(username="outsider", password="p")
        self.album = create_album(
            self.owner,
            "Trip",
            files=[make_photo(self.owner, f"{i}.jpg", _at(14)) for i in range(3)],
        )
        share_album(
            self.album, role="contributor", acting_user=self.owner, user=self.bob
        )
        self.provider = PhotosActivityProvider()

    def tearDown(self):
        cache.clear()

    def test_registered(self):
        self.assertIn(
            "photos", [info.slug for info in activity_registry.get_all().values()]
        )

    def test_a_share_and_a_burst_of_additions_are_two_events(self):
        events = self.provider.get_recent_events(self.owner.pk)

        self.assertEqual(
            [(e["label"], e["description"]) for e in events],
            [("Album shared", "Trip"), ("Photos added", "3 photos to Trip")],
        )
        self.assertEqual(events[0]["actor"]["username"], "owner")
        self.assertEqual(events[0]["url"], f"/photos/albums/{self.album.uuid}")

    def test_another_day_is_another_event(self):
        bobs = make_photo(self.bob, "bob.jpg", _at(15))
        add_items(self.album, [bobs], added_by=self.bob)
        AlbumItem.objects.filter(file=bobs).update(
            added_at=timezone.now() + timedelta(days=1)
        )

        events = self.provider.get_recent_events(None, viewer_id=self.owner.pk)

        self.assertEqual(events[0]["description"], "1 photo to Trip")
        self.assertEqual(events[0]["actor"]["username"], "bob")

    def test_photos_nobody_else_may_see_are_not_counted(self):
        stranger = User.objects.create_user(username="stranger", password="p")
        theirs = make_photo(stranger, "theirs.jpg", _at(16))
        share_file(
            theirs, target_user=self.owner, permission="ro", acting_user=stranger
        )
        add_items(self.album, [theirs], added_by=self.owner)
        File.objects.filter(pk=self.album.items.first().file_id).update(
            deleted_at=timezone.now()
        )

        events = self.provider.get_recent_events(self.owner.pk, viewer_id=self.bob.pk)

        self.assertEqual(events[1]["description"], "2 photos to Trip")

    def test_limit_offset_and_excluded_actor(self):
        events = self.provider.get_recent_events(self.owner.pk, limit=1, offset=1)
        self.assertEqual([e["label"] for e in events], ["Photos added"])

        self.assertEqual(
            self.provider.get_recent_events(None, exclude_actor_id=self.owner.pk), []
        )

    def test_someone_else_only_sees_the_albums_they_can_open(self):
        self.assertEqual(
            self.provider.get_recent_events(self.owner.pk, viewer_id=self.outsider.pk),
            [],
        )
        self.assertEqual(
            len(self.provider.get_recent_events(self.owner.pk, viewer_id=self.bob.pk)),
            2,
        )

    def test_daily_counts(self):
        today = timezone.localdate()
        counts = self.provider.get_daily_counts(
            self.owner.pk, today - timedelta(days=1), today + timedelta(days=1)
        )

        self.assertEqual(sum(counts.values()), 2)
        self.assertEqual(
            self.provider.get_daily_counts(
                self.owner.pk,
                date(2000, 1, 1),
                date(2000, 1, 2),
            ),
            {},
        )
        self.assertEqual(
            self.provider.get_daily_counts(
                self.owner.pk, today, today, viewer_id=self.outsider.pk
            ),
            {},
        )

    def test_stats(self):
        self.assertEqual(self.provider.get_stats(self.owner.pk), {"total_albums": 1})
        self.assertEqual(
            self.provider.get_stats(self.owner.pk, viewer_id=self.outsider.pk),
            {"total_albums": 0},
        )
        self.assertEqual(self.provider.get_stats(None), {"total_albums": 1})
