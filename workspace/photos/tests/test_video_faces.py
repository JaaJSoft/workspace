"""Faces in videos: one face per person seen in the video, grouped like a photo's."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.tests.videos import requires_ffmpeg
from workspace.files.ui.viewers import AudioViewer
from workspace.photos.models import Face, FaceAnalysis, FaceCluster
from workspace.photos.queries import face_progress
from workspace.photos.services.face_analysis import (
    analyze_faces,
    is_face_candidate,
    pending_faces_qs,
)
from workspace.photos.services.face_grouping import cluster_owner
from workspace.photos.services.face_purge import purge_owner_faces
from workspace.photos.services.handlers import _queue_faces
from workspace.photos.tasks import queue_owner_faces

from .faces import FacesTestMixin, faces_on, opt_in
from .images import ALICE, BOB, faces_png, faces_video, upload

User = get_user_model()

_NO_FFMPEG = (
    patch("workspace.files.services.ffmpeg.FFMPEG", None),
    patch("workspace.files.services.ffmpeg.FFPROBE", None),
)


def _without_ffmpeg(test):
    for patcher in _NO_FFMPEG:
        test = patcher(test)
    return test


@requires_ffmpeg
@faces_on
class VideoFacesTests(FacesTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)

    def _video(self, *scenes, name="clip.mkv"):
        return upload(self.user, name, faces_video(*scenes))

    def _alice_cluster(self):
        """A cluster of two photos of Alice."""
        for name in ("alice-1.png", "alice-2.png"):
            analyze_faces(upload(self.user, name, faces_png((ALICE, (40, 50, 100)))))
        cluster_owner(self.user.pk)
        return FaceCluster.objects.get(owner=self.user)

    def test_one_person_over_several_scenes_is_one_face_grouped_with_their_photos(self):
        cluster = self._alice_cluster()
        video = self._video(
            (2, [(ALICE, (40, 50, 100))]),
            (2, [(ALICE, (250, 150, 80))]),
            (2, [(ALICE, (150, 20, 120))]),
        )

        (face,) = analyze_faces(video)

        face.refresh_from_db()
        self.assertEqual(face.file_id, video.pk)
        self.assertEqual(face.cluster_id, cluster.pk)
        self.assertTrue(default_storage.exists(face.crop))
        self.assertIsNotNone(face.timestamp)
        analysis = FaceAnalysis.objects.get(file=video)
        self.assertEqual(analysis.face_count, 1)
        self.assertEqual(analysis.content_hash, video.content_hash)
        self.assertNotIn(video, pending_faces_qs())

    def test_two_people_are_two_faces_in_two_clusters(self):
        video = self._video(
            (2, [(ALICE, (40, 50, 100)), (BOB, (240, 100, 80))]),
            (2, [(BOB, (40, 50, 100)), (ALICE, (240, 100, 80))]),
        )

        faces = analyze_faces(video)
        cluster_owner(self.user.pk)

        self.assertEqual(len(faces), 2)
        clusters = set(
            Face.objects.filter(file=video).values_list("cluster_id", flat=True)
        )
        self.assertEqual(len(clusters), 2)
        self.assertNotIn(None, clusters)

    def test_someone_seen_lost_and_seen_again_is_one_face(self):
        video = self._video(
            (2, [(ALICE, (40, 50, 100))]),
            (4, []),
            (2, [(ALICE, (40, 50, 100))]),
        )

        self.assertEqual(len(analyze_faces(video)), 1)

    def test_the_timestamp_is_when_the_face_is_seen(self):
        video = self._video(
            (4, []),
            (2, [(ALICE, (40, 50, 100))]),
        )

        (face,) = analyze_faces(video)

        self.assertGreaterEqual(face.timestamp, 3.75)
        self.assertLess(face.timestamp, 6)

    def test_a_video_without_anyone_is_recorded_as_analyzed(self):
        video = self._video((2, []))

        self.assertEqual(analyze_faces(video), [])

        self.assertEqual(FaceAnalysis.objects.get(file=video).face_count, 0)
        self.assertNotIn(video, pending_faces_qs())

    @override_settings(PHOTOS_FACES_VIDEO_MAX_DURATION=1)
    def test_a_video_past_the_length_limit_is_skipped_for_good(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))

        self.assertEqual(analyze_faces(video), [])

        self.assertTrue(FaceAnalysis.objects.filter(file=video).exists())
        self.assertNotIn(video, pending_faces_qs())

    @override_settings(PHOTOS_FACES_VIDEO_MAX_FILE_BYTES=10)
    def test_a_video_past_the_size_limit_is_skipped_for_good(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))

        self.assertEqual(analyze_faces(video), [])
        self.assertTrue(FaceAnalysis.objects.filter(file=video).exists())

    def test_a_video_ffmpeg_cannot_decode_is_recorded_as_analyzed(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))
        video.content.save("broken.mkv", ContentFile(b"not a video"), save=False)

        self.assertEqual(analyze_faces(video), [])
        self.assertTrue(FaceAnalysis.objects.filter(file=video).exists())

    def test_a_voice_recording_is_not_read(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))
        File.objects.filter(pk=video.pk).update(viewer=AudioViewer.slug)
        video.refresh_from_db()

        self.assertFalse(is_face_candidate(video))
        self.assertNotIn(video, pending_faces_qs())

    def test_reanalysis_keeps_what_the_user_decided(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))
        (face,) = analyze_faces(video)
        cluster = FaceCluster.objects.create(owner=self.user)
        Face.objects.filter(pk=face.pk).update(
            cluster=cluster, assignment=Face.Assignment.CONFIRMED
        )

        (new_face,) = analyze_faces(video)

        new_face.refresh_from_db()
        self.assertNotEqual(new_face.pk, face.pk)
        self.assertEqual(new_face.cluster_id, cluster.pk)
        self.assertEqual(new_face.assignment, Face.Assignment.CONFIRMED)

    def test_turning_grouping_off_removes_video_faces_and_their_crops(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))
        (face,) = analyze_faces(video)
        crop = Face.objects.get(pk=face.pk).crop

        with self.captureOnCommitCallbacks(execute=True):
            purge_owner_faces(self.user.pk)

        self.assertFalse(Face.objects.filter(file=video).exists())
        self.assertFalse(FaceAnalysis.objects.filter(file=video).exists())
        self.assertFalse(default_storage.exists(crop))

    def test_a_video_is_queued_on_its_own_task(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))
        photo = upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))

        with (
            patch("workspace.photos.tasks.analyze_video_faces.delay") as video_task,
            patch("workspace.photos.tasks.analyze_photo_faces.delay") as photo_task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            _queue_faces(video)
            _queue_faces(photo)

        video_task.assert_called_once_with(str(video.pk))
        photo_task.assert_called_once_with(str(photo.pk))

    def test_turning_grouping_on_queues_the_videos_on_their_task(self):
        video = self._video((2, [(ALICE, (40, 50, 100))]))

        with (
            patch("workspace.photos.tasks.analyze_video_faces.apply_async") as send,
            patch("workspace.photos.tasks.analyze_photo_faces.apply_async"),
        ):
            self.assertEqual(queue_owner_faces(self.user.pk), 1)

        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], (str(video.pk),))

    def test_the_progress_counts_videos(self):
        self._video((2, [(ALICE, (40, 50, 100))]))
        upload(self.user, "alice.png", faces_png((ALICE, (40, 50, 100))))

        self.assertEqual(face_progress(self.user), {"total": 2, "analyzed": 0})


@faces_on
class WithoutFfmpegTests(FacesTestMixin, TestCase):
    """A deployment without ffmpeg leaves videos out, instead of pending forever."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        opt_in(self.user)
        self.video = upload(self.user, "clip.webm", _webm())

    @_without_ffmpeg
    def test_videos_are_neither_candidates_nor_pending(self):
        self.assertFalse(is_face_candidate(self.video))
        self.assertIsNone(analyze_faces(self.video))
        self.assertNotIn(self.video, pending_faces_qs())
        self.assertEqual(face_progress(self.user)["total"], 0)

    @_without_ffmpeg
    def test_turning_grouping_on_queues_no_video(self):
        with patch("workspace.photos.tasks.analyze_video_faces.apply_async") as send:
            self.assertEqual(queue_owner_faces(self.user.pk), 0)
        send.assert_not_called()

    @patch("workspace.files.services.ffmpeg.FFMPEG", "/usr/bin/ffmpeg")
    @patch("workspace.files.services.ffmpeg.FFPROBE", "/usr/bin/ffprobe")
    def test_with_ffmpeg_the_video_is_pending(self):
        self.assertTrue(is_face_candidate(self.video))
        self.assertIn(self.video, pending_faces_qs())


def _webm():
    from workspace.files.tests.videos import clip_bytes

    return clip_bytes("clip.webm")
