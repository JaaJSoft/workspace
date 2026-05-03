from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()


class DescendantsAPITests(APITestCase):
    """Tests for ?descendants=1 query parameter."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', password='pass',
        )
        self.client.force_authenticate(user=self.user)

        # Build tree: root / sub / deep
        self.root = File.objects.create(
            owner=self.user, name='Root',
            node_type=File.NodeType.FOLDER,
        )
        self.sub = File.objects.create(
            owner=self.user, name='Sub',
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self.deep = File.objects.create(
            owner=self.user, name='Deep',
            node_type=File.NodeType.FOLDER,
            parent=self.sub,
        )
        # Files at various levels
        self.note_root = File.objects.create(
            owner=self.user, name='root.md',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
            parent=self.root,
        )
        self.note_sub = File.objects.create(
            owner=self.user, name='sub.md',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
            parent=self.sub,
        )
        self.note_deep = File.objects.create(
            owner=self.user, name='deep.md',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
            parent=self.deep,
        )
        # File outside the tree
        self.other = File.objects.create(
            owner=self.user, name='other.md',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )

    def test_without_descendants_returns_direct_children(self):
        resp = self.client.get(
            '/api/v1/files',
            {'parent': str(self.root.uuid), 'mime_type': 'text/markdown'},
        )
        self.assertEqual(resp.status_code, 200)
        uuids = {item['uuid'] for item in resp.data}
        self.assertEqual(uuids, {str(self.note_root.uuid)})

    def test_descendants_returns_all_nested_files(self):
        resp = self.client.get(
            '/api/v1/files',
            {
                'parent': str(self.root.uuid),
                'mime_type': 'text/markdown',
                'descendants': '1',
            },
        )
        self.assertEqual(resp.status_code, 200)
        uuids = {item['uuid'] for item in resp.data}
        self.assertEqual(uuids, {
            str(self.note_root.uuid),
            str(self.note_sub.uuid),
            str(self.note_deep.uuid),
        })

    def test_descendants_excludes_files_outside_tree(self):
        resp = self.client.get(
            '/api/v1/files',
            {
                'parent': str(self.root.uuid),
                'descendants': '1',
            },
        )
        uuids = {item['uuid'] for item in resp.data}
        self.assertNotIn(str(self.other.uuid), uuids)

    def test_descendants_respects_node_type_filter(self):
        resp = self.client.get(
            '/api/v1/files',
            {
                'parent': str(self.root.uuid),
                'node_type': 'folder',
                'descendants': '1',
            },
        )
        self.assertEqual(resp.status_code, 200)
        uuids = {item['uuid'] for item in resp.data}
        self.assertEqual(uuids, {str(self.sub.uuid), str(self.deep.uuid)})

    def test_descendants_from_subfolder(self):
        resp = self.client.get(
            '/api/v1/files',
            {
                'parent': str(self.sub.uuid),
                'mime_type': 'text/markdown',
                'descendants': '1',
            },
        )
        self.assertEqual(resp.status_code, 200)
        uuids = {item['uuid'] for item in resp.data}
        self.assertEqual(uuids, {
            str(self.note_sub.uuid),
            str(self.note_deep.uuid),
        })

    def test_descendants_with_group_folder(self):
        group = Group.objects.create(name='Team')
        self.user.groups.add(group)
        group_root = File.objects.create(
            owner=self.user, name='Team',
            node_type=File.NodeType.FOLDER,
            group=group,
        )
        group_sub = File.objects.create(
            owner=self.user, name='Docs',
            node_type=File.NodeType.FOLDER,
            parent=group_root, group=group,
        )
        group_note = File.objects.create(
            owner=self.user, name='doc.md',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
            parent=group_sub, group=group,
        )
        resp = self.client.get(
            '/api/v1/files',
            {
                'parent': str(group_root.uuid),
                'mime_type': 'text/markdown',
                'descendants': '1',
            },
        )
        self.assertEqual(resp.status_code, 200)
        uuids = {item['uuid'] for item in resp.data}
        self.assertIn(str(group_note.uuid), uuids)

    def test_descendants_does_not_leak_other_users_files(self):
        other = User.objects.create_user(username='bob', password='pass')
        sneaky = File.objects.create(
            owner=other, name='sneaky.md',
            node_type=File.NodeType.FILE,
            mime_type='text/markdown',
        )
        resp = self.client.get(
            '/api/v1/files',
            {
                'parent': str(self.root.uuid),
                'descendants': '1',
            },
        )
        uuids = {item['uuid'] for item in resp.data}
        self.assertNotIn(str(sneaky.uuid), uuids)

    def test_has_children_field_on_folder(self):
        resp = self.client.get(
            '/api/v1/files',
            {'parent': str(self.root.uuid), 'node_type': 'folder'},
        )
        self.assertEqual(resp.status_code, 200)
        sub_data = next(
            item for item in resp.data
            if item['uuid'] == str(self.sub.uuid)
        )
        self.assertTrue(sub_data['has_children'])

    def test_has_children_false_for_leaf_folder(self):
        resp = self.client.get(
            '/api/v1/files',
            {'parent': str(self.sub.uuid), 'node_type': 'folder'},
        )
        self.assertEqual(resp.status_code, 200)
        deep_data = next(
            item for item in resp.data
            if item['uuid'] == str(self.deep.uuid)
        )
        self.assertFalse(deep_data['has_children'])
