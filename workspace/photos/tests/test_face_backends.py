"""The real backends' post-processing, fed crafted model outputs.

No weights here: a fake onnxruntime session returns outputs chosen so the
decoded box and landmarks can be worked out by hand. What this pins down is
the numpy rewrite of OpenCV's and insightface's decoding; the models
themselves run in test_face_weights.py, behind PHOTOS_TEST_WEIGHTS=1.
"""

import math
import sys
import tempfile
import types
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx2
import numpy as np
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from workspace.photos.services.detection import runtime, weights
from workspace.photos.services.detection.geometry import (
    _REFERENCE,
    ALIGNED_SIZE,
    align,
    letterbox,
    nms,
    similarity_transform,
)
from workspace.photos.services.detection.health import weights_health
from workspace.photos.services.detection.registry import backend_keys, get_face_backend
from workspace.photos.services.detection.scrfd_arcface import ScrfdArcFaceBackend
from workspace.photos.services.detection.weights import ModelFile
from workspace.photos.services.detection.yunet_sface import YuNetSFaceBackend


class FakeSession:
    """Answers run() with fixed outputs, by name or in order."""

    def __init__(self, outputs, input_name="input.1"):
        self.outputs = outputs
        self.feeds = []
        self._input = types.SimpleNamespace(name=input_name)

    def get_inputs(self):
        return [self._input]

    def run(self, names, feed):
        self.feeds.append(feed)
        if names is None:
            return list(self.outputs.values())
        return [self.outputs[name] for name in names]


def _session(fake):
    return patch.object(runtime, "session", return_value=fake)


class YuNetDecodingTests(SimpleTestCase):
    def _outputs(self):
        outputs = {}
        for stride in (8, 16, 32):
            cells = (640 // stride) ** 2
            outputs[f"cls_{stride}"] = np.zeros((1, cells, 1), np.float32)
            outputs[f"obj_{stride}"] = np.zeros((1, cells, 1), np.float32)
            outputs[f"bbox_{stride}"] = np.zeros((1, cells, 4), np.float32)
            outputs[f"kps_{stride}"] = np.zeros((1, cells, 10), np.float32)
        # One face, on the stride-32 grid at column 5, row 4.
        index = 4 * 20 + 5
        outputs["cls_32"][0, index] = outputs["obj_32"][0, index] = 1.0
        outputs["bbox_32"][0, index] = [0.5, 0.5, math.log(2), math.log(3)]
        outputs["kps_32"][0, index] = [0, 0, 1, 0, 0.5, 0.5, 0, 1, 1, 1]
        return outputs

    def test_decodes_the_box_and_landmarks_back_to_the_original_image(self):
        fake = FakeSession(self._outputs())
        # 1280 x 640: letterboxed into 640 x 640 at half scale.
        image = np.zeros((640, 1280, 3), np.uint8)

        with _session(fake):
            (face,) = YuNetSFaceBackend().detect(image)

        # Centre (5.5, 4.5) cells of 32 px, 2 x 3 cells, then x2 back.
        self.assertEqual(face.box, (288.0, 192.0, 128.0, 192.0))
        self.assertEqual(face.landmarks[0].tolist(), [320.0, 256.0])
        self.assertEqual(face.landmarks[2].tolist(), [352.0, 288.0])
        self.assertAlmostEqual(face.score, 1.0)
        (feed,) = fake.feeds
        self.assertEqual(feed["input"].shape, (1, 3, 640, 640))

    def test_a_low_score_is_no_face(self):
        outputs = self._outputs()
        outputs["obj_32"][:] = 0.5  # sqrt(1 * 0.5) = 0.71, under 0.8
        with _session(FakeSession(outputs)):
            self.assertEqual(
                YuNetSFaceBackend().detect(np.zeros((64, 64, 3), np.uint8)), []
            )

    def test_embeds_an_aligned_face(self):
        fake = FakeSession({"fc1": np.arange(128, dtype=np.float32)[None]})
        with _session(fake):
            vector = YuNetSFaceBackend().embed(np.zeros((112, 112, 3), np.uint8))

        self.assertEqual(vector.shape, (128,))
        self.assertEqual(fake.feeds[0]["data"].shape, (1, 3, 112, 112))


class ScrfdDecodingTests(SimpleTestCase):
    def test_decodes_distances_from_the_anchor_centre(self):
        outputs = {}
        for kind, width in (("score", 1), ("bbox", 4), ("kps", 10)):
            for stride in (8, 16, 32):
                anchors = (640 // stride) ** 2 * 2
                outputs[f"{kind}{stride}"] = np.zeros((anchors, width), np.float32)
        # Second anchor of the stride-8 cell at column 10, row 3.
        index = (3 * 80 + 10) * 2 + 1
        outputs["score8"][index] = 0.9
        outputs["bbox8"][index] = [1, 2, 3, 4]
        outputs["kps8"][index] = [0.5, 0.5] * 5
        fake = FakeSession(outputs)

        with _session(fake):
            (face,) = ScrfdArcFaceBackend().detect(np.zeros((640, 640, 3), np.uint8))

        # Anchor centre (80, 24); distances x8.
        self.assertEqual(face.box, (72.0, 8.0, 32.0, 48.0))
        self.assertEqual(face.landmarks[0].tolist(), [84.0, 28.0])
        self.assertAlmostEqual(face.score, 0.9, places=5)

    def test_embeds_an_aligned_face(self):
        fake = FakeSession({"683": np.ones((1, 512), np.float32)})
        with _session(fake):
            vector = ScrfdArcFaceBackend().embed(np.full((112, 112, 3), 255, np.uint8))

        self.assertEqual(vector.shape, (512,))
        self.assertAlmostEqual(float(fake.feeds[0]["input.1"].max()), 1.0)


class GeometryTests(SimpleTestCase):
    def test_letterbox_scales_and_pads(self):
        square, scale = letterbox(np.full((100, 200, 3), 255, np.uint8), 64)

        self.assertEqual(scale, 64 / 200)
        self.assertEqual(square.shape, (64, 64, 3))
        self.assertEqual(square[0, 0].tolist(), [255, 255, 255])
        self.assertEqual(square[63, 0].tolist(), [0, 0, 0])

    def test_nms_keeps_the_best_of_overlapping_boxes(self):
        boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], float)

        self.assertEqual(nms(boxes, np.array([0.8, 0.9, 0.5]), 0.3), [1, 2])

    def test_similarity_transform_recovers_scale_rotation_and_shift(self):
        src = _REFERENCE * 2 + [10, 20]

        matrix = similarity_transform(src, _REFERENCE)

        np.testing.assert_allclose(matrix, [[0.5, 0, -5], [0, 0.5, -10]], atol=1e-9)

    def test_align_is_the_identity_on_a_face_already_aligned(self):
        image = np.random.default_rng(1).integers(0, 255, (112, 112, 3), dtype=np.uint8)

        aligned = align(image, _REFERENCE)

        self.assertEqual(aligned.shape, (ALIGNED_SIZE, ALIGNED_SIZE, 3))
        self.assertLess(np.abs(aligned.astype(int) - image).mean(), 1)


class RuntimeTests(SimpleTestCase):
    def tearDown(self):
        runtime._sessions.clear()

    @override_settings(PHOTOS_ONNX_THREADS=2)
    def test_one_session_per_model_with_the_configured_threads(self):
        fake_ort = types.SimpleNamespace(
            SessionOptions=lambda: types.SimpleNamespace(),
            InferenceSession=MagicMock(side_effect=lambda *a, **k: object()),
        )
        model = ModelFile(name="m.onnx", sha256="x", url="u")
        with (
            patch.dict(sys.modules, {"onnxruntime": fake_ort}),
            patch.object(weights, "ensure", return_value=Path("/models/m.onnx")),
        ):
            first = runtime.session(model)
            second = runtime.session(model)

        self.assertIs(first, second)
        fake_ort.InferenceSession.assert_called_once()
        options = fake_ort.InferenceSession.call_args.args[1]
        self.assertEqual(options.intra_op_num_threads, 2)


class DownloadTests(SimpleTestCase):
    def test_a_failed_download_is_a_weights_error(self):
        with (
            patch.object(httpx2, "stream", side_effect=httpx2.ConnectError("down")),
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(weights.WeightsError),
        ):
            weights._fetch("https://example.invalid/m.onnx", Path(tmp) / "m")

    def test_streams_the_body_and_hashes_it(self):
        response = MagicMock()
        response.iter_bytes.return_value = [b"ab", b"cd"]
        stream = MagicMock()
        stream.__enter__.return_value = response
        with (
            patch.object(httpx2, "stream", return_value=stream),
            tempfile.TemporaryDirectory() as tmp,
        ):
            target = Path(tmp) / "m"
            digest = weights._fetch("https://example.invalid/m.onnx", target)
            self.assertEqual(target.read_bytes(), b"abcd")

        import hashlib

        self.assertEqual(digest, hashlib.sha256(b"abcd").hexdigest())


class HealthTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(PHOTOS_MODEL_DIR=self.tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        weights._verified.clear()
        self.addCleanup(weights._verified.clear)
        import hashlib

        self.model = ModelFile(
            name="m.onnx", sha256=hashlib.sha256(b"ok").hexdigest(), url="u"
        )

    def test_missing_weights_are_fine_they_download_on_first_use(self):
        health = weights_health("X", (self.model,))

        self.assertTrue(health.ok)
        self.assertIn("first use", health.detail)

    def test_intact_weights_are_ready(self):
        weights.model_path(self.model).write_bytes(b"ok")

        self.assertEqual(weights_health("X", (self.model,)).detail, "X, ready")

    def test_corrupt_weights_are_an_error(self):
        weights.model_path(self.model).write_bytes(b"corrupt")

        self.assertFalse(weights_health("X", (self.model,)).ok)

    def test_no_onnxruntime_is_an_error(self):
        with patch(
            "workspace.photos.services.detection.health.find_spec", return_value=None
        ):
            health = weights_health("X", (self.model,))

        self.assertFalse(health.ok)
        self.assertIn("onnxruntime", health.detail)

    def test_every_real_backend_reports_on_its_weights(self):
        for key in backend_keys():
            self.assertTrue(get_face_backend(key).health().ok, key)


class DownloadCommandTests(SimpleTestCase):
    def test_downloads_the_configured_backend(self):
        with patch.object(YuNetSFaceBackend, "prepare") as prepare:
            out = StringIO()
            call_command("download_face_models", stdout=out)

        prepare.assert_called_once()
        self.assertIn("face_detection_yunet_2023mar.onnx", out.getvalue())

    @override_settings(PHOTOS_FACE_BACKEND="nope")
    def test_an_unknown_backend_fails(self):
        with self.assertRaises(CommandError):
            call_command("download_face_models")

    def test_a_failed_download_fails_the_command(self):
        with (
            patch.object(
                ScrfdArcFaceBackend, "prepare", side_effect=weights.WeightsError("hash")
            ),
            self.assertRaises(CommandError),
        ):
            call_command("download_face_models", "--backend", "scrfd_arcface")
