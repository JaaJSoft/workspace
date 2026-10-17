"""Automatic tags (services/auto_tags.py), on the fake backend: a photo's or
a key frame's vector is its colour, and "red" is the text a red photo lies
on."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File, FileTag, Tag
from workspace.files.services import FileService
from workspace.files.services.processors import get_processor
from workspace.photos.models import PhotoEmbedding
from workspace.photos.services import auto_tags, photo_embeddings
from workspace.photos.services.auto_tags import pending_auto_tags_qs, refresh_auto_tags
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.embedding.local import (
    ClipVitB32Backend,
    JinaClipV1Backend,
    Siglip2BaseBackend,
)
from workspace.photos.services.photo_embeddings import embed_file
from workspace.photos.services.tag_vocabulary import CLASSIC_TAGS, ClassicTag
from workspace.users.services.settings import set_setting

from .embeddings import (
    BLUE,
    DARK_RED,
    RED,
    embeddings_on,
    library_photo,
    library_video,
)
from .images import jpeg_bytes, upload

User = get_user_model()

# A vocabulary the fake backend can see: colours. Its table recognizes red
# alone (FakeEmbeddingBackend.tag_thresholds).
_COLOUR_VOCABULARY = (
    ClassicTag("Red", "Red", "flame"),
    ClassicTag("Blue", "Blue", "droplet"),
)
# Between the fake backend's video (0.05) and photo (0.1) thresholds of red.
_PALE_RED = (170, 0, 0)


def _tags(photo):
    return {
        (ft.tag.name, ft.source)
        for ft in FileTag.objects.filter(file=photo).select_related("tag")
    }


class AutoTagTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def tag_by_hand(self, tag, photo):
        FileTag.objects.create(file=photo, tag=tag)


@embeddings_on
@patch.object(auto_tags, "CLASSIC_TAGS", _COLOUR_VOCABULARY)
class ClassicTagTests(AutoTagTestCase):
    def test_a_photo_gets_the_classic_tags_it_shows_once_embedded(self):
        red = library_photo(self.user, "red.jpg", RED)
        blue = library_photo(self.user, "blue.jpg", BLUE)

        self.assertEqual(_tags(red), {("Red", FileTag.Source.AUTO)})
        self.assertEqual(_tags(blue), set())
        self.assertEqual(Tag.objects.filter(owner=self.user).count(), 1)
        self.assertFalse(pending_auto_tags_qs().exists())

    def test_each_tag_has_its_own_threshold(self):
        # Close to red, past the red tag's threshold.
        dark = library_photo(self.user, "dark.jpg", DARK_RED)

        self.assertEqual(_tags(dark), set())

    @patch.object(FakeEmbeddingBackend, "tag_thresholds", {"red": 0.1, "blue": 0.1})
    def test_a_tag_the_backend_does_not_recognize_is_never_placed(self):
        # Blue is in the vocabulary, not in the default table.
        blue = library_photo(self.user, "blue.jpg", BLUE)

        self.assertEqual(_tags(blue), {("Blue", FileTag.Source.AUTO)})
        cache.clear()
        with patch.object(FakeEmbeddingBackend, "tag_thresholds", {"red": 0.1}):
            other = library_photo(self.user, "other.jpg", BLUE)
        self.assertEqual(_tags(other), set())

    def test_a_tag_is_created_with_the_classic_tags_icon(self):
        library_photo(self.user, "red.jpg", RED)

        self.assertEqual(Tag.objects.get(owner=self.user, name="Red").icon, "flame")

    def test_the_owners_tag_of_that_name_is_reused(self):
        mine = Tag.objects.create(owner=self.user, name="red", icon="heart")

        red = library_photo(self.user, "red.jpg", RED)

        self.assertEqual(_tags(red), {("red", FileTag.Source.AUTO)})
        self.assertEqual(list(Tag.objects.filter(owner=self.user)), [mine])
        mine.refresh_from_db()
        self.assertEqual(mine.icon, "heart")

    def test_a_tag_put_by_hand_stays_manual(self):
        red = library_photo(self.user, "red.jpg", RED, embed=False)
        self.tag_by_hand(Tag.objects.create(owner=self.user, name="Red"), red)

        embed_file(red)

        self.assertEqual(_tags(red), {("Red", FileTag.Source.MANUAL)})

    def test_a_tag_taken_off_never_comes_back(self):
        red = library_photo(self.user, "red.jpg", RED)
        FileTag.objects.filter(file=red).delete()

        for file_obj in get_processor("auto_tags").pending_files():
            refresh_auto_tags(file_obj)
        embed_file(red)

        self.assertEqual(_tags(red), set())

    def test_a_new_vector_tags_the_photo_again(self):
        photo = library_photo(self.user, "photo.jpg", BLUE)
        self.assertEqual(_tags(photo), set())

        FileService.update_content(photo, ContentFile(jpeg_bytes(color=RED)))
        embed_file(photo)

        self.assertEqual(_tags(photo), {("Red", FileTag.Source.AUTO)})

    def test_a_group_photo_is_never_tagged(self):
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        folder = FileService.create_folder(owner=self.user, name="Team", group=team)

        red = library_photo(self.user, "red.jpg", RED, parent=folder)

        self.assertEqual(_tags(red), set())
        self.assertNotIn(red, pending_auto_tags_qs())

    def test_an_owner_who_turned_them_off_gets_none(self):
        set_setting(self.user, "photos", "auto_tags", False)

        red = library_photo(self.user, "red.jpg", RED)

        self.assertEqual(_tags(red), set())
        # Settled all the same: turning them back on tags the next photos.
        self.assertNotIn(red, pending_auto_tags_qs())

    def test_the_catch_up_tags_a_photo_embedded_before_the_feature(self):
        red = library_photo(self.user, "red.jpg", RED)
        FileTag.objects.filter(file=red).delete()
        PhotoEmbedding.objects.filter(file=red).update(auto_tagged_at=None)

        pending = list(get_processor("auto_tags").pending_files())
        for file_obj in pending:
            refresh_auto_tags(file_obj)

        self.assertEqual(pending, [red])
        self.assertEqual(_tags(red), {("Red", FileTag.Source.AUTO)})

    def test_a_photo_given_to_someone_else_is_tagged_with_their_tags(self):
        red = library_photo(self.user, "red.jpg", RED)
        bob = User.objects.create_user(username="bob", password="p")
        File.objects.filter(pk=red.pk).update(owner=bob)
        red.refresh_from_db()

        embed_file(red)

        self.assertEqual(
            set(
                FileTag.objects.filter(file=red).values_list(
                    "tag__owner__username", flat=True
                )
            ),
            {"alice", "bob"},
        )

    def test_a_video_is_tagged_from_any_of_its_key_frames(self):
        video = library_video(self.user, "clip.webm", [(0.0, BLUE), (4.0, RED)])

        self.assertEqual(_tags(video), {("Red", FileTag.Source.AUTO)})
        self.assertFalse(
            PhotoEmbedding.objects.filter(
                file=video, auto_tagged_at__isnull=True
            ).exists()
        )
        self.assertNotIn(video, pending_auto_tags_qs())

    def test_a_video_has_its_own_thresholds(self):
        # Past the video table's red, within the photo table's.
        photo = library_photo(self.user, "red.jpg", _PALE_RED)
        video = library_video(self.user, "clip.webm", [(0.0, _PALE_RED)])

        self.assertEqual(_tags(photo), {("Red", FileTag.Source.AUTO)})
        self.assertEqual(_tags(video), set())

    @patch.object(FakeEmbeddingBackend, "video_tag_thresholds", {})
    def test_a_backend_without_a_video_table_tags_no_video(self):
        video = library_video(self.user, "clip.webm", [(0.0, RED)])

        self.assertEqual(_tags(video), set())
        self.assertNotIn(video, pending_auto_tags_qs())

    def test_the_catch_up_tags_a_video_once(self):
        with patch.object(photo_embeddings, "tag_file_safely"):
            video = library_video(self.user, "clip.webm", [(0.0, RED), (4.0, BLUE)])

        pending = list(get_processor("auto_tags").pending_files())
        for file_obj in pending:
            refresh_auto_tags(file_obj)

        self.assertEqual(pending, [video])
        self.assertEqual(_tags(video), {("Red", FileTag.Source.AUTO)})
        self.assertFalse(pending_auto_tags_qs().exists())

    def test_a_tag_taken_off_a_video_never_comes_back(self):
        video = library_video(self.user, "clip.webm", [(0.0, RED)])
        FileTag.objects.filter(file=video).delete()

        for file_obj in get_processor("auto_tags").pending_files():
            refresh_auto_tags(file_obj)

        self.assertEqual(_tags(video), set())

    def test_a_video_with_no_key_frame_is_settled_untagged(self):
        video = library_video(self.user, "clip.webm", [])

        self.assertEqual(_tags(video), set())
        row = PhotoEmbedding.objects.get(file=video)
        self.assertTrue(auto_tags.tag_file(row))
        self.assertNotIn(video, pending_auto_tags_qs())

    def test_a_tagging_failure_leaves_the_embedding_stored(self):
        red = upload(self.user, "red.jpg", jpeg_bytes(color=RED))
        with patch.object(auto_tags, "tags_for", side_effect=RuntimeError("boom")):
            (row,) = embed_file(red)

        self.assertIsNotNone(row.embedding)
        self.assertIn(red, pending_auto_tags_qs())


class VocabularyTests(TestCase):
    def test_every_classic_tag_has_a_distinct_name(self):
        names = [tag.name.lower() for tag in CLASSIC_TAGS]
        self.assertEqual(len(names), len(set(names)))
        for tag in CLASSIC_TAGS:
            self.assertLessEqual(len(tag.name), 100)

    def test_every_backend_table_names_classic_tags(self):
        texts = {tag.text for tag in CLASSIC_TAGS}
        for backend in (ClipVitB32Backend, Siglip2BaseBackend, JinaClipV1Backend):
            with self.subTest(backend=backend.key):
                self.assertLessEqual(set(backend.tag_thresholds), texts)
                # A tag the photos' table leaves out is never placed on a video.
                self.assertLessEqual(
                    set(backend.video_tag_thresholds), set(backend.tag_thresholds)
                )

    def test_every_classic_tag_has_an_icon_the_vendored_lucide_build_draws(self):
        # The bundle names each icon in PascalCase: tree-palm is TreePalm.
        bundle = (
            settings.BASE_DIR / "workspace/common/static/ui/js/vendor/lucide/lucide.js"
        ).read_text(encoding="utf-8")
        for tag in CLASSIC_TAGS:
            with self.subTest(tag=tag.name):
                self.assertNotEqual(tag.icon, "tag")
                pascal = "".join(part.capitalize() for part in tag.icon.split("-"))
                self.assertRegex(bundle, rf"\b{pascal}\b")
