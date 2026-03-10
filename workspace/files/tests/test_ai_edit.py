import base64
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()

FAKE_PNG = b'\x89PNG\r\n\x1a\nfakedata'


@override_settings(AI_IMAGE_MODEL='test-model', AI_API_KEY='test-key')
class AIEditEndpointTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='aiedit', password='pw')
        self.client.force_authenticate(self.user)
        self.file = File.objects.create(
            owner=self.user,
            name='photo.png',
            node_type=File.NodeType.FILE,
            mime_type='image/png',
        )
        self.file.content.save('photo.png', ContentFile(FAKE_PNG))

    @patch('workspace.ai.image_service.ai_edit_image')
    def test_edit_from_original(self, mock_edit):
        """First edit — source_image is null, reads from storage."""
        mock_edit.return_value = b'\x89PNGedited'
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/ai-edit',
            {'prompt': 'make it blue', 'size': '1024x1024'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('image', resp.json())
        mock_edit.assert_called_once()
        args = mock_edit.call_args
        self.assertEqual(args[0][1], 'make it blue')

    @patch('workspace.ai.image_service.ai_edit_image')
    def test_edit_from_source_image(self, mock_edit):
        """Iterative edit — source_image is base64."""
        mock_edit.return_value = b'\x89PNGedited2'
        b64 = base64.b64encode(FAKE_PNG).decode()
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/ai-edit',
            {'prompt': 'add a hat', 'source_image': b64},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        call_source = mock_edit.call_args[0][0]
        self.assertEqual(call_source, FAKE_PNG)

    def test_edit_missing_prompt(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/ai-edit',
            {'prompt': ''},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_edit_not_owner(self):
        other = User.objects.create_user(username='other', password='pw')
        self.client.force_authenticate(other)
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/ai-edit',
            {'prompt': 'make it red'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(AI_IMAGE_MODEL='')
    def test_edit_ai_not_configured(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/ai-edit',
            {'prompt': 'make it red'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('workspace.ai.image_service.ai_edit_image', side_effect=RuntimeError('API down'))
    def test_edit_ai_error(self, mock_edit):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/ai-edit',
            {'prompt': 'make it red'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn('error', resp.json())
