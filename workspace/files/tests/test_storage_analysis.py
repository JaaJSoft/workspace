"""Storage analysis: service figures, API endpoints, UI partial and the action."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.actions import ActionRegistry
from workspace.files.models import File
from workspace.files.services import FilePermission
from workspace.files.services.storage_analysis import (
    CATEGORY_SLICES,
    DUPLICATE_COPIES_LIMIT,
    DUPLICATE_GROUPS_LIMIT,
    StorageScope,
    analyze_storage,
    category_breakdown,
    duplicate_groups,
    subfolder_breakdown,
)

User = get_user_model()


def _folder(owner, name, parent=None, group=None):
    return File.objects.create(
        owner=owner,
        name=name,
        node_type=File.NodeType.FOLDER,
        parent=parent,
        group=group,
    )


def _file(owner, name, parent=None, *, size, category="document", hash="", group=None):
    return File.objects.create(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        parent=parent,
        size=size,
        category=category,
        content_hash=hash,
        group=group,
    )


class _TreeMixin:
    """A small personal tree with a sibling user whose files must never leak.

    alice/
      photos/          (folder)
        a.jpg 300 image  hash=h1
        b.jpg 300 image  hash=h1   <- duplicate of a.jpg
        raw/           (folder)
          c.mov 1000 video
      docs/            (folder)
        d.pdf 100 document
      e.txt 50 text (loose at root)
      trashed.zip 700 archive (deleted)
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.photos = _folder(self.alice, "photos")
        self.raw = _folder(self.alice, "raw", parent=self.photos)
        self.docs = _folder(self.alice, "docs")
        self.a = _file(
            self.alice, "a.jpg", self.photos, size=300, category="image", hash="h1"
        )
        self.b = _file(
            self.alice, "b.jpg", self.photos, size=300, category="image", hash="h1"
        )
        self.c = _file(self.alice, "c.mov", self.raw, size=1000, category="video")
        self.d = _file(self.alice, "d.pdf", self.docs, size=100, category="document")
        self.e = _file(self.alice, "e.txt", None, size=50, category="text")
        self.trashed = _file(
            self.alice, "trashed.zip", None, size=700, category="archive"
        )
        self.trashed.deleted_at = timezone.now()
        self.trashed.save(update_fields=["deleted_at"])
        # Bob has a same-named tree: path prefixes collide, owner must split them.
        bob_photos = _folder(self.bob, "photos")
        _file(self.bob, "z.jpg", bob_photos, size=99999, category="image", hash="h1")


class AnalyzeStorageServiceTests(_TreeMixin, TestCase):
    def test_root_totals_exclude_trash_and_other_users(self):
        result = analyze_storage(self.alice)
        self.assertEqual(result["total_size"], 1750)
        self.assertEqual(result["file_count"], 5)
        self.assertEqual(result["folder_count"], 3)
        self.assertTrue(result["is_root"])
        self.assertEqual(result["trash"], {"size": 700, "count": 1})
        self.assertIsNotNone(result["quota"])

    def test_categories_sum_to_total_with_percentages(self):
        cats = analyze_storage(self.alice)["categories"]
        self.assertEqual(
            [c["key"] for c in cats], ["video", "image", "document", "text"]
        )
        self.assertEqual(sum(c["size"] for c in cats), 1750)
        self.assertEqual(cats[0]["percent"], round(100 * 1000 / 1750, 1))
        self.assertEqual(cats[1]["count"], 2)

    def test_categories_past_the_slice_limit_fold_into_other(self):
        for i, key in enumerate(
            ["audio", "archive", "code", "font", "executable", "application"]
        ):
            _file(self.alice, f"x{i}", None, size=10 - i, category=key)
        _file(self.alice, "mystery", None, size=1, category="unknown")
        cats = category_breakdown(StorageScope(self.alice, None))
        self.assertEqual(len(cats), CATEGORY_SLICES + 1)
        other = cats[-1]
        self.assertEqual(other["key"], "other")
        # code(8) + font(7) + executable(6) + application(5) + unknown(1).
        self.assertEqual((other["size"], other["count"]), (27, 5))
        # Only the last "other" slice may be a merge; the rest are the largest categories.
        self.assertNotIn("other", [c["key"] for c in cats[:-1]])

    def test_subfolders_are_recursive_and_include_loose_files(self):
        entries = subfolder_breakdown(StorageScope(self.alice, None))
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(by_name["photos"]["size"], 1600)  # 300+300+1000
        self.assertEqual(by_name["photos"]["count"], 3)
        self.assertEqual(by_name["docs"]["size"], 100)
        loose = by_name["Files in this folder"]
        self.assertIsNone(loose["uuid"])
        self.assertEqual((loose["size"], loose["count"]), (50, 1))
        self.assertEqual(
            [e["name"] for e in entries], ["photos", "docs", "Files in this folder"]
        )
        self.assertEqual(entries[0]["percent"], round(100 * 1600 / 1750, 1))
        self.assertAlmostEqual(sum(e["percent"] for e in entries), 100.0, delta=0.2)

    def test_folder_scope_only_counts_its_subtree(self):
        result = analyze_storage(self.alice, self.photos)
        self.assertFalse(result["is_root"])
        self.assertIsNone(result["trash"])
        self.assertIsNone(result["quota"])
        self.assertEqual(result["total_size"], 1600)
        self.assertEqual(result["folder"]["uuid"], str(self.photos.uuid))
        self.assertEqual(
            [e["name"] for e in result["subfolders"]], ["raw", "Files in this folder"]
        )
        self.assertEqual(result["subfolders"][0]["size"], 1000)

    def test_subfolder_sizes_follow_the_tree_owner_not_the_viewer(self):
        # The service does not check access; whoever asks, the subtree is
        # alice's, so the per-folder rows must add up to her total.
        result = analyze_storage(self.bob, self.photos)
        self.assertEqual(result["total_size"], 1600)
        self.assertEqual(sum(e["size"] for e in result["subfolders"]), 1600)

    def test_largest_files_order_and_category_filter(self):
        result = analyze_storage(self.alice)
        self.assertEqual(
            [f["name"] for f in result["largest_files"]][:3],
            ["c.mov", "a.jpg", "b.jpg"],
        )
        self.assertEqual(result["largest_files"][0]["parent"], str(self.raw.uuid))
        filtered = analyze_storage(self.alice, category="image")
        self.assertEqual(
            {f["name"] for f in filtered["largest_files"]}, {"a.jpg", "b.jpg"}
        )
        self.assertEqual(filtered["largest_files_category"], "image")

    def test_duplicates_are_scoped_and_ranked_by_wasted_bytes(self):
        # Three 200-byte copies waste 400 bytes: more than the two 300-byte
        # jpgs (300), even though the jpgs weigh more in total.
        _file(self.alice, "f1", self.docs, size=200, category="text", hash="h2")
        _file(self.alice, "f2", self.docs, size=200, category="text", hash="h2")
        _file(self.alice, "f3", self.docs, size=200, category="text", hash="h2")
        _file(self.alice, "nohash1", None, size=5)
        _file(self.alice, "nohash2", None, size=5)
        groups = duplicate_groups(StorageScope(self.alice, None))
        self.assertEqual([g["content_hash"] for g in groups], ["h2", "h1"])
        h2, h1 = groups
        self.assertEqual((h2["copies"], h2["size"], h2["wasted"]), (3, 200, 400))
        self.assertEqual((h1["copies"], h1["size"], h1["wasted"]), (2, 300, 300))
        # Bob's z.jpg shares h1 but is not in alice's scope.
        self.assertEqual({f["name"] for f in h1["files"]}, {"a.jpg", "b.jpg"})
        # Empty hashes never form a group.
        self.assertNotIn("", [g["content_hash"] for g in groups])
        # Scoped to docs, only h2 remains.
        self.assertEqual(
            [
                g["content_hash"]
                for g in duplicate_groups(StorageScope(self.alice, self.docs))
            ],
            ["h2"],
        )

    def test_same_hash_different_size_is_not_a_duplicate(self):
        # A hash collision (or a stale hash on a rewritten blob) must not
        # pair two files of different sizes.
        _file(self.alice, "odd.jpg", self.docs, size=301, category="image", hash="h1")
        groups = duplicate_groups(StorageScope(self.alice, None))
        (h1,) = [g for g in groups if g["content_hash"] == "h1"]
        self.assertEqual(h1["copies"], 2)
        self.assertEqual({f["name"] for f in h1["files"]}, {"a.jpg", "b.jpg"})

    def test_duplicate_copies_are_capped_per_group_and_the_rest_counted(self):
        for i in range(DUPLICATE_COPIES_LIMIT + 3):
            _file(
                self.alice, f"c{i:02d}", self.docs, size=10, category="text", hash="h3"
            )
        (group,) = [
            g
            for g in duplicate_groups(StorageScope(self.alice, self.docs))
            if g["content_hash"] == "h3"
        ]
        self.assertEqual(group["copies"], DUPLICATE_COPIES_LIMIT + 3)
        self.assertEqual(len(group["files"]), DUPLICATE_COPIES_LIMIT)
        self.assertEqual(group["omitted"], 3)
        self.assertEqual(group["files"][0]["name"], "c00")

    def test_duplicates_truncated_flag(self):
        for i in range(DUPLICATE_GROUPS_LIMIT):
            _file(self.alice, f"p{i}", self.docs, size=10, hash=f"dup{i}")
            _file(self.alice, f"q{i}", self.docs, size=10, hash=f"dup{i}")
        result = analyze_storage(self.alice, self.docs)
        self.assertEqual(len(result["duplicates"]), DUPLICATE_GROUPS_LIMIT)
        self.assertTrue(result["duplicates_truncated"])
        self.assertFalse(
            analyze_storage(self.alice, self.photos)["duplicates_truncated"]
        )

    def test_group_root_reports_the_group_trash(self):
        group = Group.objects.create(name="team")
        self.alice.groups.add(group)
        root = _folder(self.alice, "team", group=group)
        sub = _folder(self.bob, "shared", parent=root, group=group)
        _file(self.bob, "g1", sub, size=400, category="image", group=group)
        gone = _file(self.alice, "g2", root, size=250, category="image", group=group)
        gone.deleted_at = timezone.now()
        gone.save(update_fields=["deleted_at"])
        result = analyze_storage(self.alice, root)
        self.assertTrue(result["is_root"])
        self.assertEqual(result["total_size"], 400)
        self.assertEqual(result["trash"], {"size": 250, "count": 1})
        self.assertIsNone(result["quota"])
        self.assertEqual(result["subfolders"][0]["name"], "shared")
        self.assertEqual(result["subfolders"][0]["size"], 400)


class StorageApiTests(_TreeMixin, APITestCase):
    def test_root_endpoint(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.get("/api/v1/files/storage")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_size"], 1750)
        self.assertEqual(resp.data["trash"]["count"], 1)

    def test_folder_endpoint_and_category_filter(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.get(
            f"/api/v1/files/{self.photos.uuid}/storage?category=image"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_size"], 1600)
        self.assertEqual(
            {f["name"] for f in resp.data["largest_files"]}, {"a.jpg", "b.jpg"}
        )

    def test_unknown_category_is_a_400(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.get("/api/v1/files/storage?category=nope")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_file_uuid_is_a_400(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.get(f"/api/v1/files/{self.a.uuid}/storage")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_uuid_is_a_404(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.get("/api/v1/files/not-a-uuid/storage")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_users_folder_is_a_404(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.get(f"/api/v1/files/{self.photos.uuid}/storage")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_rejected(self):
        resp = self.client.get("/api/v1/files/storage")
        self.assertIn(resp.status_code, (401, 403))


class StorageUiViewTests(_TreeMixin, TestCase):
    def test_root_partial_renders_every_section(self):
        self.client.force_login(self.alice)
        resp = self.client.get("/files/storage")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="storage-analysis"', html)
        self.assertIn("Empty trash", html)
        self.assertIn(f"/files/storage/{self.photos.uuid}", html)
        self.assertIn("?category=video", html)
        self.assertIn("c.mov", html)

    def test_other_category_is_not_a_link(self):
        for i, key in enumerate(["audio", "archive", "code", "font", "executable"]):
            _file(self.alice, f"x{i}", None, size=10 - i, category=key)
        self.client.force_login(self.alice)
        html = self.client.get("/files/storage").content.decode()
        self.assertIn("Other", html)
        self.assertNotIn("?category=other", html)

    def test_folder_partial_links_back_to_the_parent(self):
        self.client.force_login(self.alice)
        resp = self.client.get(f"/files/storage/{self.raw.uuid}")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(f'href="/files/storage/{self.photos.uuid}"', html)
        self.assertNotIn("Empty trash", html)

    def test_folder_of_another_user_is_a_404(self):
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.get(f"/files/storage/{self.photos.uuid}").status_code, 404
        )

    def test_unknown_category_is_a_404(self):
        self.client.force_login(self.alice)
        self.assertEqual(
            self.client.get("/files/storage?category=nope").status_code, 404
        )


class AnalyzeStorageActionTests(_TreeMixin, TestCase):
    def test_available_on_live_folders_only(self):
        self.assertTrue(
            ActionRegistry.is_action_available(
                "analyze_storage",
                self.alice,
                self.photos,
                permission=FilePermission.MANAGE,
            )
        )
        self.assertFalse(
            ActionRegistry.is_action_available(
                "analyze_storage", self.alice, self.a, permission=FilePermission.MANAGE
            )
        )
        self.assertFalse(
            ActionRegistry.is_action_available(
                "analyze_storage", self.bob, self.photos, permission=None
            )
        )
        self.photos.deleted_at = timezone.now()
        self.assertFalse(
            ActionRegistry.is_action_available(
                "analyze_storage",
                self.alice,
                self.photos,
                permission=FilePermission.MANAGE,
            )
        )
