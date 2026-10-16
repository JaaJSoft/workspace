"""A pin only shows a folder its owner can still open.

The sidebar used to list every pin on a live folder, so a member who left a
group - or a recipient whose share was revoked - kept seeing the folder, under
whatever name its owners gave it afterwards.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from rest_framework.test import APITestCase

from workspace.files.models import FileShare, PinnedFolder
from workspace.files.services import FileService
from workspace.files.services.pins import visible_pins

User = get_user_model()


class _PinFixture:
    def _fixture(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.root = FileService.create_folder(self.bob, "TeamRoot", group=self.team)
        self.group_sub = FileService.create_folder(
            self.bob, "Payroll", parent=self.root
        )
        self.mine = FileService.create_folder(self.alice, "MyFolder")
        PinnedFolder.objects.create(owner=self.alice, folder=self.mine, position=1)
        PinnedFolder.objects.create(owner=self.alice, folder=self.group_sub, position=2)

    def tearDown(self):
        cache.clear()


class SharedPinVisibilityTests(_PinFixture, APITestCase):
    """A revoked share leaves the pin behind: the views must not list it."""

    def setUp(self):
        self._fixture()
        self.bob_private = FileService.create_folder(self.bob, "BobPrivate")
        share = FileShare.objects.create(
            file=self.bob_private,
            shared_with=self.alice,
            shared_by=self.bob,
            permission="ro",
        )
        PinnedFolder.objects.create(
            owner=self.alice, folder=self.bob_private, position=3
        )
        share.delete()
        self.bob_private.name = "BobPrivate-renamed"
        self.bob_private.save(update_fields=["name"])
        self.client.force_login(self.alice)

    def test_visible_pins_drops_a_folder_out_of_reach(self):
        self.assertEqual(
            [pin.folder_id for pin in visible_pins(self.alice)],
            [self.mine.pk, self.group_sub.pk],
        )

    def test_pinned_sidebar_partial_hides_it(self):
        response = self.client.get("/files/pinned")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MyFolder")
        self.assertNotContains(response, "BobPrivate-renamed")

    def test_files_page_hides_it(self):
        response = self.client.get("/files")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BobPrivate-renamed")

    def test_pinned_api_hides_it(self):
        response = self.client.get("/api/v1/files/pinned")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["name"] for item in response.json()], ["MyFolder", "Payroll"]
        )


class GroupDeparturePinTests(_PinFixture, APITestCase):
    def setUp(self):
        self._fixture()

    def test_leaving_the_group_drops_the_pins_on_its_folders(self):
        bob_pin = PinnedFolder.objects.create(owner=self.bob, folder=self.group_sub)

        self.alice.groups.remove(self.team)

        self.assertEqual(
            set(PinnedFolder.objects.values_list("pk", flat=True)),
            {
                PinnedFolder.objects.get(owner=self.alice, folder=self.mine).pk,
                bob_pin.pk,
            },
        )

    def test_clearing_the_users_groups_drops_them(self):
        self.alice.groups.clear()

        self.assertFalse(
            PinnedFolder.objects.filter(
                owner=self.alice, folder=self.group_sub
            ).exists()
        )

    def test_a_former_member_no_longer_sees_the_renamed_folder(self):
        self.client.force_login(self.alice)
        self.alice.groups.remove(self.team)
        self.group_sub.name = "Payroll-2027-layoffs"
        self.group_sub.save(update_fields=["name"])

        for url in ("/files", "/files/pinned"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Payroll-2027-layoffs")

    def test_pins_survive_a_membership_that_stays(self):
        other = Group.objects.create(name="Other")
        self.alice.groups.add(other)
        self.alice.groups.remove(other)

        self.assertEqual(PinnedFolder.objects.filter(owner=self.alice).count(), 2)
