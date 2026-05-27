from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember, Message, MessageAttachment
from workspace.files.models import File

User = get_user_model()


class BackfillFileTypesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_backfill_file_with_content(self):
        """Files with content get detected via Magika."""
        f = File.objects.create(
            owner=self.user,
            name="hello.py",
            node_type=File.NodeType.FILE,
            mime_type="text/x-python",
        )
        f.content.save("hello.py", ContentFile(b'print("hello")'))
        f.save()

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        f.refresh_from_db()
        self.assertIsNotNone(f.type)
        self.assertNotEqual(f.type, "")

    def test_backfill_file_without_content(self):
        """Files without content fall back to extension-based detection."""
        File.objects.create(
            owner=self.user,
            name="photo.jpg",
            node_type=File.NodeType.FILE,
            mime_type="image/jpeg",
        )

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        f = File.objects.get(name="photo.jpg")
        self.assertEqual(f.type, "jpeg")

    def test_skips_folders(self):
        """Folders should not get a type - they keep the default 'unknown'."""
        folder = File.objects.create(
            owner=self.user,
            name="docs",
            node_type=File.NodeType.FOLDER,
        )

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        folder.refresh_from_db()
        self.assertEqual(folder.type, "unknown")

    def test_skips_already_labeled(self):
        """Files that already have a type are skipped."""
        f = File.objects.create(
            owner=self.user,
            name="test.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            type="txt",
            category="text",
        )

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        f.refresh_from_db()
        self.assertEqual(f.type, "txt")

    def test_dry_run_does_not_update(self):
        """Dry run reports correct count but leaves the DB unchanged."""
        File.objects.create(
            owner=self.user,
            name="readme.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )

        out = StringIO()
        call_command("backfill_file_types", "--dry-run", stdout=out)

        f = File.objects.get(name="readme.md")
        self.assertEqual(f.type, "unknown")
        output = out.getvalue()
        self.assertIn("Would update 1 files", output)


class BackfillAttachmentTypesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        conv = Conversation.objects.create(title="test", kind=Conversation.Kind.GROUP, created_by=self.user)
        ConversationMember.objects.create(conversation=conv, user=self.user)
        self.message = Message.objects.create(conversation=conv, author=self.user, body="hi")

    def _create_attachment(self, name, mime_type, content=None, **kwargs):
        att = MessageAttachment(
            message=self.message,
            original_name=name,
            mime_type=mime_type,
            size=len(content) if content else 0,
            **kwargs,
        )
        if content:
            att.file.save(name, ContentFile(content), save=False)
        att.save()
        return att

    def test_backfill_attachment_by_name(self):
        """Attachments without readable content fall back to extension-based detection."""
        att = self._create_attachment("photo.jpg", "image/jpeg")

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        att.refresh_from_db()
        self.assertEqual(att.type, "jpeg")
        self.assertEqual(att.category, "image")

    def test_backfill_attachment_with_content(self):
        """Attachments with content get detected via Magika."""
        att = self._create_attachment("script.py", "text/x-python", b'print("hello")')

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        att.refresh_from_db()
        self.assertNotEqual(att.type, "unknown")

    def test_skips_already_labeled_attachment(self):
        """Attachments that already have type and category are skipped."""
        att = self._create_attachment("song.mp3", "audio/mpeg", type="mp3", category="audio")

        out = StringIO()
        call_command("backfill_file_types", stdout=out)

        att.refresh_from_db()
        self.assertEqual(att.type, "mp3")
        self.assertIn("[MessageAttachment] Found 0", out.getvalue())

    def test_dry_run_does_not_update_attachment(self):
        """Dry run reports correct count but leaves attachments unchanged."""
        att = self._create_attachment("clip.mp4", "video/mp4")

        out = StringIO()
        call_command("backfill_file_types", "--dry-run", stdout=out)

        att.refresh_from_db()
        self.assertEqual(att.type, "unknown")
        self.assertIn("Would update 1 attachments", out.getvalue())
