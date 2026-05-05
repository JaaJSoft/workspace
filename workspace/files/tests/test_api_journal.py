from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.users.services.settings import set_setting

User = get_user_model()


class JournalRenameGateTests(APITestCase):
    """HTTP-level guards: PATCH must refuse to rename a journal note."""

    def tearDown(self):
        # set_setting populates LocMemCache (process-global). Clear to keep
        # test order independent.
        cache.clear()

    def setUp(self):
        self.user = User.objects.create_user(
            username='journal_http', email='jh@test.com', password='pass123',
        )
        self.client.force_authenticate(user=self.user)

        self.journal = File.objects.create(
            owner=self.user, name='Journal', node_type=File.NodeType.FOLDER,
        )
        self.other_folder = File.objects.create(
            owner=self.user, name='Other', node_type=File.NodeType.FOLDER,
        )
        set_setting(self.user, 'notes', 'preferences', {
            'journalFolderUuid': str(self.journal.uuid),
        })

        self.journal_note = File.objects.create(
            owner=self.user, name='2026-04-17.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown', parent=self.journal,
        )
        self.normal_note = File.objects.create(
            owner=self.user, name='shopping.md', node_type=File.NodeType.FILE,
            mime_type='text/markdown', parent=self.other_folder,
        )

    def test_rename_journal_note_forbidden(self):
        resp = self.client.patch(
            f'/api/v1/files/{self.journal_note.uuid}',
            {'name': 'renamed.md'},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.journal_note.refresh_from_db()
        self.assertEqual(self.journal_note.name, '2026-04-17.md')

    def test_rename_normal_note_allowed(self):
        resp = self.client.patch(
            f'/api/v1/files/{self.normal_note.uuid}',
            {'name': 'groceries.md'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.normal_note.refresh_from_db()
        self.assertEqual(self.normal_note.name, 'groceries.md')

    def test_noop_rename_journal_note_allowed(self):
        # Sending the current name unchanged is not a rename - must not 403.
        resp = self.client.patch(
            f'/api/v1/files/{self.journal_note.uuid}',
            {'name': '2026-04-17.md'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_content_update_journal_note_not_gated(self):
        # Content-only PATCH (no 'name' key) must bypass the rename gate.
        # The endpoint may still 4xx for unrelated reasons (content is a
        # FileField that rejects raw JSON strings), but it must not 403
        # from our gate.
        resp = self.client.patch(
            f'/api/v1/files/{self.journal_note.uuid}',
            {'content': '# today\n\nsome content'},
            format='json',
        )
        self.assertNotEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_move_journal_note_allowed(self):
        resp = self.client.patch(
            f'/api/v1/files/{self.journal_note.uuid}',
            {'parent': str(self.other_folder.uuid)},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.journal_note.refresh_from_db()
        self.assertEqual(self.journal_note.parent_id, self.other_folder.uuid)

    def test_actions_endpoint_omits_rename_for_journal_note(self):
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(self.journal_note.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        action_ids = {a['id'] for a in data[str(self.journal_note.uuid)]}
        self.assertNotIn('rename', action_ids)
        # Other actions should still be offered - the frontend keeps those buttons enabled.
        self.assertIn('toggle_favorite', action_ids)
        self.assertIn('delete', action_ids)

    def test_actions_endpoint_includes_rename_for_normal_note(self):
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(self.normal_note.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        action_ids = {a['id'] for a in data[str(self.normal_note.uuid)]}
        self.assertIn('rename', action_ids)
