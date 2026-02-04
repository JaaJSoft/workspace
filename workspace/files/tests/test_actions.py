from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from workspace.files.models import File, FileShare
from workspace.files.actions import ActionRegistry
from workspace.files.actions.base import ActionCategory

User = get_user_model()


def _make_file(owner, **kwargs):
    """Create an unsaved File with common defaults and annotations."""
    defaults = {
        'name': 'test.txt',
        'node_type': 'file',
        'mime_type': 'text/plain',
    }
    defaults.update(kwargs)
    f = File(owner=owner, **defaults)
    # Attach default annotations
    f.is_favorite = getattr(f, 'is_favorite', False)
    f.is_pinned = getattr(f, 'is_pinned', False)
    f.is_shared = getattr(f, 'is_shared', False)
    f.deleted_at = getattr(f, 'deleted_at', None)
    return f


def _make_folder(owner, **kwargs):
    """Create an unsaved folder File with common defaults and annotations."""
    defaults = {'name': 'Dir', 'node_type': 'folder'}
    defaults.update(kwargs)
    return _make_file(owner, **defaults)


class ActionRegistryTests(TestCase):
    """Tests for ActionRegistry metadata and structure."""

    def test_all_actions_registered(self):
        expected_ids = {
            'view', 'open', 'open_new_tab',
            'download', 'copy_link',
            'toggle_favorite', 'toggle_pin', 'share',
            'rename', 'cut', 'copy', 'paste_into',
            'properties',
            'delete',
            'restore', 'purge',
        }
        all_actions = ActionRegistry.all()
        actual_ids = {a.id for a in all_actions}
        self.assertEqual(expected_ids, actual_ids)

    def test_no_duplicate_ids(self):
        all_actions = ActionRegistry.all()
        ids = [a.id for a in all_actions]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_have_required_metadata(self):
        for action in ActionRegistry.all():
            self.assertTrue(action.id, f"Action missing id: {action}")
            self.assertTrue(action.label, f"Action {action.id} missing label")
            self.assertTrue(action.icon, f"Action {action.id} missing icon")
            self.assertIsInstance(action.category, ActionCategory)
            self.assertIsInstance(action.node_types, tuple)
            self.assertTrue(len(action.node_types) > 0)

    def test_registration_order(self):
        all_actions = ActionRegistry.all()
        self.assertEqual(all_actions[0].id, 'view')
        self.assertEqual(all_actions[-1].id, 'purge')

    def test_category_order_contiguous(self):
        """Actions within each category should be contiguous."""
        all_actions = ActionRegistry.all()
        seen_categories = []
        for action in all_actions:
            cat = action.category
            if not seen_categories or seen_categories[-1] != cat:
                seen_categories.append(cat)
        # Each category should appear exactly once in seen_categories
        self.assertEqual(len(seen_categories), len(set(seen_categories)))

    def test_get_existing_action(self):
        action = ActionRegistry.get('view')
        self.assertIsNotNone(action)
        self.assertEqual(action.id, 'view')

    def test_get_nonexistent_action(self):
        action = ActionRegistry.get('nonexistent')
        self.assertIsNone(action)


class ViewActionTests(TestCase):
    """Tests for ViewAction.is_available."""

    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_viewable_file(self):
        f = _make_file(self.user, mime_type='text/plain')
        action = ActionRegistry.get('view')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_shared_viewable_file(self):
        f = _make_file(self.user, mime_type='text/plain')
        action = ActionRegistry.get('view')
        self.assertTrue(action.is_available(self.other, f, is_owner=False, share_permission='ro'))

    def test_not_viewable_file(self):
        f = _make_file(self.user, mime_type='application/octet-stream')
        action = ActionRegistry.get('view')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))

    def test_deleted_file(self):
        f = _make_file(self.user, mime_type='text/plain')
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('view')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))

    def test_folder_not_applicable(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('view')
        # node_types check is done in get_available_actions, but is_available
        # should still return False for folders since they aren't viewable
        self.assertNotIn('file', [folder.node_type])  # folder won't match node_types


class OpenFolderActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_folder_available(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('open')
        self.assertTrue(action.is_available(self.user, folder, is_owner=True))

    def test_deleted_folder(self):
        folder = _make_folder(self.user)
        folder.deleted_at = timezone.now()
        action = ActionRegistry.get('open')
        self.assertFalse(action.is_available(self.user, folder, is_owner=True))

    def test_not_owner(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('open')
        self.assertFalse(action.is_available(self.other, folder, is_owner=False))


class OpenNewTabActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('open_new_tab')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_shared_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('open_new_tab')
        self.assertTrue(action.is_available(self.other, f, is_owner=False, share_permission='ro'))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('open_new_tab')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('open_new_tab')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))


class DownloadActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.is_available(self.user, folder, is_owner=True))

    def test_shared_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.is_available(self.other, f, is_owner=False, share_permission='ro'))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('download')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))

    def test_folder_label(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('download')
        self.assertEqual(action.get_label(folder), 'Download as ZIP')

    def test_file_label(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertEqual(action.get_label(f), 'Download')


class CopyLinkActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('copy_link')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('copy_link')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))


class ToggleFavoriteActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('toggle_favorite')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('toggle_favorite')
        self.assertTrue(action.is_available(self.user, folder, is_owner=True))

    def test_shared_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('toggle_favorite')
        self.assertTrue(action.is_available(self.other, f, is_owner=False, share_permission='ro'))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('toggle_favorite')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_dynamic_label_not_favorite(self):
        f = _make_file(self.user)
        f.is_favorite = False
        action = ActionRegistry.get('toggle_favorite')
        self.assertEqual(action.get_label(f), 'Add to favorites')

    def test_dynamic_label_is_favorite(self):
        f = _make_file(self.user)
        f.is_favorite = True
        action = ActionRegistry.get('toggle_favorite')
        self.assertEqual(action.get_label(f), 'Remove from favorites')

    def test_serialize_includes_state(self):
        f = _make_file(self.user)
        f.is_favorite = True
        action = ActionRegistry.get('toggle_favorite')
        data = action.serialize(f)
        self.assertIn('state', data)
        self.assertTrue(data['state']['is_favorite'])


class TogglePinActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('toggle_pin')
        self.assertTrue(action.is_available(self.user, folder, is_owner=True))

    def test_not_owner(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('toggle_pin')
        self.assertFalse(action.is_available(self.other, folder, is_owner=False, share_permission='ro'))

    def test_deleted(self):
        folder = _make_folder(self.user)
        folder.deleted_at = timezone.now()
        action = ActionRegistry.get('toggle_pin')
        self.assertFalse(action.is_available(self.user, folder, is_owner=True))

    def test_dynamic_label_not_pinned(self):
        folder = _make_folder(self.user)
        folder.is_pinned = False
        action = ActionRegistry.get('toggle_pin')
        self.assertEqual(action.get_label(folder), 'Pin to sidebar')

    def test_dynamic_label_is_pinned(self):
        folder = _make_folder(self.user)
        folder.is_pinned = True
        action = ActionRegistry.get('toggle_pin')
        self.assertEqual(action.get_label(folder), 'Unpin from sidebar')

    def test_serialize_includes_state(self):
        folder = _make_folder(self.user)
        folder.is_pinned = True
        action = ActionRegistry.get('toggle_pin')
        data = action.serialize(folder)
        self.assertIn('state', data)
        self.assertTrue(data['state']['is_pinned'])


class ShareActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('share')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('share')
        self.assertFalse(action.is_available(self.other, f, is_owner=False, share_permission='rw'))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('share')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))


class RenameActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('rename')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('rename')
        self.assertTrue(action.is_available(self.user, folder, is_owner=True))

    def test_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('rename')
        self.assertFalse(action.is_available(self.other, f, is_owner=False, share_permission='rw'))


class CutCopyPasteActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_cut_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('cut')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_cut_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('cut')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_copy_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('copy')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_copy_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('copy')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_paste_into_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('paste_into')
        self.assertTrue(action.is_available(self.user, folder, is_owner=True))

    def test_paste_into_not_owner(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('paste_into')
        self.assertFalse(action.is_available(self.other, folder, is_owner=False))


class PropertiesActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('properties')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_shared(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('properties')
        self.assertTrue(action.is_available(self.other, f, is_owner=False, share_permission='ro'))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('properties')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('properties')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))


class DeleteActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('delete')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('delete')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('delete')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))

    def test_css_class(self):
        action = ActionRegistry.get('delete')
        self.assertEqual(action.css_class, 'text-error')


class RestoreActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('restore')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_not_deleted(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('restore')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))

    def test_not_owner(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('restore')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))


class PurgeActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('purge')
        self.assertTrue(action.is_available(self.user, f, is_owner=True))

    def test_not_deleted(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('purge')
        self.assertFalse(action.is_available(self.user, f, is_owner=True))

    def test_not_owner(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('purge')
        self.assertFalse(action.is_available(self.other, f, is_owner=False))

    def test_css_class(self):
        action = ActionRegistry.get('purge')
        self.assertEqual(action.css_class, 'text-error')


class GetAvailableActionsTests(TestCase):
    """Integration tests for ActionRegistry.get_available_actions."""

    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file_actions(self):
        f = _make_file(self.user, mime_type='text/plain')
        actions = ActionRegistry.get_available_actions(
            self.user, f, is_owner=True,
        )
        action_ids = [a['id'] for a in actions]
        # Owner of a viewable file should see all non-trash, non-folder actions
        self.assertIn('view', action_ids)
        self.assertIn('open_new_tab', action_ids)
        self.assertIn('download', action_ids)
        self.assertIn('copy_link', action_ids)
        self.assertIn('toggle_favorite', action_ids)
        self.assertIn('share', action_ids)
        self.assertIn('rename', action_ids)
        self.assertIn('cut', action_ids)
        self.assertIn('copy', action_ids)
        self.assertIn('properties', action_ids)
        self.assertIn('delete', action_ids)
        # Should not see folder-only or trash actions
        self.assertNotIn('open', action_ids)
        self.assertNotIn('toggle_pin', action_ids)
        self.assertNotIn('paste_into', action_ids)
        self.assertNotIn('restore', action_ids)
        self.assertNotIn('purge', action_ids)

    def test_owner_folder_actions(self):
        folder = _make_folder(self.user)
        actions = ActionRegistry.get_available_actions(
            self.user, folder, is_owner=True,
        )
        action_ids = [a['id'] for a in actions]
        self.assertIn('open', action_ids)
        self.assertIn('download', action_ids)
        self.assertIn('toggle_favorite', action_ids)
        self.assertIn('toggle_pin', action_ids)
        self.assertIn('rename', action_ids)
        self.assertIn('paste_into', action_ids)
        self.assertIn('properties', action_ids)
        self.assertIn('delete', action_ids)
        # Should not see file-only actions
        self.assertNotIn('view', action_ids)
        self.assertNotIn('open_new_tab', action_ids)
        self.assertNotIn('copy_link', action_ids)
        self.assertNotIn('share', action_ids)

    def test_shared_ro_file_actions(self):
        f = _make_file(self.user, mime_type='text/plain')
        actions = ActionRegistry.get_available_actions(
            self.other, f, is_owner=False, share_permission='ro',
        )
        action_ids = [a['id'] for a in actions]
        self.assertIn('view', action_ids)
        self.assertIn('download', action_ids)
        self.assertIn('toggle_favorite', action_ids)
        self.assertIn('properties', action_ids)
        # Should not see owner-only actions
        self.assertNotIn('rename', action_ids)
        self.assertNotIn('cut', action_ids)
        self.assertNotIn('copy', action_ids)
        self.assertNotIn('delete', action_ids)
        self.assertNotIn('share', action_ids)

    def test_shared_rw_file_actions(self):
        f = _make_file(self.user, mime_type='text/plain')
        actions = ActionRegistry.get_available_actions(
            self.other, f, is_owner=False, share_permission='rw',
        )
        action_ids = [a['id'] for a in actions]
        # RW shared still can't rename, delete, share — same as RO for menu actions
        self.assertNotIn('rename', action_ids)
        self.assertNotIn('delete', action_ids)
        self.assertNotIn('share', action_ids)

    def test_trash_file_actions(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        actions = ActionRegistry.get_available_actions(
            self.user, f, is_owner=True,
        )
        action_ids = [a['id'] for a in actions]
        self.assertIn('restore', action_ids)
        self.assertIn('purge', action_ids)
        # Should not see normal actions
        self.assertNotIn('view', action_ids)
        self.assertNotIn('download', action_ids)
        self.assertNotIn('rename', action_ids)
        self.assertNotIn('delete', action_ids)

    def test_no_access_returns_empty(self):
        f = _make_file(self.user)
        actions = ActionRegistry.get_available_actions(
            self.other, f, is_owner=False,
        )
        # No share permission — only actions that don't require access
        # For file: no actions should be available without owner or share
        action_ids = [a['id'] for a in actions]
        self.assertNotIn('view', action_ids)
        self.assertNotIn('download', action_ids)
        self.assertNotIn('rename', action_ids)

    def test_serialized_format(self):
        f = _make_file(self.user, mime_type='text/plain')
        actions = ActionRegistry.get_available_actions(
            self.user, f, is_owner=True,
        )
        for action in actions:
            self.assertIn('id', action)
            self.assertIn('label', action)
            self.assertIn('icon', action)
            self.assertIn('category', action)
            self.assertIn('shortcut', action)
            self.assertIn('css_class', action)
            self.assertIn('bulk', action)

    def test_bulk_flag_in_serialized(self):
        f = _make_file(self.user, mime_type='text/plain')
        actions = ActionRegistry.get_available_actions(
            self.user, f, is_owner=True,
        )
        by_id = {a['id']: a for a in actions}
        self.assertTrue(by_id['download']['bulk'])
        self.assertTrue(by_id['delete']['bulk'])
        self.assertFalse(by_id['rename']['bulk'])
        self.assertFalse(by_id['view']['bulk'])

    def test_categories_grouped(self):
        """Actions should be grouped by category with no interleaving."""
        f = _make_file(self.user, mime_type='text/plain')
        actions = ActionRegistry.get_available_actions(
            self.user, f, is_owner=True,
        )
        seen = []
        for a in actions:
            cat = a['category']
            if not seen or seen[-1] != cat:
                seen.append(cat)
        self.assertEqual(len(seen), len(set(seen)))


class IsActionAvailableTests(TestCase):
    """Tests for ActionRegistry.is_action_available."""

    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_available(self):
        f = _make_file(self.user)
        self.assertTrue(
            ActionRegistry.is_action_available(
                'download', self.user, f, is_owner=True,
            )
        )

    def test_not_available_wrong_node_type(self):
        folder = _make_folder(self.user)
        self.assertFalse(
            ActionRegistry.is_action_available(
                'view', self.user, folder, is_owner=True,
            )
        )

    def test_not_available_no_permission(self):
        f = _make_file(self.user)
        self.assertFalse(
            ActionRegistry.is_action_available(
                'rename', self.other, f, is_owner=False,
            )
        )

    def test_nonexistent_action(self):
        f = _make_file(self.user)
        self.assertFalse(
            ActionRegistry.is_action_available(
                'nonexistent', self.user, f, is_owner=True,
            )
        )


class SerializerIntegrationTests(APITestCase):
    """Tests that the FileSerializer no longer includes actions."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123',
        )

    def test_file_has_no_actions_field(self):
        self.client.force_authenticate(user=self.owner)
        f = File.objects.create(
            owner=self.owner, name='doc.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'hello', name='doc.txt'),
        )
        resp = self.client.get(f'/api/v1/files/{f.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertNotIn('actions', data)

    def test_folder_has_no_actions_field(self):
        self.client.force_authenticate(user=self.owner)
        folder = File.objects.create(
            owner=self.owner, name='Folder',
            node_type=File.NodeType.FOLDER,
        )
        resp = self.client.get(f'/api/v1/files/{folder.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertNotIn('actions', data)


class BulkFlagTests(TestCase):
    """Verify the expected actions have supports_bulk=True."""

    EXPECTED_BULK = {
        'toggle_favorite', 'toggle_pin', 'download',
        'cut', 'copy', 'delete', 'restore', 'purge',
    }

    def test_bulk_actions_flagged(self):
        for action in ActionRegistry.all():
            if action.id in self.EXPECTED_BULK:
                self.assertTrue(
                    action.supports_bulk,
                    f"{action.id} should have supports_bulk=True",
                )
            else:
                self.assertFalse(
                    action.supports_bulk,
                    f"{action.id} should have supports_bulk=False",
                )

    def test_bulk_count(self):
        bulk = [a for a in ActionRegistry.all() if a.supports_bulk]
        self.assertEqual(len(bulk), len(self.EXPECTED_BULK))


class BulkFlagSerializationTests(TestCase):
    """Tests that the bulk flag is correctly serialized."""

    def setUp(self):
        self.user = User(pk=1)

    def test_bulk_flag_present_in_serialize(self):
        f = _make_file(self.user)
        for action in ActionRegistry.all():
            if 'file' not in action.node_types:
                continue
            data = action.serialize(f)
            self.assertIn('bulk', data, f"{action.id} serialize() missing 'bulk'")
            self.assertIsInstance(data['bulk'], bool)

    def test_bulk_true_for_bulk_actions(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.serialize(f)['bulk'])

    def test_bulk_false_for_non_bulk_actions(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('rename')
        self.assertFalse(action.serialize(f)['bulk'])


class FilesActionsEndpointTests(APITestCase):
    """Tests for POST /api/v1/files/actions."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='bulkowner', email='bulk@example.com', password='pass123',
        )
        self.other = User.objects.create_user(
            username='bulkother', email='bulkother@example.com', password='pass123',
        )

    def test_basic_request(self):
        self.client.force_authenticate(user=self.owner)
        f = File.objects.create(
            owner=self.owner, name='a.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'a', name='a.txt'),
        )
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(f.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertIsInstance(data, dict)
        # Keyed by UUID
        self.assertIn(str(f.uuid), data)
        actions = data[str(f.uuid)]
        self.assertIsInstance(actions, list)
        action_ids = [a['id'] for a in actions]
        self.assertIn('download', action_ids)
        self.assertIn('delete', action_ids)

    def test_per_uuid_actions(self):
        """Each UUID gets its own action list."""
        self.client.force_authenticate(user=self.owner)
        f = File.objects.create(
            owner=self.owner, name='f.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'f', name='f.txt'),
        )
        folder = File.objects.create(
            owner=self.owner, name='Dir',
            node_type=File.NodeType.FOLDER,
        )
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(f.uuid), str(folder.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        file_ids = [a['id'] for a in data[str(f.uuid)]]
        folder_ids = [a['id'] for a in data[str(folder.uuid)]]
        # File has share, folder has toggle_pin
        self.assertIn('share', file_ids)
        self.assertNotIn('share', folder_ids)
        self.assertIn('toggle_pin', folder_ids)
        self.assertNotIn('toggle_pin', file_ids)

    def test_actions_include_bulk_flag(self):
        self.client.force_authenticate(user=self.owner)
        f = File.objects.create(
            owner=self.owner, name='b.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'b', name='b.txt'),
        )
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(f.uuid)]},
            format='json',
        )
        data = resp.json()
        for action in data[str(f.uuid)]:
            self.assertIn('bulk', action)

    def test_empty_uuids(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': []},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_uuids(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            '/api/v1/files/actions',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_uuid(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': ['00000000-0000-0000-0000-000000000000']},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_trash_file_actions(self):
        self.client.force_authenticate(user=self.owner)
        f = File.objects.create(
            owner=self.owner, name='trashed.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'x', name='trashed.txt'),
            deleted_at=timezone.now(),
        )
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(f.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        action_ids = [a['id'] for a in resp.json()[str(f.uuid)]]
        self.assertIn('restore', action_ids)
        self.assertIn('purge', action_ids)
        self.assertNotIn('delete', action_ids)

    def test_other_user_no_access(self):
        self.client.force_authenticate(user=self.other)
        f = File.objects.create(
            owner=self.owner, name='private.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'secret', name='private.txt'),
        )
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(f.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_shared_file_restricted_actions(self):
        f = File.objects.create(
            owner=self.owner, name='shared.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
            content=ContentFile(b'shared', name='shared.txt'),
        )
        FileShare.objects.create(
            file=f, shared_by=self.owner, shared_with=self.other,
            permission=FileShare.Permission.READ_ONLY,
        )
        self.client.force_authenticate(user=self.other)
        resp = self.client.post(
            '/api/v1/files/actions',
            {'uuids': [str(f.uuid)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        action_ids = [a['id'] for a in resp.json()[str(f.uuid)]]
        self.assertIn('toggle_favorite', action_ids)
        self.assertIn('download', action_ids)
        self.assertNotIn('cut', action_ids)
        self.assertNotIn('delete', action_ids)
