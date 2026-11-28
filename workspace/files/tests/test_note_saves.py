"""What an editor's save of a Markdown note costs: no content detection, one
timeline entry per writing session, and processing once the editing settles."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from workspace.common.search import fts5_available
from workspace.files.models import File, FileEvent, FileLink
from workspace.files.search import search_files
from workspace.files.services import detection, tree
from workspace.files.services.content_writes import update_content
from workspace.files.services.events import SETTLE_SECONDS
from workspace.files.tasks import run_file_event_handlers

User = get_user_model()


def _note(owner, name="note.md", body="# Note\n\nFirst paragraph.\n"):
    return tree.create_file(
        owner=owner,
        name=name,
        content=ContentFile(body.encode(), name=name),
        mime_type="text/markdown",
        acting_user=owner,
    )


def _save(file_obj, body, user, *, edit=True):
    return update_content(
        file_obj,
        ContentFile(body if isinstance(body, bytes) else body.encode(), name="x"),
        name=file_obj.name,
        acting_user=user,
        edit=edit,
    )


class NoteSaveDetectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw")
        self.note = _note(self.user)
        self.assertEqual(self.note.type, "markdown")

    def _detects(self, save):
        with patch.object(
            detection, "detect_from_stream", wraps=detection.detect_from_stream
        ) as detect:
            save()
        return detect.called

    def test_an_editor_save_of_a_note_runs_no_content_detection(self):
        self.assertFalse(self._detects(lambda: _save(self.note, "# Tiny\n", self.user)))
        self.note.refresh_from_db()
        self.assertEqual(
            (self.note.type, self.note.mime_type, self.note.category),
            ("markdown", "text/markdown", "text"),
        )

    def test_a_write_from_anything_but_an_editor_is_detected(self):
        self.assertTrue(
            self._detects(lambda: _save(self.note, "# Tiny\n", self.user, edit=False))
        )

    def test_a_save_that_is_not_utf8_text_is_detected(self):
        self.assertTrue(
            self._detects(lambda: _save(self.note, b"\x89PNG\r\n\x1a\n\xff", self.user))
        )

    def test_a_note_renamed_away_from_markdown_is_detected(self):
        File.objects.filter(pk=self.note.pk).update(name="note.txt")
        self.note.refresh_from_db()
        self.assertTrue(self._detects(lambda: _save(self.note, "plain\n", self.user)))

    def test_an_editor_save_of_another_text_file_is_detected(self):
        text = tree.create_file(
            owner=self.user,
            name="todo.txt",
            content=ContentFile(b"milk\n", name="todo.txt"),
        )
        self.assertTrue(self._detects(lambda: _save(text, "eggs\n", self.user)))


class NoteSaveThroughTheApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw")
        self.client.force_authenticate(self.user)
        self.note = _note(self.user)

    def test_the_editor_patch_runs_no_content_detection(self):
        with patch.object(detection, "detect_from_stream") as detect:
            response = self.client.patch(
                f"/api/v1/files/{self.note.uuid}",
                {
                    "content": SimpleUploadedFile(
                        "note.md", b"# Saved\n", content_type="text/markdown"
                    ),
                    "base_hash": self.note.content_hash,
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, 200)
        detect.assert_not_called()
        self.note.refresh_from_db()
        self.assertEqual(self.note.type, "markdown")


class NoteSaveEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw")
        self.other = User.objects.create_user(username="coeditor", password="pw")
        self.note = _note(self.user)

    def _replaced(self):
        return FileEvent.objects.filter(
            file=self.note, action=FileEvent.Action.CONTENT_REPLACED
        )

    def test_ten_saves_in_a_minute_leave_one_event(self):
        for n in range(10):
            _save(self.note, f"# Draft {n}\n", self.user)

        self.assertEqual(self._replaced().count(), 1)

    def _age_events(self, by):
        """Move every event of the note back by *by*, keeping their order."""
        for event in FileEvent.objects.filter(file=self.note):
            FileEvent.objects.filter(pk=event.pk).update(
                created_at=event.created_at - by
            )

    def test_the_event_moves_to_the_last_save(self):
        _save(self.note, "# One\n", self.user)
        self._age_events(timedelta(minutes=2))
        aged = self._replaced().get().created_at

        _save(self.note, "# Two\n", self.user)

        renewed = self._replaced().get()
        self.assertGreater(renewed.created_at, aged + timedelta(minutes=1))
        self.note.refresh_from_db()
        self.assertEqual(self.note.last_event_at, renewed.created_at)

    def test_a_save_by_someone_else_adds_an_event(self):
        _save(self.note, "# Mine\n", self.user)
        _save(self.note, "# Theirs\n", self.other)

        self.assertEqual(
            list(self._replaced().values_list("actor__username", flat=True)),
            ["coeditor", "writer"],
        )

    def test_a_save_past_the_window_adds_an_event(self):
        _save(self.note, "# Morning\n", self.user)
        self._age_events(timedelta(minutes=6))

        _save(self.note, "# Afternoon\n", self.user)

        self.assertEqual(self._replaced().count(), 2)

    def test_another_event_in_between_starts_a_new_one(self):
        _save(self.note, "# Before\n", self.user)
        tree.rename(self.note, "renamed.md", acting_user=self.user)
        _save(self.note, "# After\n", self.user)

        self.assertEqual(self._replaced().count(), 2)

    def test_uploads_coalesce_as_well(self):
        _save(self.note, "# One\n", self.user, edit=False)
        _save(self.note, "# Two\n", self.user, edit=False)

        self.assertEqual(self._replaced().count(), 1)


class NoteSaveDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw")
        self.note = _note(self.user)

    def _dispatches(self, *bodies, edit=True):
        with (
            patch.object(run_file_event_handlers, "apply_async") as later,
            patch.object(run_file_event_handlers, "delay") as now,
        ):
            for body in bodies:
                with self.captureOnCommitCallbacks(execute=True):
                    _save(self.note, body, self.user, edit=edit)
        return later, now

    def test_an_editor_save_queues_its_handlers_once_editing_settles(self):
        later, now = self._dispatches("# Saved\n")

        now.assert_not_called()
        self.note.refresh_from_db()
        event = FileEvent.objects.get(
            file=self.note, action=FileEvent.Action.CONTENT_REPLACED
        )
        later.assert_called_once_with(
            (str(event.uuid),),
            {"content_hash": self.note.content_hash},
            countdown=SETTLE_SECONDS,
        )

    def test_an_upload_queues_its_handlers_at_once(self):
        later, now = self._dispatches("# Uploaded\n", edit=False)

        later.assert_not_called()
        now.assert_called_once()

    def test_ten_saves_run_the_pipeline_once(self):
        later, _ = self._dispatches(*(f"# Draft {n}\n" for n in range(10)))
        self.assertEqual(later.call_count, 10)

        with (
            patch("workspace.files.tasks.process_file.apply_async") as pipeline,
            patch("workspace.files.tasks.index_search_document.apply_async") as index,
        ):
            for call in later.call_args_list:
                run_file_event_handlers(*call.args[0], **call.args[1])

        pipeline.assert_called_once()
        index.assert_called_once()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class NoteSaveFinalContentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="writer", password="pw")
        self.first = _note(self.user, "First.md")
        self.second = _note(self.user, "Second.md")
        self.note = _note(self.user, "Draft.md")

    def test_links_and_search_follow_the_last_save(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        with patch.object(run_file_event_handlers, "apply_async") as later:
            for body in (
                f"aardvark [First](/notes?file={self.first.uuid})",
                f"zebra [Second](/notes?file={self.second.uuid})",
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    _save(self.note, body, self.user)

        for call in later.call_args_list:
            with self.captureOnCommitCallbacks(execute=True):
                run_file_event_handlers(*call.args[0], **call.args[1])

        self.assertEqual(
            set(
                FileLink.objects.filter(source=self.note).values_list(
                    "target_id", flat=True
                )
            ),
            {self.second.uuid},
        )
        self.assertEqual(
            [f.name for f in search_files("zebra", self.user, limit=10)], ["Draft.md"]
        )
        self.assertEqual(search_files("aardvark", self.user, limit=10), [])
