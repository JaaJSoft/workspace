"""Unit tests for FileService."""

from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


class TestInferMimeType(TestCase):
    """Tests for FileService.infer_mime_type()."""

    def test_from_uploaded_content_type(self):
        uploaded = MagicMock()
        uploaded.content_type = 'image/png'
        result = FileService.infer_mime_type('photo.jpg', uploaded=uploaded)
        self.assertEqual(result, 'image/png')

    def test_ignores_generic_octet_stream_from_upload(self):
        uploaded = MagicMock()
        uploaded.content_type = 'application/octet-stream'
        uploaded.name = None
        result = FileService.infer_mime_type('report.pdf', uploaded=uploaded)
        self.assertEqual(result, 'application/pdf')

    def test_from_filename(self):
        result = FileService.infer_mime_type('style.css')
        self.assertEqual(result, 'text/css')

    def test_fallback_to_octet_stream(self):
        result = FileService.infer_mime_type('noext')
        self.assertEqual(result, 'application/octet-stream')

    def test_from_filename_when_no_upload(self):
        result = FileService.infer_mime_type('data.json')
        self.assertEqual(result, 'application/json')

    def test_uploaded_with_no_content_type_falls_back_to_filename(self):
        uploaded = MagicMock()
        uploaded.content_type = None
        uploaded.name = None
        result = FileService.infer_mime_type('image.gif', uploaded=uploaded)
        self.assertEqual(result, 'image/gif')

    def test_none_filename_with_upload_octet_stream(self):
        uploaded = MagicMock()
        uploaded.content_type = 'application/octet-stream'
        uploaded.name = None
        result = FileService.infer_mime_type(None, uploaded=uploaded)
        self.assertEqual(result, 'application/octet-stream')


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestCreateFile(TestCase):
    """Tests for FileService.create_file()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser', email='svc@test.com', password='pass'
        )

    def test_basic_creation(self):
        f = FileService.create_file(self.user, 'readme.txt', mime_type='text/plain')
        self.assertEqual(f.name, 'readme.txt')
        self.assertEqual(f.node_type, File.NodeType.FILE)
        self.assertEqual(f.owner, self.user)
        self.assertIsNone(f.size)

    def test_creation_with_content(self):
        content = ContentFile(b'hello world', name='hello.txt')
        f = FileService.create_file(self.user, 'hello.txt', content=content)
        self.assertEqual(f.size, 11)
        self.assertTrue(f.content)

    def test_mime_auto_detection(self):
        content = ContentFile(b'{}', name='data.json')
        f = FileService.create_file(self.user, 'data.json', content=content)
        self.assertIn('json', f.mime_type)

    def test_explicit_mime_overrides(self):
        content = ContentFile(b'data', name='file.bin')
        f = FileService.create_file(
            self.user, 'file.bin', content=content, mime_type='application/custom'
        )
        self.assertEqual(f.mime_type, 'application/custom')

    def test_creation_under_parent(self):
        folder = FileService.create_folder(self.user, 'docs')
        f = FileService.create_file(self.user, 'note.txt', parent=folder, mime_type='text/plain')
        self.assertEqual(f.parent, folder)


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestCreateFolder(TestCase):
    """Tests for FileService.create_folder()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser2', email='svc2@test.com', password='pass'
        )

    def test_basic_creation(self):
        folder = FileService.create_folder(self.user, 'Photos')
        self.assertEqual(folder.name, 'Photos')
        self.assertEqual(folder.node_type, File.NodeType.FOLDER)
        self.assertEqual(folder.owner, self.user)

    def test_nested_folder(self):
        parent = FileService.create_folder(self.user, 'Root')
        child = FileService.create_folder(self.user, 'Sub', parent=parent)
        self.assertEqual(child.parent, parent)
        self.assertEqual(child.path, 'Root/Sub')

    def test_with_icon_and_color(self):
        folder = FileService.create_folder(
            self.user, 'Work', icon='briefcase', color='#ff0000'
        )
        self.assertEqual(folder.icon, 'briefcase')
        self.assertEqual(folder.color, '#ff0000')


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestRegisterDiskFile(TestCase):
    """Tests for FileService.register_disk_file()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser3', email='svc3@test.com', password='pass'
        )

    def test_sets_content_name_directly(self):
        f = FileService.register_disk_file(
            self.user, 'report.pdf', None,
            'files/svcuser3/report.pdf',
            mime_type='application/pdf', size=1024,
        )
        self.assertEqual(f.content.name, 'files/svcuser3/report.pdf')
        self.assertEqual(f.size, 1024)
        self.assertEqual(f.mime_type, 'application/pdf')

    def test_infers_mime_when_not_provided(self):
        f = FileService.register_disk_file(
            self.user, 'photo.jpg', None, 'files/svcuser3/photo.jpg',
        )
        self.assertIn('image', f.mime_type)


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestRename(TestCase):
    """Tests for FileService.rename()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser4', email='svc4@test.com', password='pass'
        )

    def test_rename_file_updates_name(self):
        content = ContentFile(b'data', name='old.txt')
        f = FileService.create_file(self.user, 'old.txt', content=content)
        FileService.rename(f, 'new.txt')
        f.refresh_from_db()
        self.assertEqual(f.name, 'new.txt')

    def test_rename_noop_when_same_name(self):
        f = FileService.create_file(self.user, 'same.txt', mime_type='text/plain')
        result = FileService.rename(f, 'same.txt')
        self.assertEqual(result.name, 'same.txt')

    def test_rename_folder_updates_name(self):
        folder = FileService.create_folder(self.user, 'OldFolder')
        FileService.rename(folder, 'NewFolder')
        folder.refresh_from_db()
        self.assertEqual(folder.name, 'NewFolder')


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestUpdateContent(TestCase):
    """Tests for FileService.update_content()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser5', email='svc5@test.com', password='pass'
        )

    def test_replaces_content(self):
        original = ContentFile(b'old', name='file.txt')
        f = FileService.create_file(self.user, 'file.txt', content=original)
        self.assertEqual(f.size, 3)

        new_content = ContentFile(b'new content here', name='file.txt')
        FileService.update_content(f, new_content)
        f.refresh_from_db()
        self.assertEqual(f.size, 16)

    def test_updates_mime_type(self):
        original = ContentFile(b'text', name='file.txt')
        f = FileService.create_file(self.user, 'file.txt', content=original)

        new_content = ContentFile(b'{}', name='file.json')
        FileService.update_content(f, new_content, name='file.json')
        f.refresh_from_db()
        self.assertIn('json', f.mime_type)


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestCopy(TestCase):
    """Tests for FileService.copy()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser6', email='svc6@test.com', password='pass'
        )

    def test_copy_file(self):
        content = ContentFile(b'hello', name='doc.txt')
        original = FileService.create_file(
            self.user, 'doc.txt', content=content, mime_type='text/plain'
        )
        copied = FileService.copy(original, None, self.user)
        self.assertNotEqual(copied.pk, original.pk)
        self.assertEqual(copied.name, 'doc (Copy).txt')
        self.assertEqual(copied.size, original.size)

    def test_copy_file_to_different_folder_keeps_name(self):
        content = ContentFile(b'hello', name='doc.txt')
        original = FileService.create_file(self.user, 'doc.txt', content=content)
        target = FileService.create_folder(self.user, 'Other')
        copied = FileService.copy(original, target, self.user)
        self.assertEqual(copied.name, 'doc.txt')
        self.assertEqual(copied.parent, target)

    def test_copy_folder_recursive(self):
        folder = FileService.create_folder(self.user, 'Src')
        content = ContentFile(b'data', name='child.txt')
        FileService.create_file(self.user, 'child.txt', parent=folder, content=content)
        sub = FileService.create_folder(self.user, 'SubDir', parent=folder)
        FileService.create_file(
            self.user, 'deep.txt', parent=sub,
            content=ContentFile(b'deep', name='deep.txt'),
        )

        copied = FileService.copy(folder, None, self.user)
        self.assertEqual(copied.name, 'Src (Copy)')
        # Check children were copied
        children = File.objects.filter(parent=copied, deleted_at__isnull=True)
        self.assertEqual(children.count(), 2)
        child_names = set(children.values_list('name', flat=True))
        self.assertIn('child.txt', child_names)
        self.assertIn('SubDir', child_names)

    def test_copy_name_conflict_increments(self):
        FileService.create_folder(self.user, 'A')
        original = FileService.create_folder(self.user, 'A')  # same name allowed for folders
        # Create conflict
        FileService.create_folder(self.user, 'A (Copy)')

        # The copy should be "A (Copy 2)"
        copied = FileService.copy(original, None, self.user)
        self.assertEqual(copied.name, 'A (Copy 2)')


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestCheckNameAvailable(TestCase):
    """Tests for FileService.check_name_available()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser7', email='svc7@test.com', password='pass'
        )

    def test_raises_on_duplicate_file(self):
        FileService.create_file(self.user, 'doc.txt', mime_type='text/plain')
        with self.assertRaises(ValueError):
            FileService.check_name_available(
                self.user, None, 'doc.txt', File.NodeType.FILE,
            )

    def test_case_insensitive(self):
        FileService.create_file(self.user, 'Doc.TXT', mime_type='text/plain')
        with self.assertRaises(ValueError):
            FileService.check_name_available(
                self.user, None, 'doc.txt', File.NodeType.FILE,
            )

    def test_excludes_self_on_update(self):
        f = FileService.create_file(self.user, 'doc.txt', mime_type='text/plain')
        # Should not raise when excluding self
        FileService.check_name_available(
            self.user, None, 'doc.txt', File.NodeType.FILE, exclude_pk=f.pk,
        )

    def test_ignores_soft_deleted(self):
        f = FileService.create_file(self.user, 'deleted.txt', mime_type='text/plain')
        f.soft_delete()
        # Should not raise since original is soft-deleted
        FileService.check_name_available(
            self.user, None, 'deleted.txt', File.NodeType.FILE,
        )

    def test_no_check_for_folders(self):
        FileService.create_folder(self.user, 'MyFolder')
        # Should not raise for folders (check only applies to files)
        FileService.check_name_available(
            self.user, None, 'MyFolder', File.NodeType.FOLDER,
        )


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class TestValidateMoveTarget(TestCase):
    """Tests for FileService.validate_move_target()."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser8', email='svc8@test.com', password='pass'
        )
        self.other_user = User.objects.create_user(
            username='otheruser', email='other@test.com', password='pass'
        )

    def test_move_to_none_is_valid(self):
        folder = FileService.create_folder(self.user, 'A')
        FileService.validate_move_target(folder, None)  # should not raise

    def test_cannot_move_into_self(self):
        folder = FileService.create_folder(self.user, 'A')
        with self.assertRaises(ValueError):
            FileService.validate_move_target(folder, folder)

    def test_cannot_move_into_descendant(self):
        parent = FileService.create_folder(self.user, 'Parent')
        child = FileService.create_folder(self.user, 'Child', parent=parent)
        grandchild = FileService.create_folder(self.user, 'GrandChild', parent=child)
        with self.assertRaises(ValueError):
            FileService.validate_move_target(parent, grandchild)

    def test_cannot_move_to_other_user_folder(self):
        folder = FileService.create_folder(self.user, 'Mine')
        other_folder = FileService.create_folder(self.other_user, 'Theirs')
        with self.assertRaises(ValueError):
            FileService.validate_move_target(folder, other_folder)

    def test_move_file_to_valid_folder(self):
        f = FileService.create_file(self.user, 'doc.txt', mime_type='text/plain')
        target = FileService.create_folder(self.user, 'Target')
        FileService.validate_move_target(f, target)  # should not raise
