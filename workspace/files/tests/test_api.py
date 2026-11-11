import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShare
from workspace.files.services import trash, tree
from workspace.files.tests.operations import outcome_of, run_operation

User = get_user_model()


class FileModelTests(TestCase):
    """Tests for the File model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )

    def test_create_folder(self):
        """Test creating a folder."""
        folder = File.objects.create(
            owner=self.user, name="Documents", node_type=File.NodeType.FOLDER
        )
        self.assertEqual(folder.name, "Documents")
        self.assertEqual(folder.node_type, File.NodeType.FOLDER)
        self.assertEqual(folder.path, "Documents")
        self.assertIsNone(folder.parent)

    def test_create_nested_folder(self):
        """Test creating nested folders."""
        parent = File.objects.create(
            owner=self.user, name="Documents", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Work", node_type=File.NodeType.FOLDER, parent=parent
        )
        self.assertEqual(child.path, "Documents/Work")
        self.assertEqual(child.parent, parent)

    def test_create_file(self):
        """Test creating a file."""
        file = File.objects.create(
            owner=self.user,
            name="test.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.assertEqual(file.name, "test.txt")
        self.assertEqual(file.node_type, File.NodeType.FILE)

    def test_create_file_with_content(self):
        """Test creating a file with content."""
        file = File(
            owner=self.user,
            name="test.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        file.content = ContentFile(b"Hello World", name="test.txt")
        file.size = 11
        file.save()

        self.assertEqual(file.size, 11)
        self.assertTrue(file.content)

    def test_path_updates_on_rename(self):
        """Test that path updates when a folder is renamed."""
        folder = File.objects.create(
            owner=self.user, name="Old", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FOLDER, parent=folder
        )

        folder.name = "New"
        folder.save()

        child.refresh_from_db()
        self.assertEqual(folder.path, "New")
        self.assertEqual(child.path, "New/Child")

    def test_path_updates_on_move(self):
        """Test that path updates when a folder is moved."""
        folder1 = File.objects.create(
            owner=self.user, name="Folder1", node_type=File.NodeType.FOLDER
        )
        folder2 = File.objects.create(
            owner=self.user, name="Folder2", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user,
            name="Child",
            node_type=File.NodeType.FOLDER,
            parent=folder1,
        )

        self.assertEqual(child.path, "Folder1/Child")

        child.parent = folder2
        child.save()

        child.refresh_from_db()
        self.assertEqual(child.path, "Folder2/Child")

    def test_soft_delete(self):
        """Test soft delete."""
        folder = File.objects.create(
            owner=self.user, name="ToDelete", node_type=File.NodeType.FOLDER
        )
        trash.trash_subtree(folder)

        folder.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)
        self.assertTrue(folder.is_deleted())

    def test_soft_delete_cascades_to_children(self):
        """Test that soft delete cascades to children."""
        folder = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FILE, parent=folder
        )

        trash.trash_subtree(folder)

        folder.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)
        self.assertIsNotNone(child.deleted_at)

    def test_restore(self):
        """Test restoring a deleted item."""
        folder = File.objects.create(
            owner=self.user, name="ToRestore", node_type=File.NodeType.FOLDER
        )
        trash.trash_subtree(folder)
        folder.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)

        trash.restore_subtree(folder)
        folder.refresh_from_db()
        self.assertIsNone(folder.deleted_at)

    def test_restore_cascades_to_children(self):
        """Test that restore cascades to children."""
        folder = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FILE, parent=folder
        )

        trash.trash_subtree(folder)
        folder.refresh_from_db()

        trash.restore_subtree(folder)
        folder.refresh_from_db()
        child.refresh_from_db()

        self.assertIsNone(folder.deleted_at)
        self.assertIsNone(child.deleted_at)

    def test_hard_delete(self):
        """Test hard delete permanently removes the item."""
        folder = File.objects.create(
            owner=self.user, name="ToDelete", node_type=File.NodeType.FOLDER
        )
        uuid = folder.uuid
        folder.delete()

        self.assertFalse(File.objects.filter(uuid=uuid).exists())


class FileAPITests(APITestCase):
    """Tests for the File API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.other_user = User.objects.create_user(
            username="otheruser", email="other@example.com", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)

    def test_list_files_empty(self):
        """Test listing files when none exist."""
        response = self.client.get("/api/v1/files")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_create_folder(self):
        """Test creating a folder via API."""
        response = self.client.post(
            "/api/v1/files", {"name": "Documents", "node_type": "folder"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Documents")
        self.assertEqual(response.data["node_type"], "folder")

    def test_create_nested_folder(self):
        """Test creating a nested folder via API."""
        parent = File.objects.create(
            owner=self.user, name="Documents", node_type=File.NodeType.FOLDER
        )
        response = self.client.post(
            "/api/v1/files",
            {"name": "Work", "node_type": "folder", "parent": str(parent.uuid)},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(response.data["parent"]), str(parent.uuid))

    def test_list_only_root_files(self):
        """Test that listing without parent filter returns only root items."""
        root = File.objects.create(
            owner=self.user, name="Root", node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FOLDER, parent=root
        )

        response = self.client.get("/api/v1/files")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["uuid"], str(root.uuid))

    def test_list_folder_contents(self):
        """Test listing contents of a folder."""
        root = File.objects.create(
            owner=self.user, name="Root", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FILE, parent=root
        )

        response = self.client.get(f"/api/v1/files?parent={root.uuid}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["uuid"], str(child.uuid))

    def test_retrieve_file(self):
        """Test retrieving a specific file."""
        file = File.objects.create(
            owner=self.user, name="test.txt", node_type=File.NodeType.FILE
        )
        response = self.client.get(f"/api/v1/files/{file.uuid}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "test.txt")

    def test_update_file_name(self):
        """Test renaming a file."""
        file = File.objects.create(
            owner=self.user, name="old.txt", node_type=File.NodeType.FILE
        )
        response = self.client.patch(f"/api/v1/files/{file.uuid}", {"name": "new.txt"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "new.txt")

    def test_move_file(self):
        """Test moving a file to another folder."""
        folder1 = File.objects.create(
            owner=self.user, name="Folder1", node_type=File.NodeType.FOLDER
        )
        folder2 = File.objects.create(
            owner=self.user, name="Folder2", node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user,
            name="file.txt",
            node_type=File.NodeType.FILE,
            parent=folder1,
        )

        response = run_operation(self.client, "move", [file], folder2)
        self.assertEqual(outcome_of(response), ({"done": 1}, []))

        file.refresh_from_db()
        self.assertEqual(file.path, "Folder2/file.txt")

    def test_patch_refuses_to_move(self):
        """Moves go through operations: a PATCH that changes the parent is refused."""
        folder = File.objects.create(
            owner=self.user, name="Folder", node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user, name="file.txt", node_type=File.NodeType.FILE
        )

        response = self.client.patch(
            f"/api/v1/files/{file.uuid}",
            {"parent": str(folder.uuid), "name": "renamed.txt"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent", response.json())
        file.refresh_from_db()
        self.assertEqual((file.parent_id, file.name), (None, "file.txt"))

    def test_patch_accepts_the_parent_it_already_has(self):
        folder = File.objects.create(
            owner=self.user, name="Folder", node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user,
            name="file.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
        )

        response = self.client.patch(
            f"/api/v1/files/{file.uuid}",
            {"parent": str(folder.uuid), "name": "renamed.txt"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        file.refresh_from_db()
        self.assertEqual((file.parent_id, file.name), (folder.pk, "renamed.txt"))

    def test_patch_same_parent_is_noop(self):
        """PATCH with the file's existing parent must succeed without error.

        The serializer no longer short-circuits on parent equality before
        calling tree.move() - move() handles the noop internally.
        Regression guard for that contract.
        """
        folder = File.objects.create(
            owner=self.user,
            name="Folder",
            node_type=File.NodeType.FOLDER,
        )
        file = File.objects.create(
            owner=self.user,
            name="file.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
        )
        response = self.client.patch(
            f"/api/v1/files/{file.uuid}",
            {
                "parent": str(folder.uuid),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertEqual(file.parent_id, folder.uuid)

    def test_move_file_to_root(self):
        """Test moving a file to root (parent=null)."""
        folder = File.objects.create(
            owner=self.user, name="Folder", node_type=File.NodeType.FOLDER
        )
        file = File.objects.create(
            owner=self.user,
            name="file.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
        )

        response = run_operation(self.client, "move", [file], None)
        self.assertEqual(outcome_of(response), ({"done": 1}, []))

        file.refresh_from_db()
        self.assertEqual(file.path, "file.txt")

    def test_delete_file(self):
        """Test deleting a file (soft delete)."""
        file = File.objects.create(
            owner=self.user, name="todelete.txt", node_type=File.NodeType.FILE
        )
        response = run_operation(self.client, "trash", [file])
        self.assertEqual(outcome_of(response), ({"done": 1}, []))

        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

    def test_renaming_a_folder_onto_another_folders_name_says_so(self):
        File.objects.create(
            owner=self.user, name="Taken", node_type=File.NodeType.FOLDER
        )
        folder = File.objects.create(
            owner=self.user, name="Mine", node_type=File.NodeType.FOLDER
        )

        response = self.client.patch(
            f"/api/v1/files/{folder.uuid}", {"name": "Taken"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json()["name"],
            ["A folder with the same name already exists in this folder."],
        )

    def test_delete_trashes_one_item(self):
        file = File.objects.create(
            owner=self.user, name="gone.txt", node_type=File.NodeType.FILE
        )

        response = self.client.delete(f"/api/v1/files/{file.uuid}")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

    def test_delete_refuses_a_file_someone_else_is_editing(self):
        other = User.objects.create_user(username="editor", password="pw")
        file = File.objects.create(
            owner=self.user, name="busy.txt", node_type=File.NodeType.FILE
        )
        File.objects.filter(pk=file.pk).update(
            locked_by=other,
            locked_at=timezone.now(),
            lock_expires_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.delete(f"/api/v1/files/{file.uuid}")

        self.assertEqual(response.status_code, 423)
        file.refresh_from_db()
        self.assertIsNone(file.deleted_at)

    def test_delete_refuses_a_groups_root_folder(self):
        group = Group.objects.create(name="Design")
        self.user.groups.add(group)
        root = tree.create_folder(self.user, "Design", group=group)

        response = self.client.delete(f"/api/v1/files/{root.uuid}")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        root.refresh_from_db()
        self.assertIsNone(root.deleted_at)

    def test_delete_of_a_file_out_of_reach_answers_like_a_missing_one(self):
        other = User.objects.create_user(username="other", password="pw")
        theirs = File.objects.create(
            owner=other, name="theirs.txt", node_type=File.NodeType.FILE
        )
        FileShare.objects.create(file=theirs, shared_by=other, shared_with=self.user)

        hidden = self.client.delete(f"/api/v1/files/{theirs.uuid}")
        absent = self.client.delete(f"/api/v1/files/{uuid.uuid4()}")

        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        theirs.refresh_from_db()
        self.assertIsNone(theirs.deleted_at)

    def test_cannot_access_other_user_files(self):
        """Test that users cannot access other users' files."""
        other_file = File.objects.create(
            owner=self.other_user, name="private.txt", node_type=File.NodeType.FILE
        )
        response = self.client.get(f"/api/v1/files/{other_file.uuid}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_move_to_other_user_folder(self):
        """Test that users cannot move files to other users' folders."""
        other_folder = File.objects.create(
            owner=self.other_user, name="OtherFolder", node_type=File.NodeType.FOLDER
        )
        my_file = File.objects.create(
            owner=self.user, name="myfile.txt", node_type=File.NodeType.FILE
        )

        response = self.client.patch(
            f"/api/v1/files/{my_file.uuid}", {"parent": str(other_folder.uuid)}
        )
        # Should fail validation - parent folder not found
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_content_resets_has_thumbnail(self):
        """PATCH content must invalidate any cached thumbnail flag.

        Regression: before the FileSerializer was routed through
        update_content, the API path left has_thumbnail untouched,
        so an image whose bytes were replaced kept serving the stale thumbnail.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = File.objects.create(
            owner=self.user,
            name="photo.png",
            node_type=File.NodeType.FILE,
            has_thumbnail=True,
        )
        file.content = ContentFile(b"old", name="photo.png")
        file.save()

        new_content = SimpleUploadedFile(
            "photo.png", b"new bytes", content_type="image/png"
        )
        resp = self.client.patch(
            f"/api/v1/files/{file.uuid}",
            {"content": new_content},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertFalse(file.has_thumbnail)

    def test_update_content_bumps_updated_at(self):
        """PATCH content must advance updated_at."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
        )
        file.content = ContentFile(b"old", name="doc.txt")
        file.save()
        original_updated_at = file.updated_at

        new_content = SimpleUploadedFile("doc.txt", b"new", content_type="text/plain")
        resp = self.client.patch(
            f"/api/v1/files/{file.uuid}",
            {"content": new_content},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertGreater(file.updated_at, original_updated_at)

    def test_update_content_persists_new_size(self):
        """PATCH content must persist the new byte size on the row."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            size=3,
        )
        file.content = ContentFile(b"old", name="doc.txt")
        file.save()

        new_content = SimpleUploadedFile(
            "doc.txt",
            b"much longer content",
            content_type="text/plain",
        )
        resp = self.client.patch(
            f"/api/v1/files/{file.uuid}",
            {"content": new_content},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        file.refresh_from_db()
        self.assertEqual(file.size, len(b"much longer content"))

    def test_list_ordering_case_insensitive(self):
        """Name sort must be case-insensitive (a < B < c)."""
        for name in ["banana", "Cherry", "apple"]:
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
            )

        response = self.client.get("/api/v1/files")
        names = [f["name"] for f in response.data]
        self.assertEqual(names, ["apple", "banana", "Cherry"])

    def test_list_ordering_by_name_param_case_insensitive(self):
        """Explicit ?ordering=name must also be case-insensitive."""
        for name in ["banana", "Cherry", "apple"]:
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
            )

        response = self.client.get("/api/v1/files?ordering=name")
        names = [f["name"] for f in response.data]
        self.assertEqual(names, ["apple", "banana", "Cherry"])

    def test_list_ordering_by_name_desc_case_insensitive(self):
        """Explicit ?ordering=-name must also be case-insensitive."""
        for name in ["banana", "Cherry", "apple"]:
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
            )

        response = self.client.get("/api/v1/files?ordering=-name")
        names = [f["name"] for f in response.data]
        self.assertEqual(names, ["Cherry", "banana", "apple"])


class EdgeCaseTests(APITestCase):
    """Tests for edge cases and error handling."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)

    def test_move_folder_into_itself_fails(self):
        """Test that moving a folder into itself fails."""
        folder = File.objects.create(
            owner=self.user, name="Folder", node_type=File.NodeType.FOLDER
        )

        response = self.client.patch(
            f"/api/v1/files/{folder.uuid}", {"parent": str(folder.uuid)}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_move_folder_into_descendant_fails(self):
        """Test that moving a folder into its descendant fails."""
        parent = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FOLDER, parent=parent
        )

        response = self.client.patch(
            f"/api/v1/files/{parent.uuid}", {"parent": str(child.uuid)}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deep_nesting_path_updates(self):
        """Test that path updates correctly for deeply nested structures."""
        level1 = File.objects.create(
            owner=self.user, name="L1", node_type=File.NodeType.FOLDER
        )
        level2 = File.objects.create(
            owner=self.user, name="L2", node_type=File.NodeType.FOLDER, parent=level1
        )
        level3 = File.objects.create(
            owner=self.user, name="L3", node_type=File.NodeType.FOLDER, parent=level2
        )
        file = File.objects.create(
            owner=self.user,
            name="deep.txt",
            node_type=File.NodeType.FILE,
            parent=level3,
        )

        self.assertEqual(file.path, "L1/L2/L3/deep.txt")

        # Rename L1
        level1.name = "Level1"
        level1.save()

        file.refresh_from_db()
        self.assertEqual(file.path, "Level1/L2/L3/deep.txt")

    def test_special_characters_in_name(self):
        """Test handling of special characters in file names."""
        file = File.objects.create(
            owner=self.user,
            name="file (1) [test] {data}.txt",
            node_type=File.NodeType.FILE,
        )
        self.assertEqual(file.name, "file (1) [test] {data}.txt")

        response = self.client.get(f"/api/v1/files/{file.uuid}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "file (1) [test] {data}.txt")

    def test_unicode_in_name(self):
        """Test handling of unicode characters in file names."""
        file = File.objects.create(
            owner=self.user, name="文档.txt", node_type=File.NodeType.FILE
        )
        self.assertEqual(file.name, "文档.txt")

        response = self.client.get(f"/api/v1/files/{file.uuid}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "文档.txt")

    def test_deleted_parent_children_behavior(self):
        """Test behavior when parent is deleted but child is restored."""
        parent = File.objects.create(
            owner=self.user, name="Parent", node_type=File.NodeType.FOLDER
        )
        child = File.objects.create(
            owner=self.user, name="Child", node_type=File.NodeType.FILE, parent=parent
        )

        # Delete parent (should cascade to child)
        trash.trash_subtree(parent)
        parent.refresh_from_db()
        child.refresh_from_db()

        self.assertIsNotNone(parent.deleted_at)
        self.assertIsNotNone(child.deleted_at)

        # Restore child - should also restore parent
        trash.restore_subtree(child)
        parent.refresh_from_db()
        child.refresh_from_db()

        self.assertIsNone(child.deleted_at)
        # Parent should also be restored
        self.assertIsNone(parent.deleted_at)


class ExcludeDescendantsOfFilterTests(APITestCase):
    """The ?exclude_descendants_of=<uuid> list filter drops a folder's subtree.

    Tree built under a root "Notes" folder:
        Notes/note1.md
        Notes/Sub/note2.md
        Notes/Journal/daily.md          <- excluded subtree
        Notes/JournalArchive/arch.md    <- sibling prefix, must NOT be excluded
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="excl", email="excl@example.com", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)
        self.notes = File.objects.create(
            owner=self.user, name="Notes", node_type=File.NodeType.FOLDER
        )
        self.journal = File.objects.create(
            owner=self.user,
            name="Journal",
            node_type=File.NodeType.FOLDER,
            parent=self.notes,
        )
        sub = File.objects.create(
            owner=self.user,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.notes,
        )
        archive = File.objects.create(
            owner=self.user,
            name="JournalArchive",
            node_type=File.NodeType.FOLDER,
            parent=self.notes,
        )
        self.note1 = File.objects.create(
            owner=self.user,
            name="note1.md",
            node_type=File.NodeType.FILE,
            parent=self.notes,
            type="markdown",
        )
        self.note2 = File.objects.create(
            owner=self.user,
            name="note2.md",
            node_type=File.NodeType.FILE,
            parent=sub,
            type="markdown",
        )
        self.daily = File.objects.create(
            owner=self.user,
            name="daily.md",
            node_type=File.NodeType.FILE,
            parent=self.journal,
            type="markdown",
        )
        self.arch = File.objects.create(
            owner=self.user,
            name="arch.md",
            node_type=File.NodeType.FILE,
            parent=archive,
            type="markdown",
        )

    def _list(self, extra=""):
        url = (
            f"/api/v1/files?type=markdown&parent={self.notes.uuid}&descendants=1"
            + extra
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return {row["uuid"] for row in resp.data}

    def test_excludes_journal_subtree(self):
        uuids = self._list(f"&exclude_descendants_of={self.journal.uuid}")
        self.assertIn(str(self.note1.uuid), uuids)  # direct child kept
        self.assertIn(str(self.note2.uuid), uuids)  # nested child kept
        self.assertIn(str(self.arch.uuid), uuids)  # sibling-prefix kept
        self.assertNotIn(str(self.daily.uuid), uuids)  # journal note dropped

    def test_absent_param_returns_full_subtree(self):
        self.assertEqual(
            self._list(),
            {
                str(self.note1.uuid),
                str(self.note2.uuid),
                str(self.daily.uuid),
                str(self.arch.uuid),
            },
        )

    def test_malformed_param_returns_400(self):
        # A malformed UUID is a client error, not a silent no-op.
        resp = self.client.get(
            f"/api/v1/files?type=markdown&parent={self.notes.uuid}"
            "&descendants=1&exclude_descendants_of=nope"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_uuid_is_noop(self):
        # Valid UUID that resolves to no file -> nothing excluded.
        ghost = "00000000-0000-0000-0000-000000000000"
        self.assertIn(
            str(self.daily.uuid), self._list(f"&exclude_descendants_of={ghost}")
        )


class RecentFilesOrderingTests(APITestCase):
    """Pins the recent view contract: most recently updated first, capped."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="recentuser",
            email="recent@example.com",
            password="testpass123",
        )
        self.client.force_authenticate(user=self.user)
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        self.files = []
        for i in range(3):
            f = File.objects.create(
                owner=self.user,
                name=f"doc{i}.txt",
                node_type=File.NodeType.FILE,
                mime_type="text/plain",
            )
            # updated_at is auto_now; bypass it to control the ordering.
            File.objects.filter(pk=f.pk).update(updated_at=now - timedelta(hours=3 - i))
            self.files.append(f)

    def test_recent_orders_by_updated_at_desc(self):
        resp = self.client.get("/api/v1/files?recent=1")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [f["name"] for f in resp.json()]
        self.assertEqual(names[:3], ["doc2.txt", "doc1.txt", "doc0.txt"])

    def test_recent_limit_caps_results(self):
        resp = self.client.get("/api/v1/files?recent=1&recent_limit=2")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [f["name"] for f in resp.json()]
        self.assertEqual(names, ["doc2.txt", "doc1.txt"])

    def test_recent_excludes_trashed(self):
        from django.utils import timezone

        File.objects.filter(pk=self.files[2].pk).update(deleted_at=timezone.now())
        resp = self.client.get("/api/v1/files?recent=1")
        names = [f["name"] for f in resp.json()]
        self.assertNotIn("doc2.txt", names)
