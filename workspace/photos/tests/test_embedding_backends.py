import gzip
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase, override_settings
from PIL import Image

from workspace.photos.services.embedding import clip, registry
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.embedding.remote import (
    RemoteEmbeddingBackend,
    RemoteEmbeddingError,
)
from workspace.photos.services.embedding.tokenizer import (
    CONTEXT_LENGTH,
    ClipTokenizer,
)


def _cosine(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))


class ClipTokenizerTests(SimpleTestCase):
    """A merges file of a few lines: the vocabulary is the 512 byte symbols,
    then one token per merge, then the two markers."""

    MERGES = ["c a", "ca t</w>", "d o", "do g</w>"]

    def setUp(self):
        self.tokenizer = ClipTokenizer(self.MERGES)
        self.start = self.tokenizer.encoder["<|startoftext|>"]
        self.end = self.tokenizer.encoder["<|endoftext|>"]

    def test_words_merge_down_to_their_token(self):
        self.assertEqual(
            self.tokenizer.encode("cat dog"),
            [self.tokenizer.encoder["cat</w>"], self.tokenizer.encoder["dog</w>"]],
        )
        self.assertEqual(self.tokenizer.encoder["cat</w>"], 513)
        self.assertEqual((self.start, self.end), (516, 517))

    def test_text_is_lowercased_and_its_spaces_collapsed(self):
        self.assertEqual(
            self.tokenizer.encode("  CAT \n\t Dog "), self.tokenizer.encode("cat dog")
        )

    def test_an_unmerged_word_falls_back_to_its_byte_symbols(self):
        symbols = self.tokenizer.encode("ox")
        self.assertEqual(
            symbols, [self.tokenizer.encoder["o"], self.tokenizer.encoder["x</w>"]]
        )

    def test_non_ascii_text_is_tokenized_byte_by_byte(self):
        self.assertEqual(len(self.tokenizer.encode("é")), 2)

    def test_encoder_input_is_padded_with_markers_and_a_mask(self):
        ids, mask = self.tokenizer("cat")

        self.assertEqual(len(ids), CONTEXT_LENGTH)
        self.assertEqual(ids[:3], [self.start, 513, self.end])
        self.assertEqual(ids[3:], [0] * (CONTEXT_LENGTH - 3))
        self.assertEqual(mask, [1, 1, 1] + [0] * (CONTEXT_LENGTH - 3))

    def test_a_long_text_is_cut_and_still_ends_with_the_end_marker(self):
        ids, mask = self.tokenizer("cat " * 200)

        self.assertEqual(len(ids), CONTEXT_LENGTH)
        self.assertEqual(ids[-1], self.end)
        self.assertEqual(sum(mask), CONTEXT_LENGTH)

    def test_reads_a_gzipped_merges_file_after_its_version_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "merges.txt.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("#version: 0.2\n" + "\n".join(self.MERGES) + "\n")
            tokenizer = ClipTokenizer.from_file(path)

        self.assertEqual(tokenizer.encode("cat"), [513])


class ClipBackendTests(SimpleTestCase):
    def test_pixel_values_scale_the_short_side_and_cut_the_centre(self):
        image = Image.new("RGB", (448, 300), (255, 255, 255))
        # A black band at the left edge, cut out by the centre crop.
        image.paste((0, 0, 0), (0, 0, 40, 300))

        pixels = clip.pixel_values(image)

        self.assertEqual(pixels.shape, (1, 3, 224, 224))
        self.assertEqual(pixels.dtype, np.float32)
        white = (1.0 - clip._MEAN) / clip._STD
        np.testing.assert_allclose(pixels[0, :, 112, 0], white, rtol=1e-5)

    def test_embed_image_runs_the_visual_model(self):
        session = MagicMock()
        session.run.return_value = [np.ones((1, 512), dtype=np.float32)]
        with patch.object(clip.runtime, "session", return_value=session) as get:
            vector = clip.ClipVitB32Backend().embed_image(Image.new("RGB", (300, 200)))

        get.assert_called_once_with(clip.VISUAL)
        (inputs,) = session.run.call_args.args[1:]
        self.assertEqual(inputs["pixel_values"].shape, (1, 3, 224, 224))
        self.assertEqual(vector.shape, (512,))

    def test_embed_text_runs_the_text_model_on_token_ids(self):
        session = MagicMock()
        session.run.return_value = [np.ones((1, 512), dtype=np.float32)]
        tokenizer = ClipTokenizer(ClipTokenizerTests.MERGES)
        with (
            patch.object(clip, "_get_tokenizer", return_value=tokenizer),
            patch.object(clip.runtime, "session", return_value=session) as get,
        ):
            vector = clip.ClipVitB32Backend().embed_text("cat")

        get.assert_called_once_with(clip.TEXTUAL)
        (inputs,) = session.run.call_args.args[1:]
        self.assertEqual(inputs["input_ids"].shape, (1, CONTEXT_LENGTH))
        self.assertEqual(inputs["input_ids"].dtype, np.int32)
        self.assertEqual(inputs["input_ids"][0, 1], 513)
        self.assertEqual(inputs["attention_mask"][0].sum(), 3)
        self.assertEqual(vector.shape, (512,))

    def test_pins_every_file_it_downloads(self):
        backend = clip.ClipVitB32Backend()

        self.assertEqual(backend.dims, 512)
        for model in backend.models:
            self.assertRegex(model.sha256, r"^[0-9a-f]{64}$")
            self.assertTrue(model.url.startswith("https://"))


@override_settings(
    PHOTOS_EMBEDDING_BASE_URL="http://embeddings.local/v1",
    PHOTOS_EMBEDDING_MODEL="siglip-so400m",
    PHOTOS_EMBEDDING_API_KEY="secret",
    PHOTOS_EMBEDDING_DIMS=4,
    PHOTOS_EMBEDDING_REMOTE_IMAGE_SIZE=256,
)
class RemoteBackendTests(SimpleTestCase):
    def _client(self, vector=(0.1, 0.2, 0.3, 0.4)):
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=list(vector))]
        )
        return client

    def test_sends_a_photo_as_a_jpeg_data_uri(self):
        client = self._client()
        backend = RemoteEmbeddingBackend()
        with patch.object(backend, "_client", return_value=client):
            vector = backend.embed_image(Image.new("RGB", (64, 48), (255, 0, 0)))

        kwargs = client.embeddings.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "siglip-so400m")
        self.assertEqual(kwargs["encoding_format"], "float")
        (sent,) = kwargs["input"]
        self.assertTrue(sent.startswith("data:image/jpeg;base64,"))
        self.assertEqual(vector, [0.1, 0.2, 0.3, 0.4])

    def test_sends_a_query_as_plain_text(self):
        client = self._client()
        backend = RemoteEmbeddingBackend()
        with patch.object(backend, "_client", return_value=client):
            backend.embed_text("dog on the beach")

        self.assertEqual(
            client.embeddings.create.call_args.kwargs["input"], ["dog on the beach"]
        )

    def test_refuses_a_vector_of_another_size(self):
        backend = RemoteEmbeddingBackend()
        with (
            patch.object(backend, "_client", return_value=self._client((1.0, 2.0))),
            self.assertRaisesMessage(RemoteEmbeddingError, "2 components"),
        ):
            backend.embed_text("snow")

    def test_the_model_is_part_of_the_key_and_the_settings_set_the_shape(self):
        backend = RemoteEmbeddingBackend()

        self.assertEqual(backend.model_key, "remote:siglip-so400m")
        self.assertEqual(backend.dims, 4)
        self.assertEqual(backend.decode_size, 256)

    def test_builds_its_client_from_the_settings(self):
        with patch("openai.OpenAI") as client_class:
            RemoteEmbeddingBackend()._client()

        kwargs = client_class.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "http://embeddings.local/v1")
        self.assertEqual(kwargs["api_key"], "secret")

    @override_settings(PHOTOS_EMBEDDING_BASE_URL="")
    def test_an_unconfigured_endpoint_fails_and_says_so(self):
        backend = RemoteEmbeddingBackend()

        self.assertFalse(backend.health().ok)
        self.assertIn("PHOTOS_EMBEDDING_BASE_URL", backend.health().detail)
        with self.assertRaises(RemoteEmbeddingError):
            backend.embed_text("snow")


class FakeBackendTests(SimpleTestCase):
    def setUp(self):
        self.backend = FakeEmbeddingBackend()

    def _photo(self, colour):
        return self.backend.embed_image(Image.new("RGB", (32, 32), colour))

    def test_the_colour_words_find_the_pictures_of_that_colour(self):
        red = self._photo((255, 0, 0))
        blue = self._photo((0, 0, 255))
        query = self.backend.embed_text("a red car")

        self.assertGreater(_cosine(query, red), 0.99)
        self.assertLess(_cosine(query, blue), 0)

    def test_close_colours_are_similar_but_not_duplicates(self):
        distance = 1 - _cosine(self._photo((255, 0, 0)), self._photo((128, 0, 0)))

        self.assertLess(distance, self.backend.similar_max_distance)
        self.assertGreater(distance, self.backend.duplicate_max_distance)


class RegistryTests(SimpleTestCase):
    @override_settings(PHOTOS_EMBEDDING_BACKEND="clip_vit_b32")
    def test_builds_the_configured_backend(self):
        self.assertIsInstance(registry.get_embedding_backend(), clip.ClipVitB32Backend)
        self.assertEqual(registry.configured_dims(), 512)

    @override_settings(PHOTOS_EMBEDDING_BACKEND="clip-vit")
    def test_a_misspelt_backend_fails_loudly_instead_of_switching_off(self):
        backend = registry.get_embedding_backend()

        self.assertTrue(registry.is_misconfigured())
        self.assertFalse(backend.health().ok)
        self.assertIn("clip-vit", backend.health().detail)
        with self.assertRaises(registry.BackendMisconfigured):
            backend.embed_text("snow")

    def test_the_fake_is_not_offered_as_a_setting(self):
        self.assertEqual(registry.backend_keys(), ["clip_vit_b32", "remote"])

    @override_settings(
        PHOTOS_EMBEDDING_BACKEND="fake",
        PHOTOS_SIMILAR_MAX_DISTANCE=0.4,
        PHOTOS_DUPLICATE_MAX_DISTANCE=None,
    )
    def test_a_threshold_setting_overrides_the_backend_default(self):
        self.assertEqual(registry.similar_max_distance(), 0.4)
        self.assertEqual(
            registry.duplicate_max_distance(),
            FakeEmbeddingBackend.duplicate_max_distance,
        )
