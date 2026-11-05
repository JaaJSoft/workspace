"""File previews: who may see which file inside a note or a list, and how.

A preview is resolved for the user asking, never for the author of whatever
holds it (services/previews.py); the batch view /files/previews answers a
<file-preview> batch with each preview in the slot it named.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import Case, IntegerField, Value, When
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.files.models import File, FileScan, FileShare
from workspace.files.services import previews
from workspace.files.ui.viewers import (
    MAX_PREVIEWS,
    VIEWER_PANEL_ID,
    render_viewer_panel,
    requested_target,
)

User = get_user_model()
URL = "/files/previews"
BLOCKING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}


def _file(owner, name, label, mime_type, **fields):
    f = File(
        owner=owner,
        name=name,
        node_type=File.NodeType.FILE,
        type=label,
        mime_type=mime_type,
        **fields,
    )
    f.content = ContentFile(b"bytes", name=name)
    f.size = 5
    f.save()
    return f


def _sensitive(file_uuid, level):
    """A stand-in for the sensitivity provider: *file_uuid* is at *level*."""

    def annotate(queryset):
        return queryset.annotate(
            sensitive_level=Case(
                When(uuid=file_uuid, then=Value(level)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    return annotate


class PreviewableFilesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="pv-owner", password="p")
        self.reader = User.objects.create_user(username="pv-reader", password="p")
        self.image = _file(self.owner, "photo.png", "png", "image/png")
        self.pdf = _file(self.owner, "report.pdf", "pdf", "application/pdf")

    def _states(self, user, *files):
        found = previews.previewable_files(user, [f.uuid for f in files])
        return {key: state for key, (state, _file_obj) in found.items()}

    def test_owner_sees_their_files(self):
        self.assertEqual(
            self._states(self.owner, self.image, self.pdf),
            {self.image.uuid: previews.OK, self.pdf.uuid: previews.OK},
        )

    def test_reader_without_access_gets_what_a_missing_file_gets(self):
        ghost = uuid.uuid4()
        found = previews.previewable_files(self.reader, [self.image.uuid, ghost])
        self.assertEqual(found, {})

    def test_share_grants_the_shared_file_only(self):
        FileShare.objects.create(
            file=self.pdf, shared_by=self.owner, shared_with=self.reader
        )
        self.assertEqual(
            self._states(self.reader, self.image, self.pdf),
            {self.pdf.uuid: previews.OK},
        )

    def test_group_file_follows_membership(self):
        group = Group.objects.create(name="pv-team")
        self.owner.groups.add(group)
        shared = _file(self.owner, "team.png", "png", "image/png", group=group)
        self.assertEqual(self._states(self.reader, shared), {})
        self.reader.groups.add(group)
        self.assertEqual(self._states(self.reader, shared), {shared.uuid: previews.OK})

    def test_trashed_file_is_named_only_to_who_may_restore_it(self):
        FileShare.objects.create(
            file=self.pdf, shared_by=self.owner, shared_with=self.reader
        )
        self.pdf.soft_delete()
        self.assertEqual(
            self._states(self.owner, self.pdf), {self.pdf.uuid: previews.TRASHED}
        )
        self.assertEqual(self._states(self.reader, self.pdf), {})

    def test_purged_file_is_absent(self):
        target = self.pdf.uuid
        self.pdf.delete(hard=True)
        self.assertEqual(previews.previewable_files(self.owner, [target]), {})

    @override_settings(**BLOCKING)
    def test_quarantined_file(self):
        FileScan.objects.create(
            file=self.image,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )
        self.assertEqual(
            self._states(self.owner, self.image),
            {self.image.uuid: previews.QUARANTINED},
        )

    def test_content_url_changes_with_the_content(self):
        before = previews.content_url(self.image)
        self.image.content_hash = "f" * 64
        self.assertNotEqual(before, previews.content_url(self.image))

    def test_query_count_does_not_grow_with_the_batch(self):
        for f in (self.image, self.pdf):
            FileShare.objects.create(
                file=f, shared_by=self.owner, shared_with=self.reader
            )
        with CaptureQueriesContext(connection) as one:
            previews.previewable_files(self.reader, [self.image.uuid])
        with self.assertNumQueries(len(one.captured_queries)):
            previews.previewable_files(self.reader, [self.image.uuid, self.pdf.uuid])


class FilePreviewsViewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="pvv-owner", password="p")
        self.reader = User.objects.create_user(username="pvv-reader", password="p")
        self.image = _file(self.owner, "photo.png", "png", "image/png")
        self.video = _file(self.owner, "clip.mp4", "mp4", "video/mp4")
        self.audio = _file(self.owner, "voice.mp3", "mp3", "audio/mpeg")
        self.pdf = _file(self.owner, "report.pdf", "pdf", "application/pdf")
        self.archive = _file(self.owner, "bundle.zip", "zip", "application/zip")
        self.client.force_login(self.owner)

    def _get(self, *pairs):
        targets = ",".join(target for target, _ in pairs)
        uuids = ",".join(str(value) for _, value in pairs)
        return self.client.get(URL, {"uuids": uuids, "targets": targets})

    def test_route(self):
        self.assertEqual(reverse("files_ui:file_previews"), URL)

    def test_each_preview_comes_back_in_its_slot(self):
        response = self._get(("fp-1", self.image.uuid), ("fp-2", self.pdf.uuid))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<div id="fp-1" class="file-preview-slot">', html)
        self.assertIn('<div id="fp-2" class="file-preview-slot">', html)
        self.assertLess(html.index('id="fp-1"'), html.index('id="fp-2"'))

    def test_image_preview_draws_the_content_and_falls_back_to_the_thumbnail(self):
        File.objects.filter(pk=self.image.pk).update(has_thumbnail=True)
        html = self._get(("fp-1", self.image.uuid)).content.decode()
        self.assertIn("file-preview-image", html)
        self.assertIn(f'src="/api/v1/files/{self.image.uuid}/content?v=', html)
        self.assertIn(
            f'data-fallback-src="/api/v1/files/{self.image.uuid}/thumbnail?size=512',
            html,
        )
        self.assertIn("data-preview-openable", html)

    def test_video_preview_uses_the_poster(self):
        File.objects.filter(pk=self.video.pk).update(has_thumbnail=True)
        html = self._get(("fp-1", self.video.uuid)).content.decode()
        self.assertIn("file-preview-video", html)
        self.assertIn(f'poster="/api/v1/files/{self.video.uuid}/thumbnail', html)

    def test_audio_preview_plays_in_place(self):
        html = self._get(("fp-1", self.audio.uuid)).content.decode()
        self.assertIn("<audio", html)

    def test_files_no_viewer_draws_inline_get_the_card(self):
        for f in (self.pdf, self.archive):
            with self.subTest(name=f.name):
                html = self._get(("fp-1", f.uuid)).content.decode()
                self.assertIn("file-preview-card", html)
                self.assertIn(f.name, html)
                self.assertIn("data-preview-openable", html)

    def test_pinned_viewer_wins(self):
        self.video.viewer = "audio"
        self.video.save(update_fields=["viewer"])
        html = self._get(("fp-1", self.video.uuid)).content.decode()
        self.assertIn("<audio", html)

    def test_folder_is_a_card_that_does_not_open(self):
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        html = self._get(("fp-1", folder.uuid)).content.decode()
        self.assertIn("file-preview-card", html)
        self.assertNotIn("data-preview-openable", html)

    def test_sensitivity_level_reaches_the_blur(self):
        with patch(
            "workspace.files.services.previews.with_sensitivity",
            _sensitive(self.image.uuid, 3),
        ):
            html = self._get(("fp-1", self.image.uuid)).content.decode()
        self.assertIn('data-sensitive="3"', html)

    def test_unreachable_and_missing_files_leave_their_slot_empty(self):
        self.client.force_login(self.reader)
        response = self._get(("fp-1", self.image.uuid), ("fp-2", uuid.uuid4()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_trashed_file_shows_a_placeholder_and_no_content(self):
        self.pdf.soft_delete()
        html = self._get(("fp-1", self.pdf.uuid)).content.decode()
        self.assertIn("file-preview-trashed", html)
        self.assertIn("report.pdf", html)
        self.assertNotIn("/content", html)
        self.assertNotIn("data-preview-openable", html)

    @override_settings(**BLOCKING)
    def test_quarantined_file_shows_a_placeholder_and_no_content(self):
        FileScan.objects.create(
            file=self.image,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )
        html = self._get(("fp-1", self.image.uuid)).content.decode()
        self.assertIn("file-preview-blocked", html)
        self.assertNotIn("/content", html)

    def test_names_are_escaped(self):
        self.archive.name = '<img src=x onerror="alert(1)">.zip'
        self.archive.save(update_fields=["name"])
        html = self._get(("fp-1", self.archive.uuid)).content.decode()
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)

    def test_malformed_batches_are_refused(self):
        for query in (
            {},
            {"uuids": str(self.image.uuid)},
            {"uuids": str(self.image.uuid), "targets": "a,b"},
            {"uuids": "not-a-uuid", "targets": "fp-1"},
            {"uuids": str(self.image.uuid), "targets": '"><script>'},
            {"uuids": str(self.image.uuid), "targets": "1starts-with-a-digit"},
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(URL, query).status_code, 400)

    def test_batch_is_capped(self):
        pairs = [(f"fp-{i}", uuid.uuid4()) for i in range(MAX_PREVIEWS + 1)]
        self.assertEqual(self._get(*pairs).status_code, 400)

    def test_requires_a_session(self):
        self.client.logout()
        response = self._get(("fp-1", self.image.uuid))
        self.assertEqual(response.status_code, 302)


class ViewerPanelIdTests(TestCase):
    def _request(self, target=None):
        headers = {"HTTP_X_ALPINE_TARGET": target} if target is not None else {}
        return RequestFactory().get("/files/view/x", **headers)

    def test_the_panel_takes_the_id_the_request_names(self):
        html = render_viewer_panel("<p>x</p>", self._request("viewer-panel-modal"))
        self.assertTrue(html.startswith('<div id="viewer-panel-modal"'))

    def test_default_id_without_a_usable_target(self):
        for target in (None, "", "a b", '"><script>', "9lives"):
            with self.subTest(target=target):
                self.assertIsNone(requested_target(self._request(target)))
                html = render_viewer_panel("<p>x</p>", self._request(target))
                self.assertTrue(html.startswith(f'<div id="{VIEWER_PANEL_ID}"'))

    def test_view_file_answers_in_the_requested_panel(self):
        owner = User.objects.create_user(username="pvp-owner", password="p")
        pdf = _file(owner, "report.pdf", "pdf", "application/pdf")
        self.client.force_login(owner)
        response = self.client.get(
            f"/files/view/{pdf.uuid}", HTTP_X_ALPINE_TARGET="viewer-panel-modal"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.content.decode().startswith('<div id="viewer-panel-modal"')
        )
