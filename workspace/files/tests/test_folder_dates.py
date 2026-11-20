from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services import content_writes, trash, tree
from workspace.files.services.transfers import OnConflict, move_item

User = get_user_model()


def _file(owner, name, parent, body=b"bytes"):
    return tree.create_file(
        owner, name, parent=parent, content=ContentFile(body, name=name)
    )


class FolderDateTests(TestCase):
    """A folder's ``updated_at`` follows its list of entries, not their bytes."""

    def setUp(self):
        self.user = User.objects.create_user(username="dana", password="x")
        self.outer = tree.create_folder(self.user, "Outer")
        self.folder = tree.create_folder(self.user, "Folder", parent=self.outer)
        self.other = tree.create_folder(self.user, "Other", parent=self.outer)

    def tearDown(self):
        cache.clear()

    def _age(self, *folders):
        """Date *folders* a day back, so a change of date is unambiguous."""
        self.long_ago = timezone.now() - timedelta(days=1)
        File.objects.filter(pk__in=[f.pk for f in folders]).update(
            updated_at=self.long_ago
        )

    def assertTouched(self, folder):
        folder.refresh_from_db()
        self.assertGreater(folder.updated_at, self.long_ago)

    def assertUntouched(self, folder):
        folder.refresh_from_db()
        self.assertEqual(folder.updated_at, self.long_ago)

    def test_upload_dates_the_folder_but_not_its_ancestors(self):
        self._age(self.outer, self.folder)
        _file(self.user, "report.pdf", self.folder)
        self.assertTouched(self.folder)
        self.assertUntouched(self.outer)

    def test_new_subfolder_dates_the_folder(self):
        self._age(self.folder)
        tree.create_folder(self.user, "Sub", parent=self.folder)
        self.assertTouched(self.folder)

    def test_copy_into_a_folder_dates_it(self):
        source = _file(self.user, "a.txt", self.other)
        self._age(self.folder, self.other)
        tree.copy(source, self.folder, self.user)
        self.assertTouched(self.folder)
        self.assertUntouched(self.other)

    def test_trashing_an_entry_dates_its_folder(self):
        node = _file(self.user, "a.txt", self.folder)
        self._age(self.folder)
        trash.trash_item(self.user, node)
        self.assertTouched(self.folder)

    def test_purging_a_trashed_entry_leaves_its_folder_alone(self):
        node = _file(self.user, "a.txt", self.folder)
        trash.trash_item(self.user, node)
        self._age(self.folder)
        trash.purge_item(self.user, node)
        self.assertUntouched(self.folder)

    def test_restoring_an_entry_dates_its_folder(self):
        node = _file(self.user, "a.txt", self.folder)
        trash.trash_item(self.user, node)
        self._age(self.folder)
        node.refresh_from_db()
        trash.restore_item(self.user, node)
        self.assertTouched(self.folder)

    def test_restoring_from_a_trashed_folder_dates_every_folder_it_reappears_in(self):
        node = _file(self.user, "a.txt", self.folder)
        trash.trash_item(self.user, self.folder)
        self._age(self.outer, self.folder)
        node.refresh_from_db()
        trash.restore_item(self.user, node)
        # The folder is back in Outer, and the file back in the folder.
        self.assertTouched(self.outer)
        self.assertTouched(self.folder)

    def test_renaming_an_entry_dates_its_folder(self):
        node = _file(self.user, "a.txt", self.folder)
        self._age(self.folder)
        tree.rename(node, "b.txt", acting_user=self.user)
        self.assertTouched(self.folder)

    def test_moving_an_entry_dates_both_folders(self):
        node = _file(self.user, "a.txt", self.folder)
        self._age(self.outer, self.folder, self.other)
        move_item(self.user, node, self.other)
        self.assertTouched(self.folder)
        self.assertTouched(self.other)
        self.assertUntouched(self.outer)

    def test_moving_onto_a_same_name_file_dates_only_the_folder_it_left(self):
        moved = _file(self.user, "a.txt", self.folder, body=b"new")
        _file(self.user, "a.txt", self.other, body=b"old")
        self._age(self.folder, self.other)
        move_item(self.user, moved, self.other, OnConflict.REPLACE)
        self.assertTouched(self.folder)
        # Other holds the same entries; only the replaced file's bytes changed.
        self.assertUntouched(self.other)

    def test_writing_a_file_leaves_its_folder_alone(self):
        node = _file(self.user, "a.txt", self.folder)
        self._age(self.folder)
        content_writes.update_content(
            node, ContentFile(b"edited", name="a.txt"), acting_user=self.user
        )
        self.assertUntouched(self.folder)


class RecentViewTests(TestCase):
    """The Recent view lists files: a folder dated by its entries is not one."""

    def setUp(self):
        self.user = User.objects.create_user(username="erin", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_folder_receiving_an_upload_stays_out_of_recent(self):
        folder = tree.create_folder(self.user, "Quarterlyfolder")
        _file(self.user, "uploaded-report.pdf", folder)
        response = self.client.get("/files?recent=1")
        self.assertContains(response, "uploaded-report.pdf")
        self.assertNotContains(response, "Quarterlyfolder")
