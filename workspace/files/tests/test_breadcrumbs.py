"""A folder shared with someone shows them its trail from the shared root.

Opening /files/<folder> built the breadcrumbs from the folder's full path, so
the recipient of a shared folder read the names (and ids) of the owner's
private folders above it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import FileShare
from workspace.files.services import FileService

User = get_user_model()


class BreadcrumbVisibilityTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.private = FileService.create_folder(self.alice, "MedicalPrivate")
        self.shared = FileService.create_folder(
            self.alice, "SharedReports", parent=self.private
        )

    def tearDown(self):
        cache.clear()

    def _share(self, node, user, permission="ro"):
        FileShare.objects.create(
            file=node, shared_with=user, shared_by=self.alice, permission=permission
        )

    def test_a_recipient_does_not_see_the_owners_folders_above(self):
        for permission in ("ro", "rw"):
            with self.subTest(permission=permission):
                FileShare.objects.all().delete()
                self._share(self.shared, self.bob, permission)
                self.client.force_login(self.bob)

                response = self.client.get(f"/files/{self.shared.uuid}")

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "SharedReports")
                self.assertNotContains(response, "MedicalPrivate")
                self.assertNotContains(response, str(self.private.uuid))

    def test_a_group_folder_shared_outside_the_group_hides_the_group_tree(self):
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        root = FileService.create_folder(self.alice, "TeamRootSecret", group=team)
        sub = FileService.create_folder(self.alice, "TeamShared", parent=root)
        self._share(sub, self.bob)
        self.client.force_login(self.bob)

        response = self.client.get(f"/files/{sub.uuid}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TeamShared")
        self.assertNotContains(response, "TeamRootSecret")
        self.assertNotContains(response, str(root.uuid))

    def test_an_ancestor_shared_too_stays_in_the_trail(self):
        self._share(self.private, self.bob)
        self._share(self.shared, self.bob)
        self.client.force_login(self.bob)

        response = self.client.get(f"/files/{self.shared.uuid}")

        self.assertContains(response, f"/files/{self.private.uuid}")

    def test_the_owner_keeps_the_whole_trail(self):
        self.client.force_login(self.alice)

        response = self.client.get(f"/files/{self.shared.uuid}")

        self.assertContains(response, "MedicalPrivate")
        self.assertContains(response, f"/files/{self.private.uuid}")

    def test_a_group_member_keeps_the_whole_trail(self):
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        self.bob.groups.add(team)
        root = FileService.create_folder(self.alice, "TeamRootVisible", group=team)
        sub = FileService.create_folder(self.alice, "Sub", parent=root)
        self.client.force_login(self.bob)

        response = self.client.get(f"/files/{sub.uuid}")

        self.assertContains(response, f"/files/{root.uuid}")
        self.assertContains(response, "TeamRootVisible")
