import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.files.models import File, FileLink
from workspace.files.services import FileService
from workspace.files.services.links import extract_link_targets, reconcile_file_links

User = get_user_model()


def _make_markdown(user, name, body=""):
    """Create a markdown File whose ``type`` is forced to 'markdown'."""
    f = FileService.create_file(
        owner=user,
        name=name,
        content=ContentFile(body.encode("utf-8"), name=name),
        mime_type="text/markdown",
    )
    f.type = "markdown"
    f.save(update_fields=["type"])
    return f


def _set_content(f, body):
    """Overwrite a file's stored content with new bytes."""
    f.content.save(f.name, ContentFile(body.encode("utf-8")), save=True)


class FileLinkModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="model-links", password="p")

    def _file(self, name):
        return File.objects.create(
            owner=self.user, name=name, node_type=File.NodeType.FILE
        )

    def test_create_link(self):
        a, b = self._file("a.md"), self._file("b.md")
        link = FileLink.objects.create(source=a, target=b)
        self.assertEqual(link.source, a)
        self.assertEqual(link.target, b)

    def test_unique_source_target(self):
        a, b = self._file("a.md"), self._file("b.md")
        FileLink.objects.create(source=a, target=b)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FileLink.objects.create(source=a, target=b)

    def test_cascade_on_hard_delete(self):
        # The FK ON DELETE CASCADE removes edges when a file is permanently
        # deleted. File.delete() soft-deletes by default; hard=True forces the
        # real DB delete that fires the cascade.
        a, b = self._file("a.md"), self._file("b.md")
        FileLink.objects.create(source=a, target=b)
        a.delete(hard=True)
        self.assertEqual(FileLink.objects.count(), 0)

    def test_soft_delete_keeps_edges(self):
        # Soft-deleted notes keep their edges by design: a restore brings the
        # links back, and the (future) graph read filters deleted nodes out
        # instead. So trashing a file must NOT prune its FileLink rows.
        a, b = self._file("a.md"), self._file("b.md")
        FileLink.objects.create(source=a, target=b)
        a.delete()  # soft delete (default)
        self.assertEqual(FileLink.objects.count(), 1)


class ExtractLinkTargetsTests(TestCase):
    def test_finds_single_note_link(self):
        u = uuid.uuid4()
        text = f"See [Beta](/notes?file={u}) for details."
        self.assertEqual(extract_link_targets(text), {str(u)})

    def test_finds_multiple_distinct_links(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        text = f"[A](/notes?file={a}) and [B](/notes?file={b})"
        self.assertEqual(extract_link_targets(text), {str(a), str(b)})

    def test_deduplicates_repeated_link(self):
        u = uuid.uuid4()
        text = f"[x](/notes?file={u}) [x again](/notes?file={u})"
        self.assertEqual(extract_link_targets(text), {str(u)})

    def test_ignores_non_uuid_file_token(self):
        # 36 chars in the [0-9a-fA-F-] class but not a valid UUID -> dropped.
        text = "[bad](/notes?file=" + ("-" * 36) + ")"
        self.assertEqual(extract_link_targets(text), set())

    def test_empty_and_no_links(self):
        self.assertEqual(extract_link_targets(""), set())
        self.assertEqual(extract_link_targets(None), set())
        self.assertEqual(extract_link_targets("# Title\n\nNo links here."), set())


class ReconcileFileLinksTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="reconcile-links", password="p")

    def test_creates_edges_for_resolved_targets(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        targets = reconcile_file_links(a)
        self.assertEqual(targets, {b.uuid})
        self.assertEqual(
            set(FileLink.objects.filter(source=a).values_list("target_id", flat=True)),
            {b.uuid},
        )

    def test_idempotent(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        reconcile_file_links(a)
        reconcile_file_links(a)
        self.assertEqual(FileLink.objects.filter(source=a).count(), 1)

    def test_removing_link_deletes_edge(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        reconcile_file_links(a)
        _set_content(a, "# A with no links")
        reconcile_file_links(a)
        self.assertEqual(FileLink.objects.filter(source=a).count(), 0)

    def test_unreadable_content_preserves_edges(self):
        # A read failure (read_text_content -> None, e.g. an IO error or a
        # UTF-8 boundary split on a huge note) must NOT wipe existing edges:
        # clearing on a transient failure would silently lose the note's graph.
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        reconcile_file_links(a)
        self.assertEqual(FileLink.objects.filter(source=a).count(), 1)
        with mock.patch(
            "workspace.files.services.links.read_text_content", return_value=None
        ):
            self.assertIsNone(reconcile_file_links(a))
        self.assertEqual(FileLink.objects.filter(source=a).count(), 1)

    def test_self_link_skipped(self):
        a = _make_markdown(self.user, "A.md", "placeholder")
        _set_content(a, f"[self](/notes?file={a.uuid})")
        targets = reconcile_file_links(a)
        self.assertEqual(targets, set())
        self.assertEqual(FileLink.objects.filter(source=a).count(), 0)

    def test_nonexistent_target_creates_no_edge(self):
        a = _make_markdown(self.user, "A.md", f"[ghost](/notes?file={uuid.uuid4()})")
        targets = reconcile_file_links(a)
        self.assertEqual(targets, set())
        self.assertEqual(FileLink.objects.filter(source=a).count(), 0)

    def test_non_markdown_is_skipped(self):
        b = _make_markdown(self.user, "B.md", "# B")
        txt = FileService.create_file(
            owner=self.user,
            name="note.txt",
            content=ContentFile(f"[B](/notes?file={b.uuid})".encode(), name="note.txt"),
            mime_type="text/plain",
        )
        txt.type = "text"
        txt.save(update_fields=["type"])
        self.assertIsNone(reconcile_file_links(txt))
        self.assertEqual(FileLink.objects.filter(source=txt).count(), 0)


class BackfillFileLinksCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="backfill-links", password="p")

    def test_backfill_populates_existing_links(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        self.assertEqual(FileLink.objects.count(), 0)
        call_command("backfill_file_links")
        self.assertEqual(
            set(FileLink.objects.filter(source=a).values_list("target_id", flat=True)),
            {b.uuid},
        )

    def test_backfill_is_idempotent(self):
        b = _make_markdown(self.user, "B.md", "# B")
        _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        call_command("backfill_file_links")
        call_command("backfill_file_links")
        self.assertEqual(FileLink.objects.count(), 1)
