from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import FileScan, FileShare
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file, unshare_file
from workspace.photos.models import Album, AlbumItem
from workspace.photos.queries import (
    OWNER,
    album_date_range,
    album_files,
    album_roles,
    album_summaries,
    get_album_role,
    reachable_album,
    user_albums,
)
from workspace.photos.services.album_cards import album_cards
from workspace.photos.services.albums import (
    POSITION_GAP,
    add_items,
    create_album,
    move_items,
    remove_items,
)

from .images import make_photo, upload

User = get_user_model()


def _at(*args):
    return datetime(*args, tzinfo=UTC)


def _order(album):
    """The file ids of *album* in manual order."""
    return list(
        AlbumItem.objects.filter(album=album)
        .order_by("position", "file_id")
        .values_list("file_id", flat=True)
    )


class AlbumItemsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.a, self.b, self.c, self.d = (
            make_photo(self.user, f"{name}.jpg", _at(2024, 7, 14 - i))
            for i, name in enumerate("abcd")
        )

    def test_create_fills_the_album_in_the_given_order(self):
        album = create_album(self.user, "Summer", files=[self.c, self.a])

        self.assertEqual(album.owner, self.user)
        self.assertIsNone(album.group)
        self.assertEqual(album.sort_mode, Album.SortMode.CAPTURE_DATE)
        self.assertEqual(_order(album), [self.c.pk, self.a.pk])
        item = AlbumItem.objects.get(album=album, file=self.c)
        self.assertEqual(item.added_by, self.user)

    def test_adding_appends_with_gaps_and_skips_what_is_there(self):
        album = create_album(self.user, "Summer", files=[self.a])

        added = add_items(album, [self.b, self.a, self.c], added_by=self.user)

        self.assertEqual(added, 2)
        self.assertEqual(_order(album), [self.a.pk, self.b.pk, self.c.pk])
        positions = list(
            AlbumItem.objects.filter(album=album)
            .order_by("position")
            .values_list("position", flat=True)
        )
        self.assertEqual(positions, [POSITION_GAP, 2 * POSITION_GAP, 3 * POSITION_GAP])

    def test_adding_writes_the_new_rows_only(self):
        album = create_album(self.user, "Summer", files=[self.a, self.b])
        before = dict(
            AlbumItem.objects.filter(album=album).values_list("file_id", "position")
        )

        add_items(album, [self.c], added_by=self.user)

        after = dict(
            AlbumItem.objects.filter(album=album).values_list("file_id", "position")
        )
        self.assertEqual({k: after[k] for k in before}, before)

    def test_removing_forgets_a_cover_among_the_removed(self):
        album = create_album(self.user, "Summer", files=[self.a, self.b])
        album.cover = self.a
        album.save(update_fields=["cover"])

        removed = remove_items(album, [self.a.pk, self.c.pk])

        self.assertEqual(removed, 1)
        album.refresh_from_db()
        self.assertIsNone(album.cover)
        self.assertEqual(_order(album), [self.b.pk])

    def test_a_hard_deleted_file_leaves_every_album(self):
        album = create_album(self.user, "Summer", files=[self.a, self.b])

        FileService.hard_delete(self.a, acting_user=self.user)

        self.assertEqual(_order(album), [self.b.pk])


class MoveItemsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.photos = [
            make_photo(self.user, f"{i}.jpg", _at(2024, 7, 1 + i)) for i in range(5)
        ]
        self.album = create_album(self.user, "Trip", files=self.photos)
        self.ids = [p.pk for p in self.photos]

    def test_after_an_item(self):
        a, b, c, d, e = self.ids

        move_items(self.album, [e, a], after=b)

        self.assertEqual(_order(self.album), [b, e, a, c, d])

    def test_before_an_item(self):
        a, b, c, d, e = self.ids

        move_items(self.album, [d], before=a)

        self.assertEqual(_order(self.album), [d, a, b, c, e])

    def test_to_the_end(self):
        a, b, c, d, e = self.ids

        move_items(self.album, [a, b])

        self.assertEqual(_order(self.album), [c, d, e, a, b])

    def test_after_the_last_item(self):
        a, b, c, d, e = self.ids

        move_items(self.album, [b], after=e)

        self.assertEqual(_order(self.album), [a, c, d, e, b])

    def test_only_the_moved_rows_are_written(self):
        a, b, c, d, e = self.ids
        before = dict(
            AlbumItem.objects.filter(album=self.album).values_list(
                "file_id", "position"
            )
        )

        move_items(self.album, [e], after=a)

        after = dict(
            AlbumItem.objects.filter(album=self.album).values_list(
                "file_id", "position"
            )
        )
        self.assertEqual(
            {k for k in before if before[k] != after[k]},
            {e},
        )

    def test_neighbours_with_no_room_left_renumber_the_album_once(self):
        a, b, c, d, e = self.ids
        AlbumItem.objects.filter(album=self.album, file_id=b).update(
            position=POSITION_GAP + 1
        )

        move_items(self.album, [d, e], after=a)

        self.assertEqual(_order(self.album), [a, d, e, b, c])

    def test_tied_positions_left_by_concurrent_adds_still_order(self):
        AlbumItem.objects.filter(album=self.album).update(position=POSITION_GAP)
        tied = sorted(self.ids)

        move_items(self.album, [tied[4]], after=tied[0])

        self.assertEqual(
            _order(self.album), [tied[0], tied[4], tied[1], tied[2], tied[3]]
        )

    def test_the_anchor_cannot_be_moved(self):
        a, b, *_ = self.ids

        with self.assertRaises(ValueError):
            move_items(self.album, [a, b], after=b)

    def test_only_items_of_the_album_move(self):
        outsider = make_photo(self.user, "x.jpg", _at(2024, 8, 1))

        with self.assertRaises(ValueError):
            move_items(self.album, [outsider.pk])
        with self.assertRaises(ValueError):
            move_items(self.album, [self.ids[0]], before=outsider.pk)


class AlbumAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.family = Group.objects.create(name="Family")
        self.user.groups.add(self.family)
        self.mine = Album.objects.create(owner=self.user, title="Mine")
        self.bobs = Album.objects.create(owner=self.bob, title="Bob's")
        self.family_album = Album.objects.create(
            owner=self.bob, group=self.family, title="Family"
        )
        self.strangers_album = Album.objects.create(
            owner=self.bob,
            group=Group.objects.create(name="Strangers"),
            title="Strangers",
        )

    def test_personal_albums_and_the_users_groups_albums(self):
        self.assertEqual(set(user_albums(self.user)), {self.mine, self.family_album})

    def test_roles(self):
        self.assertEqual(get_album_role(self.user, self.mine), OWNER)
        self.assertEqual(get_album_role(self.user, self.family_album), OWNER)
        self.assertIsNone(get_album_role(self.user, self.bobs))
        self.assertIsNone(get_album_role(self.user, self.strangers_album))
        self.assertEqual(
            album_roles(
                self.user,
                [self.mine, self.bobs, self.family_album, self.strangers_album],
            ),
            {self.mine.uuid: OWNER, self.family_album.uuid: OWNER},
        )

    def test_a_personal_album_is_not_its_owners_group_album(self):
        # Owning a personal album says nothing about the group's members.
        self.bob.groups.add(self.family)

        self.assertIsNone(get_album_role(self.user, self.bobs))

    def test_reachable_album(self):
        self.assertEqual(reachable_album(self.user, self.mine.uuid), self.mine)
        self.assertIsNone(reachable_album(self.user, self.bobs.uuid))


class AlbumFilesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.carol = User.objects.create_user(username="carol", password="p")
        self.mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14))
        self.shared = make_photo(self.carol, "shared.jpg", _at(2024, 7, 12))
        share_file(
            self.shared,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.carol,
        )
        self.album = create_album(self.user, "Summer", files=[self.mine, self.shared])

    def test_the_items_the_viewer_can_open(self):
        self.assertEqual(
            set(album_files(self.user, self.album)), {self.mine, self.shared}
        )

    def test_nothing_for_someone_who_cannot_open_the_album(self):
        self.assertEqual(list(album_files(self.carol, self.album)), [])

    def test_a_trashed_file_drops_out_and_comes_back_on_restore(self):
        item = AlbumItem.objects.get(album=self.album, file=self.mine)

        FileService.soft_delete(self.mine, acting_user=self.user)
        self.assertEqual(list(album_files(self.user, self.album)), [self.shared])

        self.mine.refresh_from_db()
        FileService.restore(self.mine, acting_user=self.user)
        self.assertIn(self.mine, album_files(self.user, self.album))
        self.assertEqual(AlbumItem.objects.get(pk=item.pk).position, item.position)

    def test_a_revoked_share_takes_the_photo_out_instead_of_leaking_it(self):
        unshare_file(self.shared, target_user=self.user, acting_user=self.carol)

        self.assertEqual(list(album_files(self.user, self.album)), [self.mine])
        # The item stays: it is the viewer's reach that changed.
        self.assertTrue(
            AlbumItem.objects.filter(album=self.album, file=self.shared).exists()
        )

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_file_is_hidden(self):
        FileScan.objects.create(
            file=self.mine,
            status=FileScan.Status.INFECTED,
            content_hash=self.mine.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertEqual(list(album_files(self.user, self.album)), [self.shared])

    def test_date_range(self):
        undated = make_photo(self.user, "scan.png", None)
        add_items(self.album, [undated], added_by=self.user)

        self.assertEqual(
            album_date_range(album_files(self.user, self.album)),
            (_at(2024, 7, 12), _at(2024, 7, 14)),
        )


class AlbumSummariesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.old = make_photo(self.user, "old.jpg", _at(2024, 7, 1))
        self.new = make_photo(self.user, "new.jpg", _at(2024, 7, 2))
        self.album = create_album(self.user, "Summer", files=[self.old])
        # Added later, whatever its capture date: the fallback cover.
        AlbumItem.objects.create(
            album=self.album,
            file=self.new,
            added_by=self.user,
            added_at=timezone.now() + timezone.timedelta(minutes=1),
            position=10 * POSITION_GAP,
        )

    def _summary(self, album=None):
        album = album or self.album
        album.refresh_from_db()
        return album_summaries(self.user, [album])[album.uuid]

    def test_count_and_fallback_cover_is_the_last_added_visible_item(self):
        self.assertEqual(self._summary(), {"count": 2, "cover_id": self.new.pk})

    def test_the_chosen_cover(self):
        self.album.cover = self.old
        self.album.save(update_fields=["cover"])

        self.assertEqual(self._summary()["cover_id"], self.old.pk)

    def test_a_trashed_cover_falls_back(self):
        self.album.cover = self.new
        self.album.save(update_fields=["cover"])

        FileService.soft_delete(self.new, acting_user=self.user)

        self.assertEqual(self._summary(), {"count": 1, "cover_id": self.old.pk})

    def test_a_cover_that_is_not_an_item_falls_back(self):
        elsewhere = make_photo(self.user, "elsewhere.jpg", _at(2024, 7, 3))
        self.album.cover = elsewhere
        self.album.save(update_fields=["cover"])

        self.assertEqual(self._summary()["cover_id"], self.new.pk)

    def test_an_album_showing_nothing(self):
        empty = Album.objects.create(owner=self.user, title="Empty")

        self.assertEqual(self._summary(empty), {"count": 0, "cover_id": None})

    def test_a_file_not_analyzed_yet_is_not_counted(self):
        add_items(self.album, [upload(self.user, "fresh.jpg")], added_by=self.user)

        self.assertEqual(self._summary()["count"], 2)

    def test_cards_query_count_does_not_grow_with_the_albums(self):
        for i in range(3):
            create_album(self.user, f"Album {i}", files=[self.old, self.new])
        self.album.cover = self.old
        self.album.save(update_fields=["cover"])
        albums = list(Album.objects.filter(owner=self.user))

        # The project reach of the file access helper, the counts, the chosen
        # covers, the fallback covers and the cover files.
        with self.assertNumQueries(5):
            cards = album_cards(self.user, albums)

        self.assertEqual(len(cards), 4)
        self.assertTrue(all(card.cover is not None for card in cards))
