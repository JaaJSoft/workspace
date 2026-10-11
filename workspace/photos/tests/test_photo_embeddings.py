from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.common.vectors import nearest
from workspace.common.vectors.encoding import from_bytes
from workspace.core.services.admin_dashboard import embedding_backend_health_card
from workspace.files.models import File, FileEvent, FileScan
from workspace.files.services import FileService
from workspace.files.services.processors import get_processor
from workspace.photos.indexes import PHOTO_EMBEDDINGS
from workspace.photos.models import PhotoEmbedding
from workspace.photos.services import photo_embeddings
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.handlers import follow_moved_photo
from workspace.photos.services.photo_embeddings import (
    QueryEmbeddingUnavailable,
    embed_photo,
    embed_query,
    forget_embedding,
    is_embedding_candidate,
    pending_embeddings_qs,
)

from .embeddings import BLUE, RED, embeddings_on
from .images import jpeg_bytes, make_video, upload

User = get_user_model()


def _vector_of(row):
    return from_bytes(row.embedding, PHOTO_EMBEDDINGS.dims)


@embeddings_on
class EmbedPhotoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def test_stores_the_vector_and_indexes_it_in_the_owners_partition(self):
        red = upload(self.user, "red.jpg", jpeg_bytes(color=RED))
        blue = upload(self.user, "blue.jpg", jpeg_bytes(color=BLUE))

        row = embed_photo(red)
        embed_photo(blue)

        row.refresh_from_db()
        self.assertEqual(row.owner, self.user)
        self.assertEqual(row.model, "fake")
        self.assertEqual(row.content_hash, red.content_hash)
        hits = nearest(PHOTO_EMBEDDINGS, _vector_of(row), partition=self.user.pk, k=2)
        self.assertEqual(hits[0][0], row.pk)
        self.assertEqual(len(hits), 2)
        self.assertNotIn(red, pending_embeddings_qs())

    def test_an_undecodable_photo_is_recorded_without_a_vector(self):
        broken = upload(self.user, "broken.jpg")
        broken.content.save("broken.jpg", ContentFile(b"not a picture"), save=False)

        row = embed_photo(broken)

        self.assertIsNone(row.embedding)
        self.assertNotIn(broken, pending_embeddings_qs())

    @override_settings(PHOTOS_EMBEDDING_MAX_FILE_BYTES=10)
    def test_a_photo_past_the_size_limit_is_recorded_without_a_vector(self):
        backend = FakeEmbeddingBackend()
        with (
            patch.object(
                photo_embeddings, "get_embedding_backend", return_value=backend
            ),
            patch.object(backend, "embed_image") as embed_image,
        ):
            row = embed_photo(upload(self.user, "big.jpg"))

        embed_image.assert_not_called()
        self.assertIsNone(row.embedding)

    def test_a_backend_failure_stores_nothing_and_leaves_the_photo_pending(self):
        photo = upload(self.user, "a.jpg")
        backend = FakeEmbeddingBackend()
        with (
            patch.object(
                photo_embeddings, "get_embedding_backend", return_value=backend
            ),
            patch.object(backend, "embed_image", side_effect=RuntimeError("down")),
            self.assertLogs("workspace.photos.services.photo_embeddings", "ERROR"),
        ):
            self.assertIsNone(embed_photo(photo))

        self.assertFalse(PhotoEmbedding.objects.exists())
        self.assertIn(photo, pending_embeddings_qs())

    def test_bytes_replaced_while_read_store_nothing(self):
        photo = upload(self.user, "a.jpg")
        File.objects.filter(pk=photo.pk).update(content_hash="f" * 64)

        self.assertIsNone(embed_photo(photo))
        self.assertFalse(PhotoEmbedding.objects.exists())

    def test_new_bytes_make_the_photo_pending_and_replace_the_vector(self):
        photo = upload(self.user, "a.jpg", jpeg_bytes(color=RED))
        before = _vector_of(embed_photo(photo))

        FileService.update_content(
            photo, ContentFile(jpeg_bytes(color=BLUE), name="a.jpg")
        )
        photo.refresh_from_db()
        self.assertIn(photo, pending_embeddings_qs())
        after = _vector_of(embed_photo(photo))

        self.assertLess(float(before @ after), 0)
        self.assertEqual(PhotoEmbedding.objects.count(), 1)

    def test_another_model_makes_every_photo_pending(self):
        photo = upload(self.user, "a.jpg")
        embed_photo(photo)
        PhotoEmbedding.objects.update(model="clip_vit_b32")

        self.assertIn(photo, pending_embeddings_qs())

    def test_a_photo_given_to_someone_else_moves_partition_without_a_rerun(self):
        bob = User.objects.create_user(username="bob", password="p")
        photo = upload(self.user, "a.jpg", jpeg_bytes(color=RED))
        row = embed_photo(photo)
        File.objects.filter(pk=photo.pk).update(owner=bob)
        photo.refresh_from_db()
        self.assertIn(photo, pending_embeddings_qs())

        with patch.object(FakeEmbeddingBackend, "embed_image") as embed_image:
            moved = embed_photo(photo)

        embed_image.assert_not_called()
        self.assertEqual(moved.pk, row.pk)
        self.assertEqual(moved.owner, bob)
        vector = _vector_of(PhotoEmbedding.objects.get(pk=row.pk))
        self.assertEqual(
            [pk for pk, _ in nearest(PHOTO_EMBEDDINGS, vector, partition=bob.pk, k=1)],
            [row.pk],
        )
        self.assertEqual(
            nearest(PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=1), []
        )

    def test_a_group_folders_photo_is_embedded_in_its_uploaders_partition(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        folder = FileService.create_folder(owner=self.user, name="Team", group=team)
        photo = upload(self.user, "a.jpg", parent=folder)

        self.assertTrue(is_embedding_candidate(photo))
        self.assertEqual(embed_photo(photo).owner, self.user)

    def test_a_deleted_row_takes_its_vector_along(self):
        photo = upload(self.user, "a.jpg")
        row = embed_photo(photo)
        vector = _vector_of(row)

        FileService.hard_delete(photo)

        self.assertFalse(PhotoEmbedding.objects.exists())
        self.assertEqual(
            nearest(PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=1), []
        )

    def test_forget_drops_the_row_and_its_vector(self):
        photo = upload(self.user, "a.jpg")
        vector = _vector_of(embed_photo(photo))

        forget_embedding(photo)

        self.assertFalse(PhotoEmbedding.objects.exists())
        self.assertEqual(
            nearest(PHOTO_EMBEDDINGS, vector, partition=self.user.pk, k=1), []
        )


@embeddings_on
class CandidateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_a_photo_is_a_candidate(self):
        photo = upload(self.user, "a.jpg")

        self.assertTrue(is_embedding_candidate(photo))
        self.assertIn(photo, pending_embeddings_qs())

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_nothing_is_a_candidate_on_an_instance_without_it(self):
        photo = upload(self.user, "a.jpg")

        self.assertFalse(is_embedding_candidate(photo))
        self.assertIsNone(embed_photo(photo))
        self.assertFalse(get_processor("embeddings").pending_files().exists())

    @override_settings(PHOTOS_EMBEDDING_BACKEND="nope")
    def test_a_misconfigured_backend_queues_nothing(self):
        upload(self.user, "a.jpg")

        self.assertFalse(get_processor("embeddings").pending_files().exists())

    def test_videos_and_other_files_are_left_out(self):
        video = make_video(self.user, "clip.webm", None)
        text = upload(self.user, "a.txt", b"hello")

        self.assertFalse(is_embedding_candidate(video))
        self.assertFalse(is_embedding_candidate(text))
        self.assertEqual(set(pending_embeddings_qs()), set())

    @override_settings(FILES_MALWARE_SCAN_ENABLED=True)
    def test_a_quarantined_photo_is_never_read(self):
        photo = upload(self.user, "a.jpg")
        FileScan.objects.create(
            file=photo,
            status=FileScan.Status.INFECTED,
            content_hash=photo.content_hash,
            scanned_at=timezone.now(),
        )

        self.assertFalse(is_embedding_candidate(photo))
        self.assertNotIn(photo, pending_embeddings_qs())

    def test_an_upload_queues_its_embedding_once_committed(self):
        photo = upload(self.user, "a.jpg")
        processor = get_processor("embeddings")

        with (
            patch("workspace.photos.tasks.embed_photo.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            processor.enqueue(photo)

        delay.assert_called_once_with(str(photo.pk))

    def test_the_task_embeds_a_pending_photo(self):
        from workspace.photos.tasks import embed_photo as task

        photo = upload(self.user, "a.jpg")

        task.run(str(photo.pk))

        self.assertTrue(PhotoEmbedding.objects.filter(file=photo).exists())

    def test_a_photo_moved_to_another_owner_is_queued_again(self):
        bob = User.objects.create_user(username="bob", password="p")
        photo = upload(self.user, "a.jpg")
        embed_photo(photo)
        File.objects.filter(pk=photo.pk).update(owner=bob)
        photo = File.objects.select_related("photo_embedding").get(pk=photo.pk)

        with (
            patch("workspace.photos.tasks.embed_photo.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            follow_moved_photo(FileEvent(file=photo, action=FileEvent.Action.MOVED))

        delay.assert_called_once_with(str(photo.pk))


@embeddings_on
class EmbedQueryTests(TestCase):
    def tearDown(self):
        cache.clear()

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_the_worker_embeds_the_text_and_the_answer_is_cached(self):
        with patch.object(
            FakeEmbeddingBackend,
            "embed_text",
            autospec=True,
            side_effect=lambda self, text: [1.0] + [0.0] * 767,
        ) as embed_text:
            first = embed_query("  red   car ")
            second = embed_query("red car")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 768)
        embed_text.assert_called_once()
        self.assertEqual(embed_text.call_args.args[1], "red car")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
    def test_a_failure_is_reported_as_unavailable(self):
        with (
            patch.object(
                FakeEmbeddingBackend, "embed_text", side_effect=RuntimeError("down")
            ),
            self.assertLogs("workspace.photos.services.photo_embeddings", "WARNING"),
            self.assertRaises(QueryEmbeddingUnavailable),
        ):
            embed_query("red")


class EmbeddingHealthCardTests(TestCase):
    def setUp(self):
        from django.test import RequestFactory

        self.request = RequestFactory().get("/admin/")

    def tearDown(self):
        cache.clear()

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=False)
    def test_no_card_when_the_instance_has_embeddings_off(self):
        self.assertIsNone(embedding_backend_health_card(self.request))

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=True, PHOTOS_EMBEDDING_BACKEND="clip")
    def test_a_misconfigured_backend_shows_as_an_error(self):
        card = embedding_backend_health_card(self.request)

        self.assertEqual(card["tone"], "danger")
        self.assertIn("unknown embedding backend 'clip'", card["value"])

    @override_settings(PHOTOS_EMBEDDINGS_ENABLED=True, PHOTOS_EMBEDDING_BACKEND="fake")
    def test_a_working_backend_shows_as_healthy(self):
        self.assertEqual(embedding_backend_health_card(self.request)["tone"], "success")
