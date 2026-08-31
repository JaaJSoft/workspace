from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileScan
from workspace.files.search import search_files
from workspace.files.services.search_index import index_file

User = get_user_model()
BLOCKING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}
FLAGGING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "flag",
}


class RestEnforcementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="enf", password="p")
        self.client.force_authenticate(user=self.user)
        self.infected = self._file("bad.txt", FileScan.Status.INFECTED, "Unit.Test")
        self.clean = self._file("good.txt", FileScan.Status.CLEAN)

    def _file(self, name, scan_status=None, signature=""):
        f = File(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        f.content = ContentFile(b"body", name=name)
        f.size = 4
        f.save()
        if scan_status is not None:
            FileScan.objects.create(
                file=f,
                status=scan_status,
                signature=signature,
                scanned_at="2026-08-30T12:00:00Z",
            )
        return f

    def _folder_with_children(self):
        folder = File.objects.create(
            owner=self.user, name="Mixed", node_type=File.NodeType.FOLDER
        )
        clean_child = File(
            owner=self.user,
            name="child_good.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
            mime_type="text/plain",
        )
        clean_child.content = ContentFile(b"body", name="child_good.txt")
        clean_child.size = 4
        clean_child.save()
        FileScan.objects.create(
            file=clean_child,
            status=FileScan.Status.CLEAN,
            scanned_at="2026-08-30T12:00:00Z",
        )

        infected_child = File(
            owner=self.user,
            name="child_bad.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
            mime_type="text/plain",
        )
        infected_child.content = ContentFile(b"body", name="child_bad.txt")
        infected_child.size = 4
        infected_child.save()
        FileScan.objects.create(
            file=infected_child,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )
        return folder

    @override_settings(**BLOCKING)
    def test_content_of_an_infected_file_is_forbidden(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}/content")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("Unit.Test", str(resp.data))

    @override_settings(**BLOCKING)
    def test_download_of_an_infected_file_is_forbidden(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(**BLOCKING)
    def test_thumbnail_of_an_infected_file_is_forbidden(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}/thumbnail")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(**BLOCKING)
    def test_clean_file_is_unaffected(self):
        resp = self.client.get(f"/api/v1/files/{self.clean.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @override_settings(**BLOCKING)
    def test_unscanned_file_is_unaffected(self):
        pending = self._file("pending.txt")
        resp = self.client.get(f"/api/v1/files/{pending.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @override_settings(**FLAGGING)
    def test_flag_policy_leaves_the_file_downloadable(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_disabled_scanning_leaves_the_file_downloadable(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @override_settings(**BLOCKING)
    def test_bulk_download_omits_the_infected_file(self):
        resp = self.client.post(
            "/api/v1/files/bulk-download",
            {"uuids": [str(self.infected.uuid), str(self.clean.uuid)]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        archive = b"".join(resp.streaming_content)
        self.assertIn(b"good.txt", archive)
        self.assertNotIn(b"bad.txt", archive)

    @override_settings(**BLOCKING)
    def test_folder_download_omits_the_infected_descendant(self):
        folder = self._folder_with_children()
        resp = self.client.get(f"/api/v1/files/{folder.uuid}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        archive = b"".join(resp.streaming_content)
        self.assertIn(b"child_good.txt", archive)
        self.assertNotIn(b"child_bad.txt", archive)

    @override_settings(**BLOCKING)
    def test_bulk_download_of_a_folder_omits_the_infected_descendant(self):
        folder = self._folder_with_children()
        resp = self.client.post(
            "/api/v1/files/bulk-download",
            {"uuids": [str(folder.uuid)]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        archive = b"".join(resp.streaming_content)
        self.assertIn(b"child_good.txt", archive)
        self.assertNotIn(b"child_bad.txt", archive)

        # Under 'flag' policy the same descendant must stay in the archive -
        # this is the case that catches a hand-rolled status check creeping
        # into the ZIP path.
        with override_settings(**FLAGGING):
            resp = self.client.post(
                "/api/v1/files/bulk-download",
                {"uuids": [str(folder.uuid)]},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            archive = b"".join(resp.streaming_content)
            self.assertIn(b"child_bad.txt", archive)

    @override_settings(**BLOCKING)
    def test_serializer_reports_the_verdict_and_hides_the_viewer(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["scan_status"], FileScan.Status.INFECTED)
        self.assertEqual(resp.data["scan_signature"], "Unit.Test")
        self.assertTrue(resp.data["is_quarantined"])
        self.assertFalse(resp.data["is_viewable"])
        self.assertIsNone(resp.data["content_url"])

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_serializer_reports_nothing_when_disabled(self):
        resp = self.client.get(f"/api/v1/files/{self.infected.uuid}")
        self.assertEqual(resp.data["scan_status"], "")

    @override_settings(**BLOCKING)
    def test_actions_endpoint_omits_download_and_view(self):
        resp = self.client.post(
            "/api/v1/files/actions",
            {"uuids": [str(self.infected.uuid)]},
            format="json",
        )
        ids = {a["id"] for a in resp.data[str(self.infected.uuid)]}
        self.assertNotIn("download", ids)
        self.assertNotIn("view", ids)
        self.assertNotIn("open_new_tab", ids)

    @override_settings(**BLOCKING)
    def test_actions_endpoint_still_offers_them_for_a_clean_file(self):
        resp = self.client.post(
            "/api/v1/files/actions",
            {"uuids": [str(self.clean.uuid)]},
            format="json",
        )
        ids = {a["id"] for a in resp.data[str(self.clean.uuid)]}
        self.assertIn("download", ids)


class ShareLinkEnforcementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lnk", password="p")
        self.file = File(
            owner=self.user,
            name="bad.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.file.content = ContentFile(b"body", name="bad.txt")
        self.file.size = 4
        self.file.save()
        FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )
        from workspace.files.models import FileShareLink

        self.link = FileShareLink.objects.create(
            file=self.file, created_by=self.user, token="tok-enforcement-1"
        )

    @override_settings(**BLOCKING)
    def test_public_content_is_forbidden(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.link.token}/content")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(**BLOCKING)
    def test_public_download_is_forbidden(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.link.token}/download")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_public_download_works_when_scanning_is_off(self):
        resp = self.client.get(f"/api/v1/files/shared/{self.link.token}/download")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class WopiEnforcementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wop", password="p")
        self.file = File(
            owner=self.user,
            name="bad.docx",
            node_type=File.NodeType.FILE,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.file.content = ContentFile(b"body", name="bad.docx")
        self.file.size = 4
        self.file.save()
        FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )

    def _contents_url(self):
        from django.urls import reverse

        from workspace.files.services.wopi.tokens import mint_access_token

        token = mint_access_token(self.user, self.file.uuid, can_write=False)
        url = reverse("wopi-file-contents", kwargs={"uuid": self.file.uuid})
        return f"{url}?access_token={token}"

    @override_settings(**BLOCKING)
    def test_get_file_is_not_found_for_a_quarantined_document(self):
        resp = self.client.get(self._contents_url())
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_get_file_works_when_scanning_is_off(self):
        resp = self.client.get(self._contents_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class SearchExclusionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="srch", password="p")
        self.clean = self._file("quarterly-report.txt")
        self.infected = self._file("quarterly-invoice.txt")
        FileScan.objects.create(
            file=self.infected,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )

    def _file(self, name):
        f = File(owner=self.user, name=name, node_type=File.NodeType.FILE)
        f.content = ContentFile(b"body", name=name)
        f.size = 4
        f.save()
        index_file(f)
        return f

    @override_settings(**BLOCKING)
    def test_blocked_file_is_absent_from_search(self):
        names = {r.name for r in search_files("quarterly", self.user, 20)}
        self.assertIn("quarterly-report.txt", names)
        self.assertNotIn("quarterly-invoice.txt", names)

    @override_settings(**FLAGGING)
    def test_flagged_file_stays_searchable(self):
        """Flag mode annotates rather than disappears; hiding it would be a
        different policy than the one the administrator chose."""
        names = {r.name for r in search_files("quarterly", self.user, 20)}
        self.assertIn("quarterly-invoice.txt", names)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_everything_is_searchable_when_scanning_is_off(self):
        names = {r.name for r in search_files("quarterly", self.user, 20)}
        self.assertEqual(len(names), 2)

    @override_settings(**BLOCKING)
    def test_unscanned_files_are_still_returned(self):
        """A file with no scan row is never excluded.

        The NOT IN subquery cannot itself go wrong here: FileScan.file is a
        non-nullable FK, so the trap that would empty every page - one NULL
        making the predicate UNKNOWN for every row - is prevented by the
        schema, not by this test.
        """
        self._file("quarterly-budget.txt")
        names = {r.name for r in search_files("quarterly", self.user, 20)}
        self.assertIn("quarterly-budget.txt", names)


class ViewerPanelEnforcementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="vwr", password="p")
        self.client.force_login(self.user)
        self.file = File(
            owner=self.user,
            name="bad.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.file.content = ContentFile(b"body", name="bad.txt")
        self.file.size = 4
        self.file.save()
        FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )

    @override_settings(**BLOCKING)
    def test_viewer_panel_is_refused_for_a_quarantined_file(self):
        resp = self.client.get(f"/files/view/{self.file.uuid}")
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"Quarantined", resp.content)
        # The modal reads this marker to hide its own Download button -
        # $ajax doesn't expose the HTTP status to the component.
        self.assertIn(b"data-viewer-blocked", resp.content)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_viewer_panel_renders_when_scanning_is_off(self):
        resp = self.client.get(f"/files/view/{self.file.uuid}")
        self.assertEqual(resp.status_code, 200)

    @override_settings(**BLOCKING)
    def test_a_stranger_still_gets_404_not_403(self):
        """The guard must not leak existence to someone without access."""
        other = User.objects.create_user(username="stranger", password="p")
        self.client.force_login(other)
        resp = self.client.get(f"/files/view/{self.file.uuid}")
        self.assertEqual(resp.status_code, 404)
