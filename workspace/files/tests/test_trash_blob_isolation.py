"""A file in the trash keeps its own bytes, whatever happens to its name.

The storage layout mirrors the tree while every name-uniqueness predicate
ignores trashed rows, so a trashed file left in its folder kept a name the
app considered free: the next file to claim it resolved to the same path,
and the storage allows overwrite, so the trashed bytes were truncated.
Restoring returned the wrong content, silently.

Every assertion here reads the bytes back off storage on purpose. An
assertion on ``content.name`` alone passes against the buggy version - the
two rows agreeing on a path is exactly the bug.
"""

import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()

TRASHED = b"ORIGINAL-IN-TRASH"
LIVE = b"BRAND-NEW"


class TrashedBlobIsolationTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self._override = self.settings(MEDIA_ROOT=self.media_root)
        self._override.enable()
        self.user = User.objects.create_user(username="trasher", password="pass")

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _make(self, name, content, parent=None):
        return FileService.create_file(
            self.user,
            name,
            parent=parent,
            content=ContentFile(content, name=name),
        )

    def _trashed(self, name="report.pdf", content=TRASHED, parent=None):
        f = self._make(name, content, parent=parent)
        FileService.soft_delete(f, acting_user=self.user)
        return f

    def _bytes_of(self, file_obj):
        file_obj.refresh_from_db()
        with file_obj.content.open("rb") as fh:
            return fh.read()

    def test_creating_a_file_named_like_a_trashed_one_spares_its_bytes(self):
        trashed = self._trashed()
        live = self._make("report.pdf", LIVE)

        FileService.restore(trashed, acting_user=self.user)

        self.assertEqual(self._bytes_of(trashed), TRASHED)
        self.assertEqual(self._bytes_of(live), LIVE)

    def test_renaming_a_file_onto_a_trashed_name_spares_its_bytes(self):
        trashed = self._trashed()
        live = self._make("draft.pdf", LIVE)

        FileService.rename(live, "report.pdf", acting_user=self.user)
        FileService.restore(trashed, acting_user=self.user)

        self.assertEqual(self._bytes_of(trashed), TRASHED)
        self.assertEqual(self._bytes_of(live), LIVE)

    def test_moving_a_file_next_to_a_trashed_namesake_spares_its_bytes(self):
        folder = FileService.create_folder(self.user, "Docs")
        trashed = self._trashed(parent=folder)
        live = self._make("report.pdf", LIVE)

        FileService.move(live, folder, acting_user=self.user)
        FileService.restore(trashed, acting_user=self.user)

        self.assertEqual(self._bytes_of(trashed), TRASHED)
        self.assertEqual(self._bytes_of(live), LIVE)

    def test_copying_a_file_onto_a_trashed_name_spares_its_bytes(self):
        folder = FileService.create_folder(self.user, "Docs")
        trashed = self._trashed(parent=folder)
        source = self._make("report.pdf", LIVE)

        copied = FileService.copy(source, folder, self.user, acting_user=self.user)
        FileService.restore(trashed, acting_user=self.user)

        self.assertEqual(self._bytes_of(trashed), TRASHED)
        self.assertEqual(self._bytes_of(copied), LIVE)

    def test_replacing_the_content_of_a_namesake_spares_the_trashed_bytes(self):
        trashed = self._trashed()
        live = self._make("report.pdf", b"placeholder")

        FileService.update_content(
            live, ContentFile(LIVE, name="report.pdf"), acting_user=self.user
        )
        FileService.restore(trashed, acting_user=self.user)

        self.assertEqual(self._bytes_of(trashed), TRASHED)
        self.assertEqual(self._bytes_of(live), LIVE)

    def test_a_file_trashed_inside_a_trashed_folder_survives_the_round_trip(self):
        folder = FileService.create_folder(self.user, "Docs")
        inner = self._make("report.pdf", TRASHED, parent=folder)
        FileService.soft_delete(folder, acting_user=self.user)

        # The name is free again, so a new Docs/report.pdf can be created.
        new_folder = FileService.create_folder(self.user, "Docs")
        rival = self._make("report.pdf", LIVE, parent=new_folder)

        FileService.restore(folder, acting_user=self.user)

        self.assertEqual(self._bytes_of(inner), TRASHED)
        self.assertEqual(self._bytes_of(rival), LIVE)

    def test_two_files_trashed_under_the_same_name_keep_their_own_bytes(self):
        first = self._trashed(content=b"FIRST")
        second = self._trashed(content=b"SECOND")

        self.assertEqual(self._bytes_of(first), b"FIRST")
        self.assertEqual(self._bytes_of(second), b"SECOND")

    def test_no_two_rows_share_a_storage_path(self):
        folder = FileService.create_folder(self.user, "Docs")
        self._trashed()
        self._trashed()
        self._trashed(parent=folder)
        self._make("report.pdf", LIVE)
        self._make("report.pdf", LIVE, parent=folder)
        other = User.objects.create_user(username="stranger", password="pass")
        FileService.create_file(
            other, "report.pdf", content=ContentFile(LIVE, name="report.pdf")
        )

        paths = list(
            File.objects.filter(node_type=File.NodeType.FILE).values_list(
                "content", flat=True
            )
        )
        self.assertEqual(len(paths), 6)
        self.assertEqual(len(set(paths)), len(paths))

    def test_purging_the_trashed_file_leaves_the_live_one_intact(self):
        trashed = self._trashed()
        live = self._make("report.pdf", LIVE)

        FileService.hard_delete(trashed, acting_user=self.user)

        self.assertEqual(self._bytes_of(live), LIVE)
        self.assertTrue(
            os.path.isfile(os.path.join(self.media_root, live.content.name))
        )
