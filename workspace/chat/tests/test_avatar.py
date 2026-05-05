"""Tests for workspace.chat.services.avatar and the GroupAvatar* views."""

from io import BytesIO
from unittest import mock

from PIL import Image
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import Conversation
from workspace.chat.services.avatar import (
    delete_group_avatar,
    get_group_avatar_etag,
    get_group_avatar_path,
    has_group_avatar,
    process_and_save_group_avatar,
)

from .test_chat import ChatTestMixin

User = get_user_model()


class AvatarPathTests(TestCase):
    def test_get_group_avatar_path(self):
        self.assertEqual(
            get_group_avatar_path('abc-123'),
            'avatars/groups/abc-123.webp',
        )


class GroupAvatarServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username='owner', password='pass')
        cls.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Team',
            created_by=cls.owner,
        )

    def test_has_group_avatar_reflects_flag(self):
        self.assertFalse(has_group_avatar(self.conversation))
        self.conversation.has_avatar = True
        self.assertTrue(has_group_avatar(self.conversation))

    def test_process_and_save_group_avatar(self):
        dummy_bytes = b'webp-bytes'

        with mock.patch(
            'workspace.chat.services.avatar.process_image_to_webp',
            return_value=dummy_bytes,
        ) as process, mock.patch(
            'workspace.chat.services.avatar.save_image'
        ) as save:
            process_and_save_group_avatar(
                self.conversation,
                image_file='<file-obj>',
                crop_x=10.0, crop_y=20.0, crop_w=100.0, crop_h=100.0,
            )

        process.assert_called_once_with(
            '<file-obj>', 10.0, 20.0, 100.0, 100.0,
        )
        save.assert_called_once_with(
            get_group_avatar_path(self.conversation.uuid),
            dummy_bytes,
        )
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.has_avatar)

    def test_delete_group_avatar(self):
        self.conversation.has_avatar = True
        self.conversation.save(update_fields=['has_avatar'])

        with mock.patch(
            'workspace.chat.services.avatar.delete_image'
        ) as delete:
            delete_group_avatar(self.conversation)

        delete.assert_called_once_with(
            get_group_avatar_path(self.conversation.uuid),
        )
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.has_avatar)

    def test_get_group_avatar_etag_delegates_to_image_service(self):
        with mock.patch(
            'workspace.chat.services.avatar.get_image_etag',
            return_value='"abc"',
        ) as etag:
            result = get_group_avatar_etag(self.conversation.uuid)

        etag.assert_called_once_with(
            get_group_avatar_path(self.conversation.uuid),
        )
        self.assertEqual(result, '"abc"')

    def test_get_group_avatar_etag_returns_none_when_missing(self):
        with mock.patch(
            'workspace.chat.services.avatar.get_image_etag',
            return_value=None,
        ):
            self.assertIsNone(get_group_avatar_etag(self.conversation.uuid))


def _make_test_image():
    """Create a minimal in-memory PNG for upload tests."""
    buf = BytesIO()
    img = Image.new('RGB', (100, 100), color='red')
    img.save(buf, format='PNG')
    buf.seek(0)
    buf.name = 'test.png'
    return buf


class GroupAvatarUploadTests(ChatTestMixin, APITestCase):
    """Tests for POST /api/v1/chat/conversations/<id>/avatar"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar'

    def test_unauthenticated_rejected(self):
        image = _make_test_image()
        resp = self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_cannot_upload(self):
        self.client.force_authenticate(self.outsider)
        image = _make_test_image()
        resp = self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_upload(self):
        self.client.force_authenticate(self.member)
        image = _make_test_image()
        resp = self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertTrue(self.group.has_avatar)

    def test_dm_returns_400(self):
        self.client.force_authenticate(self.creator)
        image = _make_test_image()
        resp = self.client.post(self.url(self.dm.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class GroupAvatarDeleteTests(ChatTestMixin, APITestCase):
    """Tests for DELETE /api/v1/chat/conversations/<id>/avatar"""

    def url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar'

    def _upload_avatar(self):
        """Helper to upload an avatar as creator first."""
        self.client.force_authenticate(self.creator)
        image = _make_test_image()
        self.client.post(self.url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')

    def test_member_can_delete(self):
        self._upload_avatar()
        self.client.force_authenticate(self.member)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.group.refresh_from_db()
        self.assertFalse(self.group.has_avatar)

    def test_outsider_cannot_delete(self):
        self._upload_avatar()
        self.client.force_authenticate(self.outsider)
        resp = self.client.delete(self.url(self.group.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class GroupAvatarRetrieveTests(ChatTestMixin, APITestCase):
    """Tests for GET /api/v1/chat/conversations/<id>/avatar/image"""

    def upload_url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar'

    def retrieve_url(self, conv_id):
        return f'/api/v1/chat/conversations/{conv_id}/avatar/image'

    def test_404_when_no_avatar(self):
        resp = self.client.get(self.retrieve_url(self.group.uuid))
        self.assertEqual(resp.status_code, 404)

    def test_retrieve_after_upload(self):
        self.client.force_authenticate(self.creator)
        image = _make_test_image()
        self.client.post(self.upload_url(self.group.uuid), {
            'image': image, 'crop_x': 0, 'crop_y': 0, 'crop_w': 100, 'crop_h': 100,
        }, format='multipart')
        self.client.logout()
        resp = self.client.get(self.retrieve_url(self.group.uuid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/webp')
