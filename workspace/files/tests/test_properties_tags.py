from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShare, FileTag, Tag

User = get_user_model()


class PropertiesPanelTagsTests(APITestCase):
    """GET /files/properties/<uuid> — tags section of the properties partial.

    The partial embeds the file's tags as a ``json_script`` block
    (``id="properties-tags-data"``) and renders the tag editor only for
    personal files owned by the requesting user, mirroring the write scope
    of the tag assignment API (``FileTagView`` -> ``user_files_qs``).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="pass",
        )
        # Plain Django @login_required view -> session auth.
        self.client.login(username="alice", password="pass")
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
        )

    def _properties_body(self, file_obj):
        response = self.client.get(f"/files/properties/{file_obj.uuid}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_owner_personal_file_embeds_tags(self):
        tag = Tag.objects.create(owner=self.user, name="urgent", color="error")
        FileTag.objects.create(file=self.file, tag=tag)

        body = self._properties_body(self.file)

        self.assertIn('id="properties-tags-data"', body)
        self.assertIn("urgent", body)

    def test_untagged_personal_file_still_gets_editor(self):
        body = self._properties_body(self.file)

        # Empty tags payload, but the section (and the editor seed) is there.
        self.assertIn('id="properties-tags-data"', body)

    def test_folder_gets_tag_editor_too(self):
        folder = File.objects.create(
            owner=self.user,
            name="projects",
            node_type=File.NodeType.FOLDER,
        )

        body = self._properties_body(folder)

        self.assertIn('id="properties-tags-data"', body)

    def test_shared_recipient_gets_no_tag_editor(self):
        tag = Tag.objects.create(owner=self.user, name="secret-tag", color="ghost")
        FileTag.objects.create(file=self.file, tag=tag)
        bob = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="pass",
        )
        FileShare.objects.create(
            file=self.file,
            shared_by=self.user,
            shared_with=bob,
            permission=FileShare.Permission.READ_ONLY,
        )
        self.client.login(username="bob", password="pass")

        body = self._properties_body(self.file)

        self.assertNotIn('id="properties-tags-data"', body)
        # The owner's tags must not leak to the recipient either.
        self.assertNotIn("secret-tag", body)

    def test_write_permission_recipient_gets_no_tag_editor(self):
        # Visibility is ownership-based, not permission-based: even a
        # recipient with write access doesn't get the tag editor.
        tag = Tag.objects.create(owner=self.user, name="secret-tag", color="ghost")
        FileTag.objects.create(file=self.file, tag=tag)
        bob = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="pass",
        )
        FileShare.objects.create(
            file=self.file,
            shared_by=self.user,
            shared_with=bob,
            permission=FileShare.Permission.READ_WRITE,
        )
        self.client.login(username="bob", password="pass")

        body = self._properties_body(self.file)

        self.assertNotIn('id="properties-tags-data"', body)
        self.assertNotIn("secret-tag", body)

    def test_group_file_gets_no_tag_editor(self):
        group = Group.objects.create(name="team")
        self.user.groups.add(group)
        group_file = File.objects.create(
            owner=self.user,
            name="shared.txt",
            node_type=File.NodeType.FILE,
            group=group,
        )

        body = self._properties_body(group_file)

        self.assertNotIn('id="properties-tags-data"', body)
