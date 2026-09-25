import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.files.models import File, FileLink, FileLinkState
from workspace.files.services import FileService
from workspace.files.services.catch_up import get_catch_up
from workspace.files.services.links import (
    EXTRACTOR_VERSION,
    extract_link_targets,
    pending_links_qs,
    reconcile_file_links,
    refresh_file_links,
)

from .catch_up import run_catch_up

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


class FileLinksCatchUpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="backfill-links", password="p")

    def _pending(self, **kwargs):
        return set(pending_links_qs(**kwargs).values_list("pk", flat=True))

    def test_registered_with_the_catch_up(self):
        self.assertIs(get_catch_up("file_links").process, refresh_file_links)

    def test_populates_existing_links_and_is_idempotent(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        self.assertEqual(FileLink.objects.count(), 0)

        self.assertEqual(run_catch_up("file_links"), 2)

        self.assertEqual(
            set(FileLink.objects.filter(source=a).values_list("target_id", flat=True)),
            {b.uuid},
        )
        self.assertEqual(run_catch_up("file_links"), 0)
        self.assertEqual(FileLink.objects.count(), 1)

    def test_a_reconciled_note_is_pending_again_once_its_content_changes(self):
        a = _make_markdown(self.user, "A.md", "# A")
        reconcile_file_links(a)
        self.assertEqual(self._pending(), set())
        self.assertEqual(self._pending(reanalyze=True), {a.pk})

        File.objects.filter(pk=a.pk).update(content_hash="f" * 64)

        self.assertEqual(self._pending(), {a.pk})

    def test_a_newer_extractor_makes_every_note_pending(self):
        a = _make_markdown(self.user, "A.md", "# A")
        reconcile_file_links(a)

        with mock.patch(
            "workspace.files.services.links.EXTRACTOR_VERSION", EXTRACTOR_VERSION + 1
        ):
            self.assertEqual(self._pending(), {a.pk})

    def test_other_files_are_never_pending(self):
        FileService.create_file(
            owner=self.user,
            name="note.txt",
            content=ContentFile(b"text", name="note.txt"),
            mime_type="text/plain",
        )
        self.assertEqual(self._pending(), set())

    def test_an_unreadable_note_records_no_state(self):
        a = _make_markdown(self.user, "A.md", "# A")
        a.content.storage.delete(a.content.name)

        self.assertFalse(refresh_file_links(a))
        self.assertFalse(FileLinkState.objects.exists())
        self.assertEqual(self._pending(), {a.pk})
