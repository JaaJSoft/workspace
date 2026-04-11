"""Tests for workspace.chat.avatar_service."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.avatar_service import (
    delete_group_avatar,
    get_group_avatar_etag,
    get_group_avatar_path,
    has_group_avatar,
    process_and_save_group_avatar,
)
from workspace.chat.models import Conversation

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
            'workspace.chat.avatar_service.process_image_to_webp',
            return_value=dummy_bytes,
        ) as process, mock.patch(
            'workspace.chat.avatar_service.save_image'
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
            'workspace.chat.avatar_service.delete_image'
        ) as delete:
            delete_group_avatar(self.conversation)

        delete.assert_called_once_with(
            get_group_avatar_path(self.conversation.uuid),
        )
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.has_avatar)

    def test_get_group_avatar_etag_delegates_to_image_service(self):
        with mock.patch(
            'workspace.chat.avatar_service.get_image_etag',
            return_value='"abc"',
        ) as etag:
            result = get_group_avatar_etag(self.conversation.uuid)

        etag.assert_called_once_with(
            get_group_avatar_path(self.conversation.uuid),
        )
        self.assertEqual(result, '"abc"')

    def test_get_group_avatar_etag_returns_none_when_missing(self):
        with mock.patch(
            'workspace.chat.avatar_service.get_image_etag',
            return_value=None,
        ):
            self.assertIsNone(get_group_avatar_etag(self.conversation.uuid))
