"""Tests for the purge_orphan_attachments management command."""

import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from workspace.ai.models import BotProfile
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

User = get_user_model()


class PurgeTestMixin:
    """Shared setup: temp MEDIA_ROOT, users, a bot, and helper factories."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        # Override MEDIA_ROOT for the entire test so file.save() uses it too
        self._media_override = self.settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()

        self.human = User.objects.create_user(
            username='human', email='human@test.com', password='pass',
        )
        self.human2 = User.objects.create_user(
            username='human2', email='human2@test.com', password='pass',
        )
        self.bot_user = User.objects.create_user(
            username='testbot', email='bot@test.com', password='pass',
        )
        BotProfile.objects.create(user=self.bot_user)

    def tearDown(self):
        self._media_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    # -- helpers --

    def _make_conv(self, members, left=None):
        """Create a conversation with the given member users.

        *left* is an optional set of users whose membership is marked as left.
        """
        from django.utils import timezone

        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='test',
            created_by=members[0],
        )
        left = left or set()
        for user in members:
            ConversationMember.objects.create(
                conversation=conv,
                user=user,
                left_at=timezone.now() if user in left else None,
            )
        return conv

    def _make_message(self, conv, author):
        return Message.objects.create(
            conversation=conv, author=author, body='hello',
        )

    def _make_attachment(self, message):
        """Create a MessageAttachment with a real file on disk."""
        att = MessageAttachment(
            message=message,
            original_name='pic.png',
            mime_type='image/png',
            size=4,
        )
        att.file.save('pic.png', ContentFile(b'\x89PNG'), save=False)
        att.save()
        return att

    def _call(self, dry_run=False):
        call_command(
            'purge_orphan_attachments',
            **({'dry_run': True} if dry_run else {}),
        )


# ---------------------------------------------------------------
# Phase 1 — abandoned conversations
# ---------------------------------------------------------------


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
class AbandonedConversationTests(PurgeTestMixin, TestCase):

    def test_conv_with_active_human_is_kept(self):
        """Conversations with at least one active human are untouched."""
        conv = self._make_conv([self.human, self.bot_user])
        msg = self._make_message(conv, self.bot_user)
        att = self._make_attachment(msg)

        self._call()

        self.assertTrue(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertTrue(MessageAttachment.objects.filter(pk=att.pk).exists())
        self.assertTrue(os.path.isfile(
            os.path.join(self.media_root, att.file.name)
        ))

    def test_conv_all_humans_left_is_deleted(self):
        """Conversations where every human left are fully purged."""
        conv = self._make_conv(
            [self.human, self.bot_user],
            left={self.human},
        )
        msg = self._make_message(conv, self.bot_user)
        att = self._make_attachment(msg)
        file_path = os.path.join(self.media_root, att.file.name)
        self.assertTrue(os.path.isfile(file_path))

        self._call()

        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertFalse(Message.objects.filter(pk=msg.pk).exists())
        self.assertFalse(MessageAttachment.objects.filter(pk=att.pk).exists())
        self.assertFalse(os.path.isfile(file_path))

    def test_conv_only_bots_remaining_is_deleted(self):
        """Conversations with only bot members (no humans at all) are purged."""
        conv = self._make_conv([self.bot_user])
        msg = self._make_message(conv, self.bot_user)
        att = self._make_attachment(msg)

        self._call()

        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertFalse(os.path.isfile(
            os.path.join(self.media_root, att.file.name)
        ))

    def test_conv_multiple_humans_one_left_is_kept(self):
        """If one human left but another is still active, keep the conv."""
        conv = self._make_conv(
            [self.human, self.human2, self.bot_user],
            left={self.human},
        )
        msg = self._make_message(conv, self.bot_user)
        att = self._make_attachment(msg)

        self._call()

        self.assertTrue(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertTrue(MessageAttachment.objects.filter(pk=att.pk).exists())

    def test_conv_all_multiple_humans_left_is_deleted(self):
        """If every human in a multi-human conv has left, purge it."""
        conv = self._make_conv(
            [self.human, self.human2, self.bot_user],
            left={self.human, self.human2},
        )
        msg = self._make_message(conv, self.bot_user)
        self._make_attachment(msg)

        self._call()

        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())

    def test_dry_run_does_not_delete(self):
        """Dry-run reports but does not actually delete anything."""
        conv = self._make_conv(
            [self.human, self.bot_user],
            left={self.human},
        )
        msg = self._make_message(conv, self.bot_user)
        att = self._make_attachment(msg)

        self._call(dry_run=True)

        self.assertTrue(Conversation.objects.filter(pk=conv.pk).exists())
        self.assertTrue(MessageAttachment.objects.filter(pk=att.pk).exists())
        self.assertTrue(os.path.isfile(
            os.path.join(self.media_root, att.file.name)
        ))

    def test_conv_no_members_is_deleted(self):
        """Edge case: conversation with zero members is also abandoned."""
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='empty',
            created_by=self.human,
        )
        self._call()
        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())


# ---------------------------------------------------------------
# Phase 2 — orphan files on disk
# ---------------------------------------------------------------


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
class OrphanFileTests(PurgeTestMixin, TestCase):

    def test_orphan_file_deleted(self):
        """A file on disk with no matching DB row is removed."""
        chat_dir = os.path.join(self.media_root, 'chat', 'fake-conv-id')
        os.makedirs(chat_dir)
        orphan_path = os.path.join(chat_dir, 'orphan.png')
        with open(orphan_path, 'wb') as f:
            f.write(b'\x89PNG')

        self._call()

        self.assertFalse(os.path.isfile(orphan_path))

    def test_empty_conv_dir_removed(self):
        """A conversation directory with only orphan files is removed."""
        chat_dir = os.path.join(self.media_root, 'chat', 'dead-conv')
        os.makedirs(chat_dir)
        with open(os.path.join(chat_dir, 'ghost.png'), 'wb') as f:
            f.write(b'\x89PNG')

        self._call()

        self.assertFalse(os.path.isdir(chat_dir))

    def test_live_file_not_deleted(self):
        """A file that matches a DB row is left alone."""
        conv = self._make_conv([self.human, self.bot_user])
        msg = self._make_message(conv, self.human)
        att = self._make_attachment(msg)
        file_path = os.path.join(self.media_root, att.file.name)

        self._call()

        self.assertTrue(os.path.isfile(file_path))

    def test_mixed_dir_only_orphans_removed(self):
        """In a dir with both live and orphan files, only orphans go away."""
        conv = self._make_conv([self.human, self.bot_user])
        msg = self._make_message(conv, self.human)
        att = self._make_attachment(msg)
        live_path = os.path.join(self.media_root, att.file.name)

        # Plant an orphan in the same conversation directory
        conv_dir = os.path.dirname(live_path)
        orphan_path = os.path.join(conv_dir, 'orphan.png')
        with open(orphan_path, 'wb') as f:
            f.write(b'fake')

        self._call()

        self.assertTrue(os.path.isfile(live_path))
        self.assertFalse(os.path.isfile(orphan_path))
        # Directory still exists because it has a live file
        self.assertTrue(os.path.isdir(conv_dir))

    def test_dry_run_keeps_orphan_files(self):
        """Dry-run lists orphan files but does not delete them."""
        chat_dir = os.path.join(self.media_root, 'chat', 'tmp-conv')
        os.makedirs(chat_dir)
        orphan_path = os.path.join(chat_dir, 'orphan.png')
        with open(orphan_path, 'wb') as f:
            f.write(b'\x89PNG')

        self._call(dry_run=True)

        self.assertTrue(os.path.isfile(orphan_path))

    def test_no_chat_dir_is_noop(self):
        """If chat/ directory doesn't exist, phase 2 silently does nothing."""
        # media_root exists but has no chat/ subdirectory — should not crash
        self._call()


# ---------------------------------------------------------------
# Both phases combined
# ---------------------------------------------------------------


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
class CombinedTests(PurgeTestMixin, TestCase):

    def test_abandoned_conv_then_leftover_dir_cleaned(self):
        """Phase 1 deletes the conv; phase 2 cleans up any leftover dir."""
        conv = self._make_conv(
            [self.human, self.bot_user],
            left={self.human},
        )
        msg = self._make_message(conv, self.bot_user)
        att = self._make_attachment(msg)
        conv_dir = os.path.dirname(
            os.path.join(self.media_root, att.file.name)
        )

        self._call()

        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())
        # The directory may or may not exist; if it does, it should be empty
        if os.path.isdir(conv_dir):
            self.assertEqual(os.listdir(conv_dir), [])

    def test_nothing_to_purge(self):
        """When everything is clean, the command just prints nothing."""
        conv = self._make_conv([self.human, self.bot_user])
        self._make_message(conv, self.human)

        self._call()  # should not raise

        self.assertTrue(Conversation.objects.filter(pk=conv.pk).exists())
