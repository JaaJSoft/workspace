from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileShare
from workspace.files.services.files import FilePermission, FileService

User = get_user_model()


class FileAuthzMixin:
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.group = Group.objects.create(name='Engineering')
        self.alice.groups.add(self.group)

    def _make_file(self, owner, name='test.txt', parent=None, group=None):
        return File.objects.create(
            owner=owner, name=name, node_type=File.NodeType.FILE,
            parent=parent, group=group,
        )

    def _make_folder(self, owner, name='folder', parent=None, group=None):
        return File.objects.create(
            owner=owner, name=name, node_type=File.NodeType.FOLDER,
            parent=parent, group=group,
        )


# ── accessible_files_q ─────────────────────────────────────────

class AccessibleFilesQTests(FileAuthzMixin, TestCase):

    def test_includes_owned_files(self):
        f = self._make_file(self.alice)
        qs = File.objects.filter(FileService.accessible_files_q(self.alice))
        self.assertIn(f, list(qs))

    def test_excludes_other_users_files(self):
        f = self._make_file(self.bob)
        qs = File.objects.filter(FileService.accessible_files_q(self.alice))
        self.assertNotIn(f, list(qs))

    def test_includes_group_files(self):
        f = self._make_file(self.bob, group=self.group)
        qs = File.objects.filter(FileService.accessible_files_q(self.alice))
        self.assertIn(f, list(qs))

    def test_excludes_group_files_when_not_member(self):
        f = self._make_file(self.alice, group=self.group)
        qs = File.objects.filter(FileService.accessible_files_q(self.bob))
        # Bob is not in the group, but alice owns it - bob should not see it
        # unless it's shared. Bob is not the owner either.
        self.assertNotIn(f, list(qs))

    def test_includes_shared_files(self):
        f = self._make_file(self.bob)
        FileShare.objects.create(
            file=f, shared_by=self.bob, shared_with=self.alice, permission='ro',
        )
        qs = File.objects.filter(FileService.accessible_files_q(self.alice))
        self.assertIn(f, list(qs))

    def test_includes_deleted_files_owned(self):
        """accessible_files_q does NOT filter deleted_at per docstring."""
        f = self._make_file(self.alice)
        f.deleted_at = timezone.now()
        f.save()
        qs = File.objects.filter(FileService.accessible_files_q(self.alice))
        self.assertIn(f, list(qs))

    def test_no_duplicates_when_owned_and_shared(self):
        f = self._make_file(self.alice)
        FileShare.objects.create(
            file=f, shared_by=self.alice, shared_with=self.alice, permission='rw',
        )
        qs = File.objects.filter(FileService.accessible_files_q(self.alice))
        self.assertEqual(qs.distinct().count(), 1)


# ── user_files_qs ──────────────────────────────────────────────

class UserFilesQsTests(FileAuthzMixin, TestCase):

    def test_includes_owned_personal_files(self):
        f = self._make_file(self.alice)
        qs = FileService.user_files_qs(self.alice)
        self.assertIn(f, list(qs))

    def test_excludes_group_files(self):
        f = self._make_file(self.alice, group=self.group)
        qs = FileService.user_files_qs(self.alice)
        self.assertNotIn(f, list(qs))

    def test_excludes_deleted_files(self):
        f = self._make_file(self.alice)
        f.deleted_at = timezone.now()
        f.save()
        qs = FileService.user_files_qs(self.alice)
        self.assertNotIn(f, list(qs))

    def test_excludes_other_users_files(self):
        self._make_file(self.bob)
        qs = FileService.user_files_qs(self.alice)
        self.assertEqual(qs.count(), 0)


# ── user_group_files_qs ────────────────────────────────────────

class UserGroupFilesQsTests(FileAuthzMixin, TestCase):

    def test_includes_group_files_for_member(self):
        f = self._make_file(self.bob, group=self.group)
        qs = FileService.user_group_files_qs(self.alice)
        self.assertIn(f, list(qs))

    def test_excludes_files_from_non_member_groups(self):
        other_group = Group.objects.create(name='Design')
        f = self._make_file(self.bob, group=other_group)
        qs = FileService.user_group_files_qs(self.alice)
        self.assertNotIn(f, list(qs))

    def test_excludes_deleted_group_files(self):
        f = self._make_file(self.bob, group=self.group)
        f.deleted_at = timezone.now()
        f.save()
        qs = FileService.user_group_files_qs(self.alice)
        self.assertNotIn(f, list(qs))

    def test_excludes_personal_files(self):
        self._make_file(self.alice)
        qs = FileService.user_group_files_qs(self.alice)
        self.assertEqual(qs.count(), 0)


# ── get_permission ──────────────────────────────────────────────

class GetPermissionTests(FileAuthzMixin, TestCase):

    def test_owner_gets_manage(self):
        f = self._make_file(self.alice)
        self.assertEqual(FileService.get_permission(self.alice, f), FilePermission.MANAGE)

    def test_group_member_gets_edit(self):
        f = self._make_file(self.bob, group=self.group)
        self.assertEqual(FileService.get_permission(self.alice, f), FilePermission.EDIT)

    def test_shared_rw_gets_write(self):
        f = self._make_file(self.bob)
        FileShare.objects.create(
            file=f, shared_by=self.bob, shared_with=self.alice, permission='rw',
        )
        self.assertEqual(FileService.get_permission(self.alice, f), FilePermission.WRITE)

    def test_shared_ro_gets_view(self):
        f = self._make_file(self.bob)
        FileShare.objects.create(
            file=f, shared_by=self.bob, shared_with=self.alice, permission='ro',
        )
        self.assertEqual(FileService.get_permission(self.alice, f), FilePermission.VIEW)

    def test_no_relation_returns_none(self):
        f = self._make_file(self.bob)
        self.assertIsNone(FileService.get_permission(self.alice, f))

    def test_deleted_file_returns_none_for_non_owner(self):
        f = self._make_file(self.bob, group=self.group)
        f.deleted_at = timezone.now()
        f.save()
        self.assertIsNone(FileService.get_permission(self.alice, f))

    def test_owner_still_sees_deleted_files(self):
        f = self._make_file(self.alice)
        f.deleted_at = timezone.now()
        f.save()
        self.assertEqual(FileService.get_permission(self.alice, f), FilePermission.MANAGE)

    def test_permission_ordering(self):
        """Permissions should be comparable: MANAGE > EDIT > WRITE > VIEW."""
        self.assertGreater(FilePermission.MANAGE, FilePermission.EDIT)
        self.assertGreater(FilePermission.EDIT, FilePermission.WRITE)
        self.assertGreater(FilePermission.WRITE, FilePermission.VIEW)


# ── can_access ──────────────────────────────────────────────────

class CanAccessTests(FileAuthzMixin, TestCase):

    def test_owner_can_access(self):
        f = self._make_file(self.alice)
        self.assertTrue(FileService.can_access(self.alice, f))

    def test_group_member_can_access(self):
        f = self._make_file(self.bob, group=self.group)
        self.assertTrue(FileService.can_access(self.alice, f))

    def test_shared_user_can_access(self):
        f = self._make_file(self.bob)
        FileShare.objects.create(
            file=f, shared_by=self.bob, shared_with=self.alice, permission='ro',
        )
        self.assertTrue(FileService.can_access(self.alice, f))

    def test_unrelated_user_cannot_access(self):
        f = self._make_file(self.bob)
        self.assertFalse(FileService.can_access(self.alice, f))

    def test_deleted_file_not_accessible_to_non_owner(self):
        f = self._make_file(self.bob, group=self.group)
        f.deleted_at = timezone.now()
        f.save()
        self.assertFalse(FileService.can_access(self.alice, f))
