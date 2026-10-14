"""Videos in the embedding space: one vector per key frame, with its time."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from PIL import Image

from workspace.common.vectors import nearest
from workspace.common.vectors.encoding import from_bytes
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.processors import get_processor
from workspace.files.tests.videos import requires_ffmpeg
from workspace.photos.indexes import PHOTO_EMBEDDINGS
from workspace.photos.models import PhotoEmbedding
from workspace.photos.queries import MINE
from workspace.photos.services import similarity
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend, colour_vector
from workspace.photos.services.photo_embeddings import (
    embed_file,
    forget_embedding,
    pending_embeddings_qs,
)
from workspace.photos.services.similarity import search_by_description
from workspace.photos.services.video_key_frames import KeyFrame

from .embeddings import (
    BLUE,
    GREEN,
    RED,
    embeddings_on,
    ffmpeg_installed,
    library_video,
)
from .images import colour_video, library_clip, make_video, video_bytes

User = get_user_model()


def fade_video(start, end, *, seconds, fps=4):
    """One shot slowly turning from colour *start* to *end*: no frame differs
    from the one before enough to be a scene change."""
    steps = round(seconds * fps)
    return video_bytes(
        [
            (
                1 / fps,
                Image.new(
                    "RGB",
                    (64, 48),
                    tuple(
                        round(a + (b - a) * i / (steps - 1))
                        for a, b in zip(start, end, strict=True)
                    ),
                ),
            )
            for i in range(steps)
        ],
        fps=fps,
    )


def _pictures_of(video):
    """The key frame pictures stored for *video*."""
    try:
        return default_storage.listdir(f"video-frames/{video.pk}")[1]
    except FileNotFoundError:
        return []


def _unit(vector):
    return vector / (vector @ vector) ** 0.5


def _colour_of(row):
    """The colour among RED, GREEN, BLUE whose fake vector *row* holds."""
    vector = from_bytes(row.embedding, PHOTO_EMBEDDINGS.dims)
    backend = FakeEmbeddingBackend()
    return max(
        (RED, GREEN, BLUE),
        key=lambda colour: float(
            _unit(backend.embed_image(Image.new("RGB", (1, 1), colour))) @ vector
        ),
    )


@requires_ffmpeg
@embeddings_on
class VideoKeyFrameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def _video(self, data, name="clip.mkv"):
        return library_clip(self.user, name, data)

    def test_each_shot_is_one_key_frame_from_the_moment_it_starts(self):
        video = self._video(colour_video((3, RED), (3, BLUE)))

        rows = embed_file(video)

        self.assertEqual([_colour_of(row) for row in rows], [RED, BLUE])
        self.assertEqual(rows[0].timestamp, 0.0)
        self.assertGreaterEqual(rows[1].timestamp, 2.75)
        self.assertLess(rows[1].timestamp, 3.5)
        self.assertTrue(
            all(row.owner_id == self.user.pk and row.model == "fake" for row in rows)
        )
        self.assertNotIn(video, pending_embeddings_qs())
        vector = from_bytes(rows[1].embedding, PHOTO_EMBEDDINGS.dims)
        hits = nearest(PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=5)
        self.assertEqual(hits[0][0], rows[1].pk)

    def test_each_key_frame_keeps_its_picture(self):
        video = self._video(colour_video((3, RED), (3, BLUE)))

        rows = embed_file(video)

        for row, colour in zip(rows, (RED, BLUE), strict=True):
            with default_storage.open(row.frame) as handle, Image.open(handle) as img:
                self.assertEqual(img.format, "WEBP")
                self.assertEqual(img.size, (64, 48))
                pixel = img.convert("RGB").getpixel((32, 24))
            self.assertTrue(
                all(abs(a - b) < 40 for a, b in zip(pixel, colour, strict=True))
            )

    def test_a_still_shot_is_one_vector(self):
        video = self._video(colour_video((10, RED)))

        (row,) = embed_file(video)

        self.assertEqual(row.timestamp, 0.0)

    def test_a_view_seen_again_is_not_embedded_twice(self):
        video = self._video(colour_video((3, RED), (3, BLUE), (3, RED)))

        rows = embed_file(video)

        self.assertEqual([_colour_of(row) for row in rows], [RED, BLUE])

    def test_a_shot_without_a_cut_is_read_along_its_length(self):
        video = self._video(fade_video(RED, BLUE, seconds=12))

        rows = embed_file(video)

        self.assertGreater(len(rows), 2)
        self.assertEqual(_colour_of(rows[0]), RED)
        self.assertEqual(_colour_of(rows[-1]), BLUE)
        with patch.object(
            similarity,
            "embed_query",
            side_effect=lambda text: list(FakeEmbeddingBackend().embed_text(text)),
        ):
            (match,) = search_by_description(self.user, "blue", MINE)
        self.assertEqual(match.file_id, video.pk)
        self.assertEqual(match.timestamp, rows[-1].timestamp)
        self.assertGreater(match.timestamp, 8)

    @override_settings(PHOTOS_VIDEO_KEY_FRAMES_MAX=2)
    def test_the_frame_budget_caps_the_key_frames(self):
        video = self._video(colour_video((2, RED), (2, BLUE), (2, GREEN)))

        self.assertEqual(len(embed_file(video)), 2)

    @override_settings(PHOTOS_VIDEO_KEY_FRAMES_MAX_DURATION=2)
    def test_a_video_too_long_is_recorded_without_a_vector(self):
        video = self._video(colour_video((3, RED)))

        (row,) = embed_file(video)

        self.assertIsNone(row.embedding)
        self.assertIsNone(row.timestamp)
        self.assertNotIn(video, pending_embeddings_qs())

    def test_a_video_ffmpeg_cannot_read_is_recorded_without_a_vector(self):
        video = self._video(colour_video((1, RED)))
        video.content.save("clip.mkv", ContentFile(b"not a video"), save=False)

        with self.assertLogs("workspace.photos.services.video_frames", "INFO"):
            (row,) = embed_file(video)

        self.assertIsNone(row.embedding)

    def test_new_bytes_replace_every_key_frame(self):
        video = self._video(colour_video((3, RED), (3, BLUE)))
        old = embed_file(video)

        FileService.update_content(
            video, ContentFile(colour_video((3, GREEN)), name="clip.mkv")
        )
        video.refresh_from_db()
        self.assertIn(video, pending_embeddings_qs())
        with self.captureOnCommitCallbacks(execute=True):
            (row,) = embed_file(video)

        self.assertEqual(_colour_of(row), GREEN)
        self.assertTrue(default_storage.exists(row.frame))
        self.assertFalse(any(default_storage.exists(old_row.frame) for old_row in old))
        self.assertEqual(list(PhotoEmbedding.objects.filter(file=video)), [row])
        vector = from_bytes(old[1].embedding, PHOTO_EMBEDDINGS.dims)
        self.assertEqual(
            [
                pk
                for pk, _ in nearest(
                    PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=5
                )
            ],
            [row.pk],
        )

    def test_the_task_embeds_a_pending_video(self):
        from workspace.photos.tasks import embed_video as task

        video = self._video(colour_video((2, RED)))

        task.run(str(video.pk))

        self.assertEqual(PhotoEmbedding.objects.filter(file=video).count(), 1)


@embeddings_on
@ffmpeg_installed()
class VideoEmbeddingTests(TestCase):
    """What needs no decoding: the limits checked before ffmpeg runs, the
    rows of a video, and the task an upload queues."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.video = make_video(self.user, "clip.webm", None)

    def tearDown(self):
        cache.clear()

    @override_settings(PHOTOS_VIDEO_KEY_FRAMES_MAX_FILE_BYTES=10)
    def test_a_video_too_large_is_never_decoded(self):
        with patch("workspace.files.services.ffmpeg.sample_frames") as sample:
            (row,) = embed_file(self.video)

        sample.assert_not_called()
        self.assertIsNone(row.embedding)
        self.assertNotIn(self.video, pending_embeddings_qs())

    def test_a_backend_failure_stores_nothing_and_leaves_the_video_pending(self):
        with (
            patch(
                "workspace.photos.services.photo_embeddings.embed_key_frames",
                side_effect=RuntimeError("down"),
            ),
            self.assertLogs("workspace.photos.services.photo_embeddings", "ERROR"),
        ):
            self.assertIsNone(embed_file(self.video))

        self.assertFalse(PhotoEmbedding.objects.exists())
        self.assertIn(self.video, pending_embeddings_qs())

    def test_a_blob_that_cannot_be_opened_stores_nothing(self):
        with patch(
            "workspace.photos.services.photo_embeddings.embed_key_frames",
            return_value=None,
        ):
            self.assertIsNone(embed_file(self.video))

        self.assertIn(self.video, pending_embeddings_qs())

    def test_a_video_given_to_someone_else_keeps_its_key_frames(self):
        video = library_video(self.user, "trip.webm", [(0.0, RED), (4.5, BLUE)])
        bob = User.objects.create_user(username="bob", password="p")
        File.objects.filter(pk=video.pk).update(owner=bob)
        video.refresh_from_db()
        self.assertIn(video, pending_embeddings_qs())

        before = {
            row.pk: row.frame for row in PhotoEmbedding.objects.filter(file=video)
        }

        with (
            patch(
                "workspace.photos.services.photo_embeddings.embed_key_frames"
            ) as embed,
            self.captureOnCommitCallbacks(execute=True),
        ):
            rows = embed_file(video)

        embed.assert_not_called()
        self.assertEqual({row.pk: row.frame for row in rows}, before)
        self.assertTrue(all(default_storage.exists(frame) for frame in before.values()))
        self.assertEqual(sorted(row.timestamp for row in rows), [0.0, 4.5])
        self.assertTrue(all(row.owner_id == bob.pk for row in rows))
        self.assertNotIn(video, pending_embeddings_qs())
        vector = from_bytes(rows[0].embedding, PHOTO_EMBEDDINGS.dims)
        self.assertEqual(
            nearest(PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=5), []
        )

    def test_forget_drops_every_key_frame_its_vector_and_its_picture(self):
        video = library_video(self.user, "trip.webm", [(0.0, RED), (4.5, BLUE)])
        row = PhotoEmbedding.objects.filter(file=video).first()
        vector = from_bytes(row.embedding, PHOTO_EMBEDDINGS.dims)
        self.assertTrue(default_storage.exists(row.frame))

        with self.captureOnCommitCallbacks(execute=True):
            forget_embedding(video)

        self.assertFalse(PhotoEmbedding.objects.exists())
        self.assertEqual(
            nearest(PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=5), []
        )
        self.assertFalse(default_storage.exists(row.frame))

    def test_a_deleted_video_takes_its_pictures_along(self):
        video = library_video(self.user, "trip.webm", [(0.0, RED), (4.5, BLUE)])
        frames = list(
            PhotoEmbedding.objects.filter(file=video).values_list("frame", flat=True)
        )

        with self.captureOnCommitCallbacks(execute=True):
            FileService.hard_delete(video)

        self.assertFalse(any(default_storage.exists(frame) for frame in frames))

    def test_a_video_changed_while_read_leaves_no_picture_behind(self):
        File.objects.filter(pk=self.video.pk).update(content_hash="f" * 64)

        with patch(
            "workspace.photos.services.photo_embeddings.embed_key_frames",
            return_value=[
                KeyFrame(
                    timestamp=0.0,
                    vector=colour_vector(RED, FakeEmbeddingBackend.dims),
                    image=Image.new("RGB", (16, 9), RED),
                )
            ],
        ):
            self.assertIsNone(embed_file(self.video))

        self.assertEqual(_pictures_of(self.video), [])

    def test_an_upload_queues_the_video_task_once_committed(self):
        with (
            patch("workspace.photos.tasks.embed_video.delay") as delay,
            patch("workspace.photos.tasks.embed_photo.delay") as photo_delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            get_processor("embeddings").enqueue(self.video)

        delay.assert_called_once_with(str(self.video.pk))
        photo_delay.assert_not_called()
