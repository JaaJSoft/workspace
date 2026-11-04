"""POST /api/v1/files/embeds - what a note's embedded files show to its reader."""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import Case, IntegerField, Value, When
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileScan, FileShare
from workspace.files.services.embeds import MAX_EMBEDS, UNAVAILABLE

User = get_user_model()
URL = "/api/v1/files/embeds"
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


class EmbedsEndpointTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="embed-owner", password="p")
        self.reader = User.objects.create_user(username="embed-reader", password="p")
        self.image = _file(self.owner, "photo.png", "png", "image/png")
        self.pdf = _file(self.owner, "report.pdf", "pdf", "application/pdf")
        self.audio = _file(self.owner, "voice.mp3", "mp3", "audio/mpeg")
        self.video = _file(self.owner, "clip.mp4", "mp4", "video/mp4")
        self.note = _file(self.owner, "Other.md", "markdown", "text/markdown")
        self.archive = _file(self.owner, "bundle.zip", "zip", "application/zip")
        self.client.force_authenticate(self.owner)

    def _resolve(self, *uuids):
        response = self.client.post(
            URL, {"uuids": [str(u) for u in uuids]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response.json()

    def test_route_name(self):
        self.assertEqual(reverse("file-embeds"), URL)

    def test_kind_follows_the_viewer_the_files_app_would_use(self):
        answers = self._resolve(
            self.image.uuid,
            self.pdf.uuid,
            self.audio.uuid,
            self.video.uuid,
            self.note.uuid,
            self.archive.uuid,
        )
        kinds = {key: answer["kind"] for key, answer in answers.items()}
        self.assertEqual(
            kinds,
            {
                str(self.image.uuid): "image",
                str(self.pdf.uuid): "pdf",
                str(self.audio.uuid): "audio",
                str(self.video.uuid): "video",
                str(self.note.uuid): "note",
                str(self.archive.uuid): "file",
            },
        )

    def test_pinned_viewer_wins_over_the_content_type(self):
        self.video.viewer = "audio"
        self.video.save(update_fields=["viewer"])
        self.assertEqual(
            self._resolve(self.video.uuid)[str(self.video.uuid)]["kind"], "audio"
        )

    def test_available_file_carries_what_the_editor_draws(self):
        answer = self._resolve(self.image.uuid)[str(self.image.uuid)]
        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["name"], "photo.png")
        self.assertEqual(answer["mime_type"], "image/png")
        self.assertEqual(answer["size"], 5)
        self.assertEqual(answer["icon"], "image")
        self.assertEqual(answer["sensitive"], 0)
        self.assertTrue(
            answer["content_url"].startswith(
                f"/api/v1/files/{self.image.uuid}/content?v="
            )
        )
        self.assertEqual(
            answer["download_url"], f"/api/v1/files/{self.image.uuid}/download"
        )
        self.assertIsNone(answer["thumbnail_url"])

    def test_content_url_changes_with_the_content(self):
        before = self._resolve(self.image.uuid)[str(self.image.uuid)]["content_url"]
        File.objects.filter(pk=self.image.pk).update(content_hash="f" * 64)
        after = self._resolve(self.image.uuid)[str(self.image.uuid)]["content_url"]
        self.assertNotEqual(before, after)

    def test_thumbnail_url_when_one_was_generated(self):
        File.objects.filter(pk=self.video.pk).update(has_thumbnail=True)
        answer = self._resolve(self.video.uuid)[str(self.video.uuid)]
        self.assertTrue(
            answer["thumbnail_url"].startswith(
                f"/api/v1/files/{self.video.uuid}/thumbnail?size=512&v="
            )
        )

    def test_sensitivity_level_is_passed_on_for_the_blur(self):
        with patch(
            "workspace.files.services.embeds.with_sensitivity",
            _sensitive(self.image.uuid, 3),
        ):
            answers = self._resolve(self.image.uuid, self.pdf.uuid)
        self.assertEqual(answers[str(self.image.uuid)]["sensitive"], 3)
        self.assertEqual(answers[str(self.pdf.uuid)]["sensitive"], 0)

    def test_folder_is_a_card_without_content(self):
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        answer = self._resolve(folder.uuid)[str(folder.uuid)]
        self.assertEqual(answer["kind"], "folder")
        self.assertIsNone(answer["content_url"])
        self.assertIsNone(answer["download_url"])

    def test_keys_are_canonical_uuids(self):
        answers = self._resolve(str(self.image.uuid).upper())
        self.assertEqual(list(answers), [str(self.image.uuid)])

    def test_unknown_uuid_is_unavailable(self):
        ghost = uuid.uuid4()
        self.assertEqual(self._resolve(ghost), {str(ghost): UNAVAILABLE})

    # -- the reader is not the author ---------------------------------------

    def test_reader_without_access_gets_the_same_answer_as_for_a_missing_file(self):
        self.client.force_authenticate(self.reader)
        ghost = uuid.uuid4()
        answers = self._resolve(self.image.uuid, ghost)
        self.assertEqual(answers[str(self.image.uuid)], answers[str(ghost)])
        self.assertEqual(answers[str(self.image.uuid)], {"status": "unavailable"})

    def test_reader_with_a_share_sees_the_file(self):
        FileShare.objects.create(
            file=self.pdf, shared_by=self.owner, shared_with=self.reader
        )
        self.client.force_authenticate(self.reader)
        answers = self._resolve(self.pdf.uuid, self.image.uuid)
        self.assertEqual(answers[str(self.pdf.uuid)]["status"], "ok")
        self.assertEqual(answers[str(self.image.uuid)], UNAVAILABLE)

    def test_group_file_follows_membership(self):
        group = Group.objects.create(name="embed-team")
        self.owner.groups.add(group)
        shared = _file(self.owner, "team.png", "png", "image/png", group=group)

        self.client.force_authenticate(self.reader)
        self.assertEqual(self._resolve(shared.uuid)[str(shared.uuid)], UNAVAILABLE)

        self.reader.groups.add(group)
        self.assertEqual(self._resolve(shared.uuid)[str(shared.uuid)]["status"], "ok")

    # -- targets that cannot be shown ---------------------------------------

    def test_trashed_file_names_itself_to_who_may_restore_it(self):
        self.pdf.soft_delete()
        answer = self._resolve(self.pdf.uuid)[str(self.pdf.uuid)]
        self.assertEqual(answer, {"status": "trashed", "name": "report.pdf"})

    def test_trashed_file_is_unavailable_to_a_share_recipient(self):
        FileShare.objects.create(
            file=self.pdf, shared_by=self.owner, shared_with=self.reader
        )
        self.pdf.soft_delete()
        self.client.force_authenticate(self.reader)
        self.assertEqual(self._resolve(self.pdf.uuid)[str(self.pdf.uuid)], UNAVAILABLE)

    def test_purged_file_is_unavailable(self):
        target = self.pdf.uuid
        self.pdf.delete(hard=True)
        self.assertEqual(self._resolve(target)[str(target)], UNAVAILABLE)

    @override_settings(**BLOCKING)
    def test_quarantined_file_gives_no_content(self):
        FileScan.objects.create(
            file=self.image,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at="2026-08-30T12:00:00Z",
        )
        answer = self._resolve(self.image.uuid)[str(self.image.uuid)]
        self.assertEqual(answer, {"status": "quarantined", "name": "photo.png"})

    # -- request shape ------------------------------------------------------

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        response = self.client.post(
            URL, {"uuids": [str(self.image.uuid)]}, format="json"
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_malformed_requests_are_refused(self):
        for body in ({}, {"uuids": []}, {"uuids": "x"}, {"uuids": ["not-a-uuid"]}):
            with self.subTest(body=body):
                response = self.client.post(URL, body, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_batch_is_capped(self):
        body = {"uuids": [str(uuid.uuid4()) for _ in range(MAX_EMBEDS + 1)]}
        response = self.client.post(URL, body, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_query_count_does_not_grow_with_the_batch(self):
        for f in (self.image, self.pdf, self.audio, self.video):
            FileShare.objects.create(
                file=f, shared_by=self.owner, shared_with=self.reader
            )
        self.client.force_authenticate(self.reader)
        with CaptureQueriesContext(connection) as one:
            self._resolve(self.image.uuid)
        with self.assertNumQueries(len(one.captured_queries)):
            self._resolve(
                self.image.uuid, self.pdf.uuid, self.audio.uuid, self.video.uuid
            )
