from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.tests.operations import outcome_of, run_operation

User = get_user_model()


class TrashAPITests(APITestCase):
    """Tests for the trash functionality."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.client.force_authenticate(user=self.user)

    def test_list_trash(self):
        """Test listing trashed items."""
        file = File.objects.create(
            owner=self.user, name="trashed.txt", node_type=File.NodeType.FILE
        )
        file.delete()  # Soft delete

        response = self.client.get("/api/v1/files/trash")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["uuid"], str(file.uuid))

    def test_trash_does_not_show_active_files(self):
        """Test that trash only shows deleted items."""
        File.objects.create(
            owner=self.user, name="active.txt", node_type=File.NodeType.FILE
        )
        trashed_file = File.objects.create(
            owner=self.user, name="trashed.txt", node_type=File.NodeType.FILE
        )
        trashed_file.delete()

        response = self.client.get("/api/v1/files/trash")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["uuid"], str(trashed_file.uuid))

    def test_restore_from_trash(self):
        """Test restoring an item from trash."""
        file = File.objects.create(
            owner=self.user, name="torestore.txt", node_type=File.NodeType.FILE
        )
        file.delete()
        file.refresh_from_db()
        self.assertIsNotNone(file.deleted_at)

        response = run_operation(self.client, "restore", [file])
        self.assertEqual(outcome_of(response), ({"done": 1}, []))

        file.refresh_from_db()
        self.assertIsNone(file.deleted_at)

    def test_purge_from_trash(self):
        """Test permanently deleting an item from trash."""
        file = File.objects.create(
            owner=self.user, name="topurge.txt", node_type=File.NodeType.FILE
        )
        file.delete()
        uuid = file.uuid

        response = run_operation(self.client, "purge", [uuid])
        self.assertEqual(outcome_of(response), ({"done": 1}, []))

        self.assertFalse(File.objects.filter(uuid=uuid).exists())

    def test_cannot_purge_active_file(self):
        """Test that active files cannot be purged."""
        file = File.objects.create(
            owner=self.user, name="active.txt", node_type=File.NodeType.FILE
        )

        response = run_operation(self.client, "purge", [file])
        # A purge only looks in the trash: a live file is not one of its items.
        self.assertEqual(outcome_of(response), ({"failed": 1}, [("", "not_found")]))
        self.assertTrue(File.objects.filter(pk=file.pk).exists())

    def test_clean_trash(self):
        """Test cleaning trash with force option."""
        file = File.objects.create(
            owner=self.user, name="trashed.txt", node_type=File.NodeType.FILE
        )
        file.delete()

        response = run_operation(self.client, "empty_trash")
        self.assertEqual(outcome_of(response), ({"done": 1}, []))

        self.assertFalse(File.objects.filter(uuid=file.uuid).exists())
