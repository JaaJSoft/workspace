from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from workspace.files.actions import ActionRegistry
from workspace.files.models import File, FileShare, FileTag, Tag
from workspace.files.services import FilePermission, FileService

User = get_user_model()


def _file(owner, name, **kwargs):
    return File.objects.create(
        owner=owner,
        name=name,
        node_type=kwargs.pop("node_type", File.NodeType.FILE),
        **kwargs,
    )


class TagViewTests(TestCase):
    """`/files?tag=<uuid>` lists every file carrying that tag."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tagger", email="tagger@test.com", password="x"
        )
        self.other = User.objects.create_user(
            username="stranger", email="stranger@test.com", password="x"
        )
        self.client.force_login(self.user)

        self.tag = Tag.objects.create(owner=self.user, name="work", color="#3b82f6")
        self.tagged = _file(self.user, "tagged.txt")
        self.untagged = _file(self.user, "untagged.txt")
        FileTag.objects.create(file=self.tagged, tag=self.tag)

    def test_lists_only_files_carrying_the_tag(self):
        response = self.client.get(f"/files?tag={self.tag.uuid}")

        self.assertEqual(response.status_code, 200)
        names = [node.name for node in response.context["nodes"]]
        self.assertEqual(names, ["tagged.txt"])

    def test_tag_view_metadata(self):
        response = self.client.get(f"/files?tag={self.tag.uuid}")

        self.assertTrue(response.context["is_tag_view"])
        self.assertFalse(response.context["is_root_view"])
        self.assertEqual(response.context["page_title"], "work")
        self.assertEqual(response.context["sidebar_active"], f"tag:{self.tag.uuid}")

    def test_tag_view_ignores_trashed_files(self):
        from django.utils import timezone

        self.tagged.deleted_at = timezone.now()
        self.tagged.save(update_fields=["deleted_at"])

        response = self.client.get(f"/files?tag={self.tag.uuid}")

        self.assertEqual(list(response.context["nodes"]), [])

    def test_someone_elses_tag_is_not_found(self):
        foreign = Tag.objects.create(owner=self.other, name="theirs")

        response = self.client.get(f"/files?tag={foreign.uuid}")

        self.assertEqual(response.status_code, 404)

    def test_unknown_tag_is_not_found(self):
        response = self.client.get("/files?tag=00000000-0000-0000-0000-000000000000")

        self.assertEqual(response.status_code, 404)

    def test_malformed_tag_uuid_is_not_found(self):
        """Regression: a raw string reaching filter(uuid=...) raised
        ValidationError from UUIDField.to_python and surfaced as a 500."""
        response = self.client.get("/files?tag=not-a-uuid")

        self.assertEqual(response.status_code, 404)

    def test_tag_param_wins_over_other_special_views(self):
        response = self.client.get(f"/files?tag={self.tag.uuid}&favorites=1&recent=1")

        self.assertTrue(response.context["is_tag_view"])
        self.assertFalse(response.context["is_favorites_view"])
        self.assertFalse(response.context["is_recent_view"])


class ListingTagsTests(TestCase):
    """Tags are rendered on the rows/cards of every browser listing."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="lister", email="lister@test.com", password="x"
        )
        self.client.force_login(self.user)
        self.tag = Tag.objects.create(owner=self.user, name="invoices", color="#eab308")
        self.file = _file(self.user, "bill.txt")
        FileTag.objects.create(file=self.file, tag=self.tag)

    def test_row_exposes_its_tag_uuids_for_client_side_filtering(self):
        response = self.client.get(reverse("files_ui:index"))

        self.assertContains(response, f'data-tags="{self.tag.uuid} "')

    def test_tag_is_rendered_as_a_chip_in_the_listing(self):
        response = self.client.get(reverse("files_ui:index"))

        self.assertContains(response, '<tag-chip name="invoices" color="#eab308"')

    def test_listing_tags_offers_only_tags_present_in_the_listing(self):
        Tag.objects.create(owner=self.user, name="unused", color="#3b82f6")

        response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(
            [t.name for t in response.context["listing_tags"]], ["invoices"]
        )

    def test_shared_listing_does_not_leak_the_owners_tags(self):
        """FileTag rows belong to the file owner; the recipient must never
        see them in the 'Shared with me' listing."""
        owner = User.objects.create_user(
            username="owner", email="owner@test.com", password="x"
        )
        owner_tag = Tag.objects.create(owner=owner, name="confidential")
        shared = _file(owner, "shared.txt")
        FileTag.objects.create(file=shared, tag=owner_tag)
        FileShare.objects.create(
            file=shared,
            shared_with=self.user,
            permission=FileShare.Permission.READ_ONLY,
            shared_by=owner,
        )

        response = self.client.get("/files?shared=1")

        self.assertEqual([n.name for n in response.context["nodes"]], ["shared.txt"])
        self.assertNotContains(response, "confidential")


class ManageTagsActionTests(TestCase):
    """Only files the user can actually tag expose the 'Tags' action."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="acts", email="acts@test.com", password="x"
        )
        self.other = User.objects.create_user(
            username="acts2", email="acts2@test.com", password="x"
        )
        self.action = ActionRegistry.get("manage_tags")

    def _available(self, user, file_obj):
        return self.action.is_available(
            user, file_obj, permission=FileService.get_permission(user, file_obj)
        )

    def test_available_on_own_personal_file(self):
        file_obj = _file(self.user, "mine.txt")

        self.assertTrue(self._available(self.user, file_obj))

    def test_available_on_own_folder(self):
        folder = _file(self.user, "Dir", node_type=File.NodeType.FOLDER)

        self.assertTrue(self._available(self.user, folder))

    def test_unavailable_on_group_file(self):
        group = Group.objects.create(name="team")
        self.user.groups.add(group)
        file_obj = _file(self.user, "team.txt", group=group)

        self.assertFalse(self._available(self.user, file_obj))

    def test_unavailable_for_a_share_recipient(self):
        file_obj = _file(self.other, "theirs.txt")
        FileShare.objects.create(
            file=file_obj,
            shared_with=self.user,
            permission=FileShare.Permission.READ_WRITE,
            shared_by=self.other,
        )

        self.assertEqual(
            FileService.get_permission(self.user, file_obj), FilePermission.WRITE
        )
        self.assertFalse(self._available(self.user, file_obj))

    def test_unavailable_on_trashed_file(self):
        from django.utils import timezone

        file_obj = _file(self.user, "gone.txt", deleted_at=timezone.now())

        self.assertFalse(self._available(self.user, file_obj))
