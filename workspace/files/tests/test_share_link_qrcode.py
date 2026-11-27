"""A public link as a QR code: what the code holds, and who gets it."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.common.qr import make_qr, qr_svg
from workspace.common.tests.qr import read_qr
from workspace.files.models import File, FileShare
from workspace.files.services import tree
from workspace.files.services.sharing import create_share_link, share_file

User = get_user_model()
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def qrcode_path(link_uuid):
    return f"/api/v1/files/share-links/{link_uuid}/qrcode"


def address_of(link):
    return f"http://testserver/files/shared/{link.token}"


class ShareLinkQRCodeTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="x")
        self.file = tree.create_file(self.owner, "report.pdf")
        self.link = create_share_link(self.file, acting_user=self.owner)
        self.client.force_authenticate(self.owner)

    def test_svg_by_default(self):
        response = self.client.get(qrcode_path(self.link.uuid))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content)

    def test_png_on_request(self):
        response = self.client.get(qrcode_path(self.link.uuid), {"kind": "png"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(PNG_MAGIC))

    def scanned(self, link):
        """What a reader gets from the PNG served for *link*."""
        response = self.client.get(qrcode_path(link.uuid), {"kind": "png"})
        return read_qr(response.content)

    def test_scanning_the_code_gives_the_address_the_link_opens_at(self):
        self.assertEqual(self.scanned(self.link), address_of(self.link))

    def test_the_svg_is_the_same_code_as_the_png(self):
        response = self.client.get(qrcode_path(self.link.uuid))
        self.assertEqual(
            response.content.decode(), qr_svg(make_qr(address_of(self.link)))
        )

    def test_a_password_never_reaches_the_code(self):
        protected = create_share_link(
            self.file, acting_user=self.owner, password="correct horse"
        )
        self.assertEqual(self.scanned(protected), address_of(protected))

    def test_a_folder_link_has_its_code_too(self):
        folder = tree.create_folder(self.owner, "Holidays")
        link = create_share_link(folder, acting_user=self.owner)
        self.assertEqual(self.scanned(link), address_of(link))

    def test_named_after_the_node_and_shown_inline(self):
        response = self.client.get(qrcode_path(self.link.uuid), {"kind": "png"})
        self.assertEqual(
            response["Content-Disposition"], 'inline; filename="report.pdf-qr.png"'
        )

    def test_never_stored_by_the_browser(self):
        response = self.client.get(qrcode_path(self.link.uuid))
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_unknown_kind_is_400(self):
        response = self.client.get(qrcode_path(self.link.uuid), {"kind": "webp"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.data)

    def test_a_listed_link_says_where_its_code_is(self):
        listed = self.client.get(f"/api/v1/files/{self.file.uuid}/share-links").data
        self.assertEqual(listed[0]["qrcode_url"], qrcode_path(self.link.uuid))
        self.assertEqual(self.client.get(listed[0]["qrcode_url"]).status_code, 200)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        response = self.client.get(qrcode_path(self.link.uuid))
        self.assertIn(response.status_code, (401, 403))


class OutOfReachLinkAnswersLikeAnAbsentOneTests(APITestCase):
    """Whoever holds a link's uuid learns nothing from asking for its code:
    not theirs and not there are one answer."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)

    def assertAnswersLikeAbsent(self, user, link):
        self.client.force_authenticate(user)
        hidden = self.client.get(qrcode_path(link.uuid))
        absent = self.client.get(qrcode_path(uuid.uuid4()))
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_a_link_of_someone_elses_file(self):
        mine = tree.create_file(self.alice, "mine.txt")
        link = create_share_link(mine, acting_user=self.alice)
        self.assertAnswersLikeAbsent(self.bob, link)

    def test_a_link_of_a_file_shared_with_the_caller(self):
        mine = tree.create_file(self.alice, "mine.txt")
        link = create_share_link(mine, acting_user=self.alice)
        share_file(
            mine,
            target_user=self.bob,
            permission=FileShare.Permission.READ_WRITE,
            acting_user=self.alice,
        )
        self.assertAnswersLikeAbsent(self.bob, link)

    def test_a_link_another_member_made_in_the_callers_group(self):
        root = tree.create_folder(self.alice, "TeamRoot", group=self.team)
        link = create_share_link(root, acting_user=self.alice)
        self.assertAnswersLikeAbsent(self.bob, link)

    def test_a_link_on_a_node_of_a_group_its_owner_left(self):
        root = tree.create_folder(self.bob, "TeamRoot", group=self.team)
        sub = tree.create_folder(self.alice, "AliceSub", parent=root)
        self.alice.groups.remove(self.team)
        # Leaving revokes the links made there; one that outlived it, however
        # it did, still answers nothing to its former owner.
        link = create_share_link(sub, acting_user=self.alice)
        self.assertAnswersLikeAbsent(self.alice, link)

    def test_a_link_of_a_trashed_file(self):
        mine = tree.create_file(self.alice, "mine.txt")
        link = create_share_link(mine, acting_user=self.alice)
        File.objects.filter(pk=mine.pk).update(deleted_at=timezone.now())
        self.assertAnswersLikeAbsent(self.alice, link)
