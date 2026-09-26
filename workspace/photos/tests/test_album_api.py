from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import FileShare
from workspace.files.services.sharing import share_file
from workspace.photos.models import Album, AlbumItem
from workspace.photos.services.albums import create_album

from .images import make_photo

User = get_user_model()

API = "/api/v1/photos/albums"


def _at(*args):
    return datetime(*args, tzinfo=UTC)


class AlbumApiTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.client.force_login(self.user)
        self.a = make_photo(self.user, "a.jpg", _at(2024, 7, 14))
        self.b = make_photo(self.user, "b.jpg", _at(2024, 7, 15))
        self.c = make_photo(self.user, "c.jpg", _at(2024, 7, 16))

    def tearDown(self):
        cache.clear()

    def _order(self, album):
        return list(
            AlbumItem.objects.filter(album=album)
            .order_by("position", "file_id")
            .values_list("file_id", flat=True)
        )


class AlbumListCreateTests(AlbumApiTestCase):
    def test_login_required(self):
        self.client.logout()

        self.assertIn(self.client.get(API).status_code, (401, 403))

    def test_list_the_albums_the_user_can_open_by_title(self):
        create_album(self.user, "zoo", files=[self.a])
        create_album(self.user, "Beach")
        create_album(self.bob, "Bob's")

        data = self.client.get(API).json()

        self.assertEqual([a["title"] for a in data], ["Beach", "zoo"])
        zoo = data[1]
        self.assertEqual(zoo["count"], 1)
        self.assertEqual(zoo["cover"], str(self.a.uuid))
        self.assertEqual(zoo["url"], f"/photos/albums/{zoo['uuid']}")
        self.assertEqual(zoo["sort_mode"], "capture_date")

    def test_create_with_photos(self):
        response = self.client.post(
            API,
            {"title": "  Summer 2024 ", "files": [str(self.b.uuid), str(self.a.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        album = Album.objects.get(uuid=response.json()["uuid"])
        self.assertEqual(album.title, "Summer 2024")
        self.assertEqual(album.owner, self.user)
        self.assertEqual(self._order(album), [self.b.pk, self.a.pk])
        self.assertEqual(response.json()["count"], 2)

    def test_create_refuses_a_photo_the_user_cannot_open(self):
        bobs = make_photo(self.bob, "bob.jpg", _at(2024, 7, 14))

        response = self.client.post(
            API,
            {"title": "Mine", "files": [str(self.a.uuid), str(bobs.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Album.objects.exists())

    def test_create_needs_a_title(self):
        response = self.client.post(
            API, {"title": " "}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)

    def test_create_refuses_a_malformed_file_list(self):
        for files in ("nope", [], ["not-a-uuid"]):
            response = self.client.post(
                API, {"title": "X", "files": files}, content_type="application/json"
            )
            self.assertEqual(response.status_code, 400, files)


class AlbumDetailTests(AlbumApiTestCase):
    def setUp(self):
        super().setUp()
        self.album = create_album(self.user, "Summer", files=[self.a, self.b])

    def _url(self, album=None):
        return f"{API}/{(album or self.album).uuid}"

    def _patch(self, data, album=None):
        return self.client.patch(
            self._url(album), data, content_type="application/json"
        )

    def test_someone_elses_album_is_not_found(self):
        bobs = create_album(self.bob, "Bob's", files=[])

        self.assertEqual(self.client.get(self._url(bobs)).status_code, 404)
        self.assertEqual(self._patch({"title": "Mine"}, bobs).status_code, 404)
        self.assertEqual(self.client.delete(self._url(bobs)).status_code, 404)
        bobs.refresh_from_db()
        self.assertEqual(bobs.title, "Bob's")

    def test_a_group_album_opens_for_the_groups_members(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        album = Album.objects.create(owner=self.bob, group=family, title="Family")

        response = self.client.get(self._url(album))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"], family.pk)

    def test_rename_describe_and_sort(self):
        response = self._patch(
            {"title": "Winter", "description": "Snow", "sort_mode": "manual"}
        )

        self.assertEqual(response.status_code, 200)
        self.album.refresh_from_db()
        self.assertEqual(
            (self.album.title, self.album.description, self.album.sort_mode),
            ("Winter", "Snow", "manual"),
        )

    def test_an_unknown_sort_mode_is_refused(self):
        self.assertEqual(self._patch({"sort_mode": "random"}).status_code, 400)

    def test_cover_must_be_a_photo_of_the_album(self):
        response = self._patch({"cover": str(self.c.uuid)})

        self.assertEqual(response.status_code, 400)

        response = self._patch({"cover": str(self.b.uuid)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cover"], str(self.b.uuid))
        self.album.refresh_from_db()
        self.assertEqual(self.album.cover, self.b)

    def test_clearing_the_cover_falls_back(self):
        self.album.cover = self.a
        self.album.save(update_fields=["cover"])

        response = self._patch({"cover": None})

        self.assertIsNone(Album.objects.get(pk=self.album.pk).cover)
        # The fallback is the last added item.
        self.assertEqual(response.json()["cover"], str(self.b.uuid))

    def test_a_malformed_cover_is_refused(self):
        self.assertEqual(self._patch({"cover": "nope"}).status_code, 400)

    def test_delete_keeps_the_photos(self):
        response = self.client.delete(self._url())

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Album.objects.filter(pk=self.album.pk).exists())
        self.assertFalse(AlbumItem.objects.exists())
        self.a.refresh_from_db()
        self.assertIsNone(self.a.deleted_at)


class AlbumItemsApiTests(AlbumApiTestCase):
    def setUp(self):
        super().setUp()
        self.album = create_album(self.user, "Summer", files=[self.a])

    def _post(self, path, data):
        return self.client.post(
            f"{API}/{self.album.uuid}{path}", data, content_type="application/json"
        )

    def test_add_appends_and_skips_what_is_there(self):
        response = self._post(
            "/items", {"files": [str(self.a.uuid), str(self.c.uuid), str(self.b.uuid)]}
        )

        self.assertEqual(response.json(), {"added": 2})
        self.assertEqual(self._order(self.album), [self.a.pk, self.c.pk, self.b.pk])
        self.assertEqual(
            AlbumItem.objects.get(album=self.album, file=self.c).added_by, self.user
        )

    def test_a_photo_shared_with_the_user_can_be_added_and_stays_its_owners(self):
        bobs = make_photo(self.bob, "bob.jpg", _at(2024, 7, 14))
        share_file(
            bobs,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.bob,
        )

        response = self._post("/items", {"files": [str(bobs.uuid)]})

        self.assertEqual(response.json(), {"added": 1})
        bobs.refresh_from_db()
        self.assertEqual(bobs.owner, self.bob)

    def test_add_refuses_what_the_user_cannot_open(self):
        bobs = make_photo(self.bob, "bob.jpg", _at(2024, 7, 14))

        response = self._post("/items", {"files": [str(self.b.uuid), str(bobs.uuid)]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._order(self.album), [self.a.pk])

    def test_add_to_someone_elses_album_is_not_found(self):
        bobs = create_album(self.bob, "Bob's")

        response = self.client.post(
            f"{API}/{bobs.uuid}/items",
            {"files": [str(self.a.uuid)]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(AlbumItem.objects.filter(album=bobs).exists())

    def test_remove(self):
        self._post("/items", {"files": [str(self.b.uuid)]})

        response = self._post("/items/remove", {"files": [str(self.a.uuid)]})

        self.assertEqual(response.json(), {"removed": 1})
        self.assertEqual(self._order(self.album), [self.b.pk])

    def test_reorder_needs_manual_order(self):
        self._post("/items", {"files": [str(self.b.uuid), str(self.c.uuid)]})

        response = self._post("/reorder", {"files": [str(self.c.uuid)]})

        self.assertEqual(response.status_code, 403)

    def test_reorder(self):
        self._post("/items", {"files": [str(self.b.uuid), str(self.c.uuid)]})
        self.album.sort_mode = Album.SortMode.MANUAL
        self.album.save(update_fields=["sort_mode"])

        response = self._post(
            "/reorder", {"files": [str(self.c.uuid)], "before": str(self.a.uuid)}
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._order(self.album), [self.c.pk, self.a.pk, self.b.pk])

        response = self._post(
            "/reorder", {"files": [str(self.c.uuid)], "after": str(self.b.uuid)}
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._order(self.album), [self.a.pk, self.b.pk, self.c.pk])

    def test_reorder_refuses_nonsense(self):
        self._post("/items", {"files": [str(self.b.uuid)]})
        self.album.sort_mode = Album.SortMode.MANUAL
        self.album.save(update_fields=["sort_mode"])
        a, b, c = (str(p.uuid) for p in (self.a, self.b, self.c))

        for body in (
            {"files": [a], "before": b, "after": b},
            {"files": [a], "before": "nope"},
            {"files": [a], "after": a},
            {"files": [c]},
        ):
            self.assertEqual(self._post("/reorder", body).status_code, 400, body)
        self.assertEqual(self._order(self.album), [self.a.pk, self.b.pk])


class AlbumActionsApiTests(AlbumApiTestCase):
    URL = f"{API}/actions"

    def _actions(self, uuids):
        return self.client.post(
            self.URL, {"uuids": uuids}, content_type="application/json"
        )

    def test_every_uuid_gets_a_key_and_an_unreachable_one_an_empty_list(self):
        mine = create_album(self.user, "Mine")
        bobs = create_album(self.bob, "Bob's")
        nothing = "00000000-0000-4000-8000-000000000000"

        data = self._actions([str(mine.uuid), str(bobs.uuid), nothing]).json()

        self.assertEqual(data[str(bobs.uuid)], [])
        self.assertEqual(data[nothing], [])
        self.assertEqual(
            [a["id"] for a in data[str(mine.uuid)]],
            [
                "rename",
                "edit_description",
                "change_sort",
                "add_items",
                "remove_items",
                "set_cover",
                "delete",
            ],
        )

    def test_reorder_and_the_sort_label_follow_the_sort_mode(self):
        album = create_album(self.user, "Mine", sort_mode=Album.SortMode.MANUAL)

        actions = {
            a["id"]: a for a in self._actions([str(album.uuid)]).json()[str(album.uuid)]
        }

        self.assertIn("reorder", actions)
        self.assertEqual(actions["change_sort"]["label"], "Sort by capture date")

    def test_keys_are_spelled_as_sent(self):
        album = create_album(self.user, "Mine")
        upper = str(album.uuid).upper()

        data = self._actions([upper]).json()

        self.assertEqual(list(data), [upper])
        self.assertTrue(data[upper])

    def test_malformed_batches(self):
        for body in ({}, {"uuids": []}, {"uuids": ["nope"]}, {"uuids": ["x"] * 201}):
            response = self.client.post(self.URL, body, content_type="application/json")
            self.assertEqual(response.status_code, 400, body)
