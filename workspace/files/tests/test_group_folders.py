"""Tests for group folders feature."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.ui.views import build_breadcrumbs

User = get_user_model()


class GroupFolderModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.user.groups.add(self.group)

    def test_file_has_group_field(self):
        folder = File.objects.create(
            owner=self.user,
            name='Marketing Files',
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        self.assertEqual(folder.group, self.group)
        self.assertEqual(folder.group_id, self.group.id)

    def test_file_group_defaults_to_none(self):
        folder = File.objects.create(
            owner=self.user,
            name='Personal',
            node_type=File.NodeType.FOLDER,
        )
        self.assertIsNone(folder.group)

    def test_unique_group_root_folder(self):
        """Only one active root folder per group."""
        File.objects.create(
            owner=self.user,
            name='Marketing Files',
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        with self.assertRaises(IntegrityError):
            File.objects.create(
                owner=self.user,
                name='Marketing Files 2',
                node_type=File.NodeType.FOLDER,
                group=self.group,
            )

    def test_unique_constraint_allows_soft_deleted(self):
        """Can create new root after soft-deleting the old one."""
        old = File.objects.create(
            owner=self.user,
            name='Marketing Files',
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        old.soft_delete()
        new = File.objects.create(
            owner=self.user,
            name='Marketing Files v2',
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        self.assertEqual(new.group, self.group)

    def test_unique_constraint_allows_child_folders(self):
        """Child folders with same group are allowed (not root)."""
        root = File.objects.create(
            owner=self.user,
            name='Marketing Files',
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        child = File.objects.create(
            owner=self.user,
            name='Subfolder',
            node_type=File.NodeType.FOLDER,
            parent=root,
            group=self.group,
        )
        self.assertEqual(child.group, self.group)


class GroupStoragePathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.user.groups.add(self.group)
        self.root = File.objects.create(
            owner=self.user,
            name='Marketing Files',
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )

    def test_folder_storage_path_for_group_folder(self):
        path = FileService._folder_storage_path(self.root)
        self.assertEqual(path, 'files/groups/Marketing/Marketing Files')

    def test_folder_storage_path_for_group_subfolder(self):
        sub = File.objects.create(
            owner=self.user,
            name='Reports',
            node_type=File.NodeType.FOLDER,
            parent=self.root,
            group=self.group,
        )
        path = FileService._folder_storage_path(sub)
        self.assertEqual(path, 'files/groups/Marketing/Marketing Files/Reports')

    def test_folder_storage_path_personal_unchanged(self):
        personal = File.objects.create(
            owner=self.user,
            name='Personal',
            node_type=File.NodeType.FOLDER,
        )
        path = FileService._folder_storage_path(personal)
        self.assertEqual(path, 'files/users/alice/Personal')

    def test_file_upload_path_for_group_file(self):
        from workspace.files.models import file_upload_path

        f = File(
            owner=self.user,
            name='report.pdf',
            node_type=File.NodeType.FILE,
            parent=self.root,
            group=self.group,
        )
        f.path = 'Marketing Files/report.pdf'
        result = file_upload_path(f, 'report.pdf')
        self.assertEqual(result, 'files/groups/Marketing/Marketing Files/report.pdf')

    def test_file_upload_path_personal_unchanged(self):
        from workspace.files.models import file_upload_path

        f = File(
            owner=self.user,
            name='notes.txt',
            node_type=File.NodeType.FILE,
        )
        f.path = 'notes.txt'
        result = file_upload_path(f, 'notes.txt')
        self.assertEqual(result, 'files/users/alice/notes.txt')


class GroupDeletionSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.user.groups.add(self.group)

    def test_deleting_group_soft_deletes_all_group_files(self):
        root = File.objects.create(
            owner=self.user, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )
        child = File.objects.create(
            owner=self.user, name='report.txt', node_type=File.NodeType.FILE,
            parent=root, group=self.group, mime_type='text/plain',
        )
        self.group.delete()

        root.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNotNone(root.deleted_at)
        self.assertIsNotNone(child.deleted_at)
        # group FK set to NULL via SET_NULL
        self.assertIsNone(root.group_id)
        self.assertIsNone(child.group_id)

    def test_deleting_group_does_not_affect_personal_files(self):
        personal = File.objects.create(
            owner=self.user, name='Personal', node_type=File.NodeType.FOLDER,
        )
        self.group.delete()
        personal.refresh_from_db()
        self.assertIsNone(personal.deleted_at)


class GroupAccessControlTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.outsider = User.objects.create_user(username='outsider', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        self.root = File.objects.create(
            owner=self.alice, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )
        self.group_file = File.objects.create(
            owner=self.alice, name='report.txt', node_type=File.NodeType.FILE,
            parent=self.root, group=self.group, mime_type='text/plain',
        )

    def test_can_access_group_file_as_member(self):
        self.assertTrue(FileService.can_access(self.bob, self.group_file))

    def test_can_access_group_folder_as_member(self):
        self.assertTrue(FileService.can_access(self.bob, self.root))

    def test_cannot_access_group_file_as_outsider(self):
        self.assertFalse(FileService.can_access(self.outsider, self.group_file))

    def test_can_access_group_file_as_owner(self):
        self.assertTrue(FileService.can_access(self.alice, self.group_file))

    def test_user_group_files_qs_returns_group_files(self):
        qs = FileService.user_group_files_qs(self.bob)
        self.assertIn(self.root, qs)
        self.assertIn(self.group_file, qs)

    def test_user_group_files_qs_excludes_non_member(self):
        qs = FileService.user_group_files_qs(self.outsider)
        self.assertEqual(qs.count(), 0)

    def test_user_files_qs_excludes_group_files(self):
        personal = File.objects.create(
            owner=self.bob, name='personal.txt', node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        qs = FileService.user_files_qs(self.bob)
        self.assertIn(personal, qs)
        self.assertNotIn(self.group_file, qs)


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class GroupPropagationOnCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.user.groups.add(self.group)
        self.root = File.objects.create(
            owner=self.user, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )

    def test_create_file_inherits_group_from_parent(self):
        f = FileService.create_file(self.user, 'report.txt', parent=self.root)
        self.assertEqual(f.group_id, self.group.id)

    def test_create_folder_inherits_group_from_parent(self):
        sub = FileService.create_folder(self.user, 'Reports', parent=self.root)
        self.assertEqual(sub.group_id, self.group.id)

    def test_create_file_no_group_when_personal(self):
        f = FileService.create_file(self.user, 'personal.txt')
        self.assertIsNone(f.group_id)

    def test_create_folder_explicit_group_for_root(self):
        group2 = Group.objects.create(name='Sales')
        root2 = FileService.create_folder(self.user, 'Sales', group=group2)
        self.assertEqual(root2.group_id, group2.id)

    def test_nested_creation_propagates_group(self):
        sub = FileService.create_folder(self.user, 'Sub', parent=self.root)
        f = FileService.create_file(self.user, 'deep.txt', parent=sub)
        self.assertEqual(f.group_id, self.group.id)


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.InMemoryStorage')
class GroupMoveTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        self.group_root = File.objects.create(
            owner=self.alice, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )

    def test_propagate_group_sets_group_on_descendants(self):
        folder = File.objects.create(
            owner=self.alice, name='Folder', node_type=File.NodeType.FOLDER,
            parent=self.group_root,
        )
        child = File.objects.create(
            owner=self.alice, name='file.txt', node_type=File.NodeType.FILE,
            parent=folder, mime_type='text/plain',
        )
        FileService.propagate_group(folder, self.group)
        folder.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(folder.group_id, self.group.id)
        self.assertEqual(child.group_id, self.group.id)

    def test_propagate_group_clears_group(self):
        folder = File.objects.create(
            owner=self.alice, name='Folder', node_type=File.NodeType.FOLDER,
            parent=self.group_root, group=self.group,
        )
        child = File.objects.create(
            owner=self.alice, name='file.txt', node_type=File.NodeType.FILE,
            parent=folder, group=self.group, mime_type='text/plain',
        )
        FileService.propagate_group(folder, None)
        folder.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNone(folder.group_id)
        self.assertIsNone(child.group_id)

    def test_move_personal_to_group_sets_group(self):
        f = FileService.create_file(
            self.alice, 'doc.txt', content=ContentFile(b'hello', name='doc.txt'),
        )
        FileService.move(f, self.group_root, acting_user=self.alice)
        f.refresh_from_db()
        self.assertEqual(f.group_id, self.group.id)

    def test_move_group_to_personal_clears_group_and_changes_owner(self):
        f = FileService.create_file(
            self.alice, 'doc.txt', parent=self.group_root,
            content=ContentFile(b'hello', name='doc.txt'),
        )
        personal_folder = FileService.create_folder(self.bob, 'My Stuff')
        FileService.move(f, personal_folder, acting_user=self.bob)
        f.refresh_from_db()
        self.assertIsNone(f.group_id)
        self.assertEqual(f.owner_id, self.bob.id)


class GroupValidationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.outsider = User.objects.create_user(username='outsider', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        self.group_root = File.objects.create(
            owner=self.alice, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )

    def test_move_to_group_folder_allowed_for_member(self):
        personal = File.objects.create(
            owner=self.bob, name='doc.txt', node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        FileService.validate_move_target(personal, self.group_root, user=self.bob)

    def test_move_to_group_folder_denied_for_outsider(self):
        personal = File.objects.create(
            owner=self.outsider, name='doc.txt', node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        with self.assertRaises(ValueError):
            FileService.validate_move_target(personal, self.group_root, user=self.outsider)

    def test_move_from_group_to_personal_allowed(self):
        group_file = File.objects.create(
            owner=self.alice, name='doc.txt', node_type=File.NodeType.FILE,
            parent=self.group_root, group=self.group, mime_type='text/plain',
        )
        bob_folder = File.objects.create(
            owner=self.bob, name='Personal', node_type=File.NodeType.FOLDER,
        )
        FileService.validate_move_target(group_file, bob_folder, user=self.bob)

    def test_check_name_available_in_group_folder(self):
        File.objects.create(
            owner=self.alice, name='report.txt', node_type=File.NodeType.FILE,
            parent=self.group_root, group=self.group, mime_type='text/plain',
        )
        with self.assertRaises(ValueError):
            FileService.check_name_available(
                self.bob, self.group_root, 'report.txt', File.NodeType.FILE,
            )


class GroupFolderSerializerTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)

    def test_create_group_root_folder_via_api(self):
        self.client.force_authenticate(user=self.alice)
        resp = self.client.post('/api/v1/files', {
            'name': 'Marketing Files',
            'node_type': 'folder',
            'group': self.group.id,
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['group'], self.group.id)

    def test_create_file_in_group_folder_inherits_group(self):
        self.client.force_authenticate(user=self.alice)
        root = File.objects.create(
            owner=self.alice, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )
        resp = self.client.post('/api/v1/files', {
            'name': 'notes.txt',
            'node_type': 'file',
            'parent': str(root.uuid),
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['group'], self.group.id)

    def test_group_field_in_response(self):
        self.client.force_authenticate(user=self.alice)
        root = File.objects.create(
            owner=self.alice, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )
        resp = self.client.get(f'/api/v1/files/{root.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['group'], self.group.id)


class GroupFolderViewSetTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.outsider = User.objects.create_user(username='outsider', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        self.group_root = File.objects.create(
            owner=self.alice, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )

    def test_list_with_group_filter(self):
        self.client.force_authenticate(user=self.bob)
        resp = self.client.get(f'/api/v1/files?group={self.group.id}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        uuids = [f['uuid'] for f in resp.data]
        self.assertIn(str(self.group_root.uuid), uuids)

    def test_list_group_root_contents(self):
        child = File.objects.create(
            owner=self.alice, name='report.txt', node_type=File.NodeType.FILE,
            parent=self.group_root, group=self.group, mime_type='text/plain',
        )
        self.client.force_authenticate(user=self.bob)
        resp = self.client.get(f'/api/v1/files?parent={self.group_root.uuid}&group={self.group.id}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        uuids = [f['uuid'] for f in resp.data]
        self.assertIn(str(child.uuid), uuids)

    def test_outsider_cannot_list_group_files(self):
        self.client.force_authenticate(user=self.outsider)
        resp = self.client.get(f'/api/v1/files?group={self.group.id}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 0)

    def test_retrieve_group_file_as_member(self):
        self.client.force_authenticate(user=self.bob)
        resp = self.client.get(f'/api/v1/files/{self.group_root.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_retrieve_group_file_as_outsider(self):
        self.client.force_authenticate(user=self.outsider)
        resp = self.client.get(f'/api/v1/files/{self.group_root.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_group_root_non_member_denied(self):
        self.client.force_authenticate(user=self.outsider)
        group2 = Group.objects.create(name='Secret')
        resp = self.client.post('/api/v1/files', {
            'name': 'Hacked', 'node_type': 'folder', 'group': group2.id,
        })
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_bob_can_delete_group_file(self):
        child = File.objects.create(
            owner=self.alice, name='report.txt', node_type=File.NodeType.FILE,
            parent=self.group_root, group=self.group, mime_type='text/plain',
        )
        self.client.force_authenticate(user=self.bob)
        resp = self.client.delete(f'/api/v1/files/{child.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_bob_can_rename_group_file(self):
        child = File.objects.create(
            owner=self.alice, name='report.txt', node_type=File.NodeType.FILE,
            parent=self.group_root, group=self.group, mime_type='text/plain',
        )
        self.client.force_authenticate(user=self.bob)
        resp = self.client.patch(f'/api/v1/files/{child.uuid}', {'name': 'renamed.txt'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'renamed.txt')


class UserGroupsAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.user.groups.add(self.group)

    def test_list_user_groups(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/v1/users/groups')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['id'], self.group.id)
        self.assertEqual(resp.data[0]['name'], 'Marketing')
        self.assertFalse(resp.data[0]['has_folder'])

    def test_list_user_groups_with_existing_folder(self):
        File.objects.create(
            owner=self.user, name='Marketing', node_type=File.NodeType.FOLDER, group=self.group,
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/v1/users/groups')
        self.assertEqual(resp.data[0]['has_folder'], True)

    def test_unauthenticated_denied(self):
        resp = self.client.get('/api/v1/users/groups')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class BreadcrumbTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            username='alice', password='pass', first_name='Alice', last_name='Dupont',
        )
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.group = Group.objects.create(name='Marketing')
        self.alice.groups.add(self.group)

    def test_personal_root_folder_uses_full_name(self):
        folder = File.objects.create(
            owner=self.alice, name='Documents', node_type=File.NodeType.FOLDER,
        )
        crumbs = build_breadcrumbs(folder, user=self.alice)
        self.assertEqual(len(crumbs), 2)
        self.assertEqual(crumbs[0]['label'], 'Alice Dupont')
        self.assertEqual(crumbs[0]['icon'], 'hard-drive')
        self.assertEqual(crumbs[0]['url'], '/files')
        self.assertEqual(crumbs[1]['label'], 'Documents')

    def test_personal_root_folder_falls_back_to_username(self):
        folder = File.objects.create(
            owner=self.bob, name='Stuff', node_type=File.NodeType.FOLDER,
        )
        crumbs = build_breadcrumbs(folder, user=self.bob)
        self.assertEqual(crumbs[0]['label'], 'bob')

    def test_personal_nested_folder(self):
        parent = File.objects.create(
            owner=self.alice, name='Documents', node_type=File.NodeType.FOLDER,
        )
        child = File.objects.create(
            owner=self.alice, name='Reports', node_type=File.NodeType.FOLDER, parent=parent,
        )
        crumbs = build_breadcrumbs(child, user=self.alice)
        self.assertEqual(len(crumbs), 3)
        self.assertEqual(crumbs[0]['label'], 'Alice Dupont')
        self.assertEqual(crumbs[1]['label'], 'Documents')
        self.assertEqual(crumbs[2]['label'], 'Reports')

    def test_group_root_folder(self):
        folder = File.objects.create(
            owner=self.alice, name='Marketing Files', node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        crumbs = build_breadcrumbs(folder, user=self.alice)
        self.assertEqual(len(crumbs), 2)
        self.assertEqual(crumbs[0]['label'], 'Groups')
        self.assertEqual(crumbs[0]['icon'], 'users')
        self.assertNotIn('url', crumbs[0])
        self.assertEqual(crumbs[1]['label'], 'Marketing Files')

    def test_group_nested_folder(self):
        root = File.objects.create(
            owner=self.alice, name='Marketing Files', node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        sub = File.objects.create(
            owner=self.alice, name='Q1', node_type=File.NodeType.FOLDER,
            parent=root, group=self.group,
        )
        crumbs = build_breadcrumbs(sub, user=self.alice)
        self.assertEqual(len(crumbs), 3)
        self.assertEqual(crumbs[0]['label'], 'Groups')
        self.assertEqual(crumbs[1]['label'], 'Marketing Files')
        self.assertEqual(crumbs[2]['label'], 'Q1')

    def test_no_user_falls_back_to_my_files(self):
        folder = File.objects.create(
            owner=self.alice, name='Docs', node_type=File.NodeType.FOLDER,
        )
        crumbs = build_breadcrumbs(folder)
        self.assertEqual(crumbs[0]['label'], 'My Files')

    def test_folder_with_custom_icon_and_color(self):
        folder = File.objects.create(
            owner=self.alice, name='Special', node_type=File.NodeType.FOLDER,
            icon='briefcase', color='text-error',
        )
        crumbs = build_breadcrumbs(folder, user=self.alice)
        self.assertEqual(crumbs[1]['icon'], 'briefcase')
        self.assertEqual(crumbs[1]['icon_color'], 'text-error')

    def test_breadcrumbs_include_uuid(self):
        """Breadcrumb dicts for folders must include a 'uuid' key."""
        parent = File.objects.create(
            owner=self.alice, name='Documents', node_type=File.NodeType.FOLDER,
        )
        child = File.objects.create(
            owner=self.alice, name='Reports', node_type=File.NodeType.FOLDER, parent=parent,
        )
        crumbs = build_breadcrumbs(child, user=self.alice)
        # Root entry ("Alice Dupont") has no uuid
        self.assertNotIn('uuid', crumbs[0])
        # Folder entries have their uuid
        self.assertEqual(crumbs[1]['uuid'], parent.uuid)
        self.assertEqual(crumbs[2]['uuid'], child.uuid)

    def test_group_breadcrumbs_include_uuid(self):
        root = File.objects.create(
            owner=self.alice, name='Marketing Files', node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        sub = File.objects.create(
            owner=self.alice, name='Q1', node_type=File.NodeType.FOLDER,
            parent=root, group=self.group,
        )
        crumbs = build_breadcrumbs(sub, user=self.alice)
        # "Groups" header has no uuid
        self.assertNotIn('uuid', crumbs[0])
        self.assertEqual(crumbs[1]['uuid'], root.uuid)
        self.assertEqual(crumbs[2]['uuid'], sub.uuid)


class BreadcrumbQueryCountTests(TestCase):
    """Breadcrumb building must use a constant number of queries regardless of depth."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', password='pass', first_name='Alice', last_name='Dupont',
        )
        self.group = Group.objects.create(name='Engineering')
        self.user.groups.add(self.group)

    def test_root_folder_zero_queries(self):
        folder = File.objects.create(
            owner=self.user, name='Root', node_type=File.NodeType.FOLDER,
        )
        # Re-fetch from DB to clear cached FK references
        folder = File.objects.get(pk=folder.pk)
        with self.assertNumQueries(0):
            build_breadcrumbs(folder, user=self.user)

    def test_nested_5_levels_one_query(self):
        """5 levels deep should use exactly 1 query, not 5."""
        parent = None
        for i in range(5):
            parent = File.objects.create(
                owner=self.user, name=f'level_{i}', node_type=File.NodeType.FOLDER,
                parent=parent,
            )

        # Re-fetch leaf from DB — simulates the real view path where
        # the folder is loaded via File.objects.filter(...).first()
        leaf = File.objects.get(pk=parent.pk)
        # 1 query to fetch the 4 ancestors (leaf itself is already in memory)
        with self.assertNumQueries(1):
            crumbs = build_breadcrumbs(leaf, user=self.user)
        self.assertEqual(len(crumbs), 6)  # root entry + 5 folders
        for i, name in enumerate(['level_0', 'level_1', 'level_2', 'level_3', 'level_4']):
            self.assertEqual(crumbs[i + 1]['label'], name)

    def test_group_nested_3_levels_one_query(self):
        parent = None
        for i in range(3):
            parent = File.objects.create(
                owner=self.user, name=f'g_level_{i}', node_type=File.NodeType.FOLDER,
                parent=parent, group=self.group,
            )

        leaf = File.objects.get(pk=parent.pk)
        with self.assertNumQueries(1):
            crumbs = build_breadcrumbs(leaf, user=self.user)
        self.assertEqual(len(crumbs), 4)  # "Groups" + 3 folders
        self.assertEqual(crumbs[0]['label'], 'Groups')


class BreadcrumbSpecialCharTests(TestCase):
    """Breadcrumbs must handle folder names with spaces, unicode, and symbols."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', password='pass', first_name='Alice', last_name='Dupont',
        )

    def test_spaces_in_names(self):
        parent = File.objects.create(
            owner=self.user, name='My Documents', node_type=File.NodeType.FOLDER,
        )
        child = File.objects.create(
            owner=self.user, name='Work Stuff 2026', node_type=File.NodeType.FOLDER,
            parent=parent,
        )
        leaf = File.objects.get(pk=child.pk)
        crumbs = build_breadcrumbs(leaf, user=self.user)
        self.assertEqual(crumbs[1]['label'], 'My Documents')
        self.assertEqual(crumbs[2]['label'], 'Work Stuff 2026')

    def test_unicode_names(self):
        parent = File.objects.create(
            owner=self.user, name='Données', node_type=File.NodeType.FOLDER,
        )
        child = File.objects.create(
            owner=self.user, name='日本語フォルダ', node_type=File.NodeType.FOLDER,
            parent=parent,
        )
        leaf = File.objects.get(pk=child.pk)
        crumbs = build_breadcrumbs(leaf, user=self.user)
        self.assertEqual(crumbs[1]['label'], 'Données')
        self.assertEqual(crumbs[2]['label'], '日本語フォルダ')

    def test_symbols_in_names(self):
        parent = File.objects.create(
            owner=self.user, name='R&D (2026)', node_type=File.NodeType.FOLDER,
        )
        child = File.objects.create(
            owner=self.user, name='budget — final #2', node_type=File.NodeType.FOLDER,
            parent=parent,
        )
        leaf = File.objects.get(pk=child.pk)
        crumbs = build_breadcrumbs(leaf, user=self.user)
        self.assertEqual(crumbs[1]['label'], 'R&D (2026)')
        self.assertEqual(crumbs[2]['label'], 'budget — final #2')

    def test_special_chars_query_count(self):
        """Special characters must not break the single-query optimization."""
        parent = File.objects.create(
            owner=self.user, name='Données & Résultats (2026)', node_type=File.NodeType.FOLDER,
        )
        child = File.objects.create(
            owner=self.user, name='日本語 #2', node_type=File.NodeType.FOLDER,
            parent=parent,
        )
        leaf = File.objects.get(pk=child.pk)
        with self.assertNumQueries(1):
            build_breadcrumbs(leaf, user=self.user)


class FileNameSlashValidationTests(TestCase):
    """Names containing '/' must be rejected — it is the path separator."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass')

    def test_folder_name_with_slash_rejected(self):
        with self.assertRaises(ValueError):
            File.objects.create(
                owner=self.user, name='foo/bar', node_type=File.NodeType.FOLDER,
            )

    def test_file_name_with_slash_rejected(self):
        with self.assertRaises(ValueError):
            File.objects.create(
                owner=self.user, name='report/final.pdf', node_type=File.NodeType.FILE,
            )

    def test_name_without_slash_accepted(self):
        f = File.objects.create(
            owner=self.user, name='R&D (2026) — final', node_type=File.NodeType.FOLDER,
        )
        self.assertEqual(f.name, 'R&D (2026) — final')
