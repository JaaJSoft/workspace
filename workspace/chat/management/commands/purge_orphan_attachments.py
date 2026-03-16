"""Management command to delete orphaned chat attachment files from storage.

Two cleanup phases:
1. Abandoned conversations — all human members have left, only bot messages
   remain.  Deletes attachments (disk + DB), messages, and the conversation.
2. Orphan files — files on disk whose MessageAttachment row no longer exists.
"""

import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Delete orphaned chat attachment files and clean up abandoned "
        "conversations (all human members left)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        stats = {
            'abandoned_convs': 0,
            'abandoned_messages': 0,
            'abandoned_files': 0,
            'orphan_files': 0,
            'empty_dirs': 0,
        }

        self._purge_abandoned_conversations(dry_run, stats)
        self._purge_orphan_files(dry_run, stats)

        if not any(stats.values()):
            self.stdout.write("Nothing to purge.")
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done: {stats['abandoned_convs']} abandoned conversation(s), "
                f"{stats['abandoned_messages']} message(s), "
                f"{stats['abandoned_files']} attachment file(s), "
                f"{stats['orphan_files']} orphan file(s), "
                f"{stats['empty_dirs']} empty dir(s)."
            ))

    # ------------------------------------------------------------------
    # Phase 1: abandoned conversations
    # ------------------------------------------------------------------

    def _purge_abandoned_conversations(self, dry_run, stats):
        """Delete conversations where every human member has left."""

        # A "human" member is one whose user has no BotProfile.
        # Find conversations that have zero active human members.
        convs_with_active_humans = ConversationMember.objects.filter(
            left_at__isnull=True,
        ).exclude(
            user__bot_profile__isnull=False,
        ).values_list('conversation_id', flat=True)

        abandoned = Conversation.objects.exclude(
            pk__in=convs_with_active_humans,
        )

        count = abandoned.count()
        if not count:
            return

        label = "Would purge" if dry_run else "Purging"
        self.stdout.write(f"{label} {count} abandoned conversation(s)…")

        for conv in abandoned.iterator():
            messages = Message.objects.filter(conversation=conv)
            attachments = MessageAttachment.objects.filter(message__in=messages)

            att_count = attachments.count()
            msg_count = messages.count()

            if dry_run:
                self.stdout.write(
                    f"  conv {conv.pk}: {msg_count} message(s), "
                    f"{att_count} attachment(s)"
                )
            else:
                # Delete physical files first
                for att in attachments.iterator():
                    if att.file:
                        try:
                            att.file.delete(save=False)
                        except OSError:
                            logger.warning(
                                "Could not delete file %s", att.file.name
                            )

                # Cascade deletes messages, attachments, members, pins, etc.
                conv.delete()

            stats['abandoned_convs'] += 1
            stats['abandoned_messages'] += msg_count
            stats['abandoned_files'] += att_count

    # ------------------------------------------------------------------
    # Phase 2: orphan files on disk
    # ------------------------------------------------------------------

    def _purge_orphan_files(self, dry_run, stats):
        """Delete files in chat/ that have no matching DB row."""
        chat_root = os.path.join(settings.MEDIA_ROOT, 'chat')

        if not os.path.isdir(chat_root):
            return

        known_paths = set(
            MessageAttachment.objects.values_list('file', flat=True)
        )

        orphan_files = []
        orphan_dirs = []

        for conv_dir in os.scandir(chat_root):
            if not conv_dir.is_dir():
                continue

            dir_has_live_files = False

            for entry in os.scandir(conv_dir.path):
                if not entry.is_file():
                    continue

                rel_path = os.path.join('chat', conv_dir.name, entry.name)
                # Normalize to forward slashes (Django stores paths this way)
                rel_path = rel_path.replace('\\', '/')

                if rel_path in known_paths:
                    dir_has_live_files = True
                else:
                    orphan_files.append(entry.path)

            if not dir_has_live_files:
                orphan_dirs.append(conv_dir.path)

        if not orphan_files and not orphan_dirs:
            return

        label = "Would delete" if dry_run else "Deleting"
        self.stdout.write(
            f"{label} {len(orphan_files)} orphan file(s) "
            f"across {len(orphan_dirs)} empty conversation dir(s)."
        )

        if dry_run:
            for f in orphan_files:
                self.stdout.write(f"  file: {f}")
            for d in orphan_dirs:
                self.stdout.write(f"  dir:  {d}")
            return

        for filepath in orphan_files:
            try:
                os.remove(filepath)
                stats['orphan_files'] += 1
            except OSError:
                logger.warning("Could not delete file %s", filepath)

        for dirpath in orphan_dirs:
            try:
                os.rmdir(dirpath)
                stats['empty_dirs'] += 1
            except OSError:
                logger.warning("Could not remove dir %s", dirpath)
