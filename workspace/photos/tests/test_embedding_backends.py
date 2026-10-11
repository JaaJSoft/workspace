import contextlib
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase, override_settings
from PIL import Image

from workspace.photos.services.embedding import local, registry
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend
from workspace.photos.services.embedding.remote import (
    RemoteEmbeddingBackend,
    RemoteEmbeddingError,
)


def _cosine(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))


LOCAL_BACKENDS = (
    local.ClipVitB32Backend,
    local.Siglip2BaseBackend,
    local.JinaClipV1Backend,
)


class LocalBackendTests(SimpleTestCase):
    def setUp(self):
        local._models.clear()
        self.addCleanup(local._models.clear)

    def _fastembed(self, vector):
        built = MagicMock()
        built.embed.return_value = iter([np.asarray(vector, dtype=np.float32)])
        return built

    def test_embed_image_runs_the_image_encoder_on_the_picture(self):
        image = Image.new("RGB", (300, 200))
        built = self._fastembed(np.ones(768))
        backend = local.Siglip2BaseBackend()
        with patch.object(local, "_build", return_value=built) as build:
            vector = backend.embed_image(image)

        build.assert_called_once_with(backend.image_encoder)
        built.embed.assert_called_once_with([image])
        self.assertEqual(vector.shape, (768,))

    def test_embed_text_runs_the_text_encoder(self):
        built = self._fastembed(np.ones(512))
        backend = local.ClipVitB32Backend()
        with patch.object(local, "_build", return_value=built) as build:
            backend.embed_text("dog on the beach")

        build.assert_called_once_with(backend.text_encoder)
        built.embed.assert_called_once_with(["dog on the beach"])

    def test_each_encoder_is_built_once_per_process(self):
        backend = local.Siglip2BaseBackend()
        with patch.object(local, "_build", return_value=MagicMock()) as build:
            for _ in range(2):
                local.model(backend.image_encoder)
                local.model(backend.text_encoder)

        # One model name, two encoders: never mistaken for one another.
        self.assertEqual(build.call_count, 2)

    @override_settings(PHOTOS_MODEL_DIR="/srv/models", PHOTOS_ONNX_THREADS=2)
    def test_builds_on_the_cpu_into_the_model_directory(self):
        encoder = local.ClipVitB32Backend.text_encoder
        with patch("fastembed.TextEmbedding") as text_embedding:
            local._build(encoder)

        args, kwargs = text_embedding.call_args
        self.assertEqual(args, (encoder.name,))
        self.assertEqual(Path(kwargs["cache_dir"]), Path("/srv/models/fastembed"))
        self.assertEqual(kwargs["threads"], 2)
        self.assertEqual(kwargs["providers"], ["CPUExecutionProvider"])

    def test_every_encoder_is_one_fastembed_ships_where_health_looks(self):
        from fastembed import ImageEmbedding, TextEmbedding

        catalogs = {
            True: {m["model"]: m for m in ImageEmbedding.list_supported_models()},
            False: {m["model"]: m for m in TextEmbedding.list_supported_models()},
        }
        for backend in LOCAL_BACKENDS:
            for encoder in (backend.image_encoder, backend.text_encoder):
                with self.subTest(backend=backend.key, image=encoder.image):
                    described = catalogs[encoder.image][encoder.name]
                    self.assertEqual(described["sources"]["hf"], encoder.repo)
                    self.assertEqual(described["model_file"], encoder.file)
                    self.assertEqual(described["dim"], backend.dims)

    def test_health_says_the_weights_come_on_first_use_until_they_are_here(self):
        backend = local.Siglip2BaseBackend()
        with tempfile.TemporaryDirectory() as models, self._unloaded() as loaded:
            with override_settings(PHOTOS_MODEL_DIR=models):
                health = backend.health()

                self.assertTrue(health.ok)
                self.assertIn("download on first use", health.detail)

                for encoder in (backend.image_encoder, backend.text_encoder):
                    repo = Path(local.cache_dir()) / (
                        f"models--{encoder.repo.replace('/', '--')}"
                    )
                    onnx = repo / "snapshots" / "abc123" / encoder.file
                    onnx.parent.mkdir(parents=True, exist_ok=True)
                    onnx.write_bytes(b"onnx")
                health = backend.health()

            # The dashboard renders in the web process, which never runs a
            # model: answering must not import the model stack.
            self.assertEqual(loaded(), [])

        self.assertEqual(health.detail, "SigLIP 2 base, ready")

    @staticmethod
    @contextlib.contextmanager
    def _unloaded():
        """fastembed and the Hub client taken out of sys.modules, and a
        callable listing which of them got imported again."""
        heavy = ("fastembed", "huggingface_hub", "onnxruntime")

        def loaded():
            return [name for name in sys.modules if name.split(".")[0] in heavy]

        with patch.dict(sys.modules):
            for name in loaded():
                del sys.modules[name]
            yield loaded


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
        self.assertIsInstance(registry.get_embedding_backend(), local.ClipVitB32Backend)
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
        self.assertEqual(
            registry.backend_keys(),
            ["clip_vit_b32", "jina_clip_v1", "remote", "siglip2_base"],
        )

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
