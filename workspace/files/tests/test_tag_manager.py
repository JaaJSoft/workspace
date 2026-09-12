from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileTag, Tag
from workspace.files.services.tags import (
    TagMergeError,
    merge_tags,
    purge_unused_tags,
    tags_with_usage,
)

User = get_user_model()


def _file(owner, name):
    return File.objects.create(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        mime_type="text/markdown",
    )


class TagFavoriteAndUsageAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.client.force_authenticate(self.user)

    def test_list_carries_usage_count_and_favorite_flag(self):
        tag = Tag.objects.create(owner=self.user, name="work", is_favorite=True)
        FileTag.objects.create(file=_file(self.user, "a.md"), tag=tag)
        FileTag.objects.create(file=_file(self.user, "b.md"), tag=tag)
        resp = self.client.get("/api/v1/tags")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data[0]["file_count"], 2)
        self.assertTrue(resp.data[0]["is_favorite"])

    def test_usage_count_includes_trashed_files(self):
        # Trash is reversible, so the assignment still exists: the count and
        # the "unused" purge must agree on what counts as an assignment.
        tag = Tag.objects.create(owner=self.user, name="work")
        f = _file(self.user, "a.md")
        FileTag.objects.create(file=f, tag=tag)
        f.delete()
        resp = self.client.get("/api/v1/tags")
        self.assertEqual(resp.data[0]["file_count"], 1)

    def test_patch_favorite(self):
        tag = Tag.objects.create(owner=self.user, name="work")
        resp = self.client.patch(f"/api/v1/tags/{tag.uuid}", {"is_favorite": True})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["is_favorite"])
        tag.refresh_from_db()
        self.assertTrue(tag.is_favorite)

    def test_create_defaults_to_not_favorite_with_zero_usage(self):
        resp = self.client.post("/api/v1/tags", {"name": "new"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(resp.data["is_favorite"])
        self.assertEqual(resp.data["file_count"], 0)

    def test_tags_with_usage_is_owner_scoped(self):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="testpass123"
        )
        Tag.objects.create(owner=self.user, name="mine")
        Tag.objects.create(owner=other, name="theirs")
        self.assertEqual([t.name for t in tags_with_usage(self.user)], ["mine"])


class TagMergeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.client.force_authenticate(self.user)
        self.source = Tag.objects.create(owner=self.user, name="todo")
        self.target = Tag.objects.create(owner=self.user, name="to-do")
        self.only_source = _file(self.user, "only-source.md")
        self.both = _file(self.user, "both.md")
        self.only_target = _file(self.user, "only-target.md")
        FileTag.objects.create(file=self.only_source, tag=self.source)
        FileTag.objects.create(file=self.both, tag=self.source)
        FileTag.objects.create(file=self.both, tag=self.target)
        FileTag.objects.create(file=self.only_target, tag=self.target)

    def test_merge_preserves_every_assignment_without_duplicates(self):
        merged = merge_tags(self.source, self.target)
        self.assertEqual(merged, self.target)
        self.assertFalse(Tag.objects.filter(pk=self.source.pk).exists())
        tagged = set(
            FileTag.objects.filter(tag=self.target).values_list("file_id", flat=True)
        )
        self.assertEqual(
            tagged, {self.only_source.pk, self.both.pk, self.only_target.pk}
        )
        self.assertEqual(FileTag.objects.filter(file=self.both).count(), 1)

    def test_merge_into_itself_is_refused(self):
        with self.assertRaises(TagMergeError) as ctx:
            merge_tags(self.source, self.source)
        self.assertEqual(ctx.exception.code, "same_tag")

    def test_merge_across_owners_is_refused(self):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="testpass123"
        )
        theirs = Tag.objects.create(owner=other, name="theirs")
        with self.assertRaises(TagMergeError) as ctx:
            merge_tags(self.source, theirs)
        self.assertEqual(ctx.exception.code, "cross_owner")

    def test_merge_endpoint_returns_the_target_with_its_new_count(self):
        resp = self.client.post(
            f"/api/v1/tags/{self.source.uuid}/merge", {"into": str(self.target.uuid)}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["uuid"], str(self.target.uuid))
        self.assertEqual(resp.data["file_count"], 3)
        self.assertFalse(Tag.objects.filter(pk=self.source.pk).exists())

    def test_merge_endpoint_rejects_self_and_malformed_target(self):
        resp = self.client.post(
            f"/api/v1/tags/{self.source.uuid}/merge", {"into": str(self.source.uuid)}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        resp = self.client.post(
            f"/api/v1/tags/{self.source.uuid}/merge", {"into": "nope"}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        resp = self.client.post(f"/api/v1/tags/{self.source.uuid}/merge", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_merge_endpoint_404s_on_a_target_out_of_reach(self):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="testpass123"
        )
        theirs = Tag.objects.create(owner=other, name="theirs")
        resp = self.client.post(
            f"/api/v1/tags/{self.source.uuid}/merge", {"into": str(theirs.uuid)}
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Tag.objects.filter(pk=self.source.pk).exists())

    def test_merge_endpoint_404s_on_a_source_out_of_reach(self):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="testpass123"
        )
        theirs = Tag.objects.create(owner=other, name="theirs")
        resp = self.client.post(
            f"/api/v1/tags/{theirs.uuid}/merge", {"into": str(self.target.uuid)}
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class TagPurgeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.client.force_authenticate(self.user)

    def test_purge_deletes_only_the_callers_unused_tags(self):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="testpass123"
        )
        used = Tag.objects.create(owner=self.user, name="used")
        Tag.objects.create(owner=self.user, name="unused-a")
        Tag.objects.create(owner=self.user, name="unused-b")
        Tag.objects.create(owner=other, name="their-unused")
        FileTag.objects.create(file=_file(self.user, "a.md"), tag=used)

        self.assertEqual(purge_unused_tags(self.user), 2)
        self.assertEqual(
            set(Tag.objects.values_list("name", flat=True)), {"used", "their-unused"}
        )

    def test_purge_endpoint_reports_how_many_went(self):
        Tag.objects.create(owner=self.user, name="unused")
        resp = self.client.post("/api/v1/tags/purge-unused")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, {"deleted": 1})
        self.assertEqual(Tag.objects.filter(owner=self.user).count(), 0)
