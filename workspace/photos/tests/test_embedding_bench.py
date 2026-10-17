"""The embedding bench (scripts/embedding_bench.py) and its dataset.

The scoring decides which model a deployment runs and where its thresholds
sit, so it is pinned here on hand-made vectors. The scripts are loaded from
their path: they live outside the ``workspace`` package. The bench itself
downloads Open Images photos and model weights; here it runs on drawn
pictures with the fake backend.
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase, override_settings
from PIL import Image

from workspace.files.tests.videos import requires_ffmpeg

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bench = _load("embedding_bench")
dataset = _load("embedding_dataset")


def _direction(angle):
    """A unit vector of 2-d at *angle* degrees: cosine distance between two
    of them is 1 - cos(their angle)."""
    rad = np.radians(angle)
    return [np.cos(rad), np.sin(rad)]


class RankingTests(SimpleTestCase):
    def test_average_precision(self):
        # Ranked: relevant, not, relevant -> (1/1 + 2/3) / 2.
        self.assertAlmostEqual(
            bench.average_precision([0.1, 0.2, 0.3], [True, False, True]), 5 / 6
        )
        self.assertEqual(bench.average_precision([0.1, 0.2], [False, False]), 0.0)

    def test_precision_in_the_first_k(self):
        self.assertEqual(
            bench.precision_at([0.3, 0.1, 0.2], [True, False, False], k=2), 0.0
        )

    def test_at_threshold_counts_what_it_returns(self):
        matrix = np.array([[0.1, 0.2, 0.9], [0.8, 0.9, 0.95]])
        relevant = np.array([[True, False, True], [True, False, False]])

        scores = bench.at_threshold(matrix, relevant, 0.5)

        # Row 1 returns two, one right; row 2 returns nothing.
        self.assertEqual(scores["precision"], 0.5)
        self.assertEqual(scores["recall"], (1 / 2 + 0) / 2)
        self.assertEqual(scores["results"], 1.0)

    def test_the_best_threshold_separates_right_from_wrong(self):
        matrix = np.array([[0.1, 0.2, 0.6, 0.7]])
        relevant = np.array([[True, True, False, False]])

        best = bench.best_threshold(matrix, relevant)

        self.assertGreaterEqual(best["threshold"], 0.2)
        self.assertLess(best["threshold"], 0.6)
        self.assertEqual(best["f1"], 1.0)

    def test_distances_are_cosine_whatever_the_length(self):
        matrix = bench.distances([[2.0, 0.0]], [[0.0, 3.0], [5.0, 0.0]])

        np.testing.assert_allclose(matrix, [[1.0, 0.0]], atol=1e-12)


class DuplicateScoreTests(SimpleTestCase):
    def test_finds_its_copies_and_suggests_the_loosest_clean_threshold(self):
        images = np.array([_direction(0), _direction(90), _direction(180)])
        # Two copies of the first two photos, a few degrees off.
        copies = np.array(
            [[_direction(2), _direction(-3)], [_direction(91), _direction(88)]]
        )

        scores = bench.score_duplicates(images, copies, threshold=0.01)

        self.assertEqual(scores["at_threshold"]["recall"], 1.0)
        self.assertEqual(scores["at_threshold"]["precision"], 1.0)
        # Past 1 - cos(88 deg) a copy of the other photo creeps in.
        suggested = scores["suggested"]["threshold"]
        self.assertLess(suggested, 1 - np.cos(np.radians(88)))
        self.assertGreater(suggested, 0.9)
        self.assertEqual(set(scores["by_variant"]), {"re-encoded", "halved"})

    def test_a_photo_is_never_its_own_copy(self):
        images = np.array([_direction(0), _direction(90)])
        copies = np.array([[_direction(40)]])

        scores = bench.score_duplicates(images, copies, threshold=0.5)

        # The photo itself, at distance 0, is no mistake: the closest one is
        # the other photo, 90 deg away.
        self.assertAlmostEqual(scores["closest_other"], 1.0)
        self.assertEqual(scores["at_threshold"]["precision"], 1.0)


class SimilarScoreTests(SimpleTestCase):
    def test_a_photo_showing_the_same_thing_is_a_right_answer(self):
        vectors = np.array([_direction(0), _direction(5), _direction(120)])
        labels = [frozenset({"Dog"}), frozenset({"Dog"}), frozenset({"Cat"})]

        scores = bench.score_similar(vectors, labels, threshold=0.1)

        self.assertEqual(scores["map"], (1 + 1 + 0) / 3)
        self.assertEqual(scores["at_threshold"]["precision"], 1.0)

    def test_photos_showing_none_of_the_queries_ask_nothing(self):
        vectors = np.array([_direction(0), _direction(5)])

        scores = bench.score_similar(vectors, [frozenset({"Dog"}), frozenset()], 0.1)

        # One row only, whose one candidate shows something else.
        self.assertEqual(scores["at_threshold"]["results"], 1.0)
        self.assertEqual(scores["at_threshold"]["precision"], 0.0)


class TaggingScoreTests(SimpleTestCase):
    def test_the_loosest_threshold_keeps_the_precision(self):
        row = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
        relevant = np.array([True, True, True, False, False])

        # Three right then two wrong: 3/4 at 0.4 is under 90%.
        self.assertEqual(bench.loosest_threshold(row, relevant, 0.9), 0.3)
        self.assertEqual(bench.loosest_threshold(row, relevant, 0.7), 0.4)

    def test_a_label_without_two_clean_tags_is_never_placed(self):
        row = np.array([0.1, 0.2, 0.3])

        self.assertTrue(
            np.isnan(bench.loosest_threshold(row, [False, True, False], 0.9))
        )
        self.assertTrue(
            np.isnan(bench.loosest_threshold(row, [True, False, False], 0.5))
        )

    def test_precision_counts_every_tag_placed(self):
        tagged = np.array([[True, True, False, False], [False, False, False, False]])
        relevant = np.array([[True, False, True, False], [True, False, False, False]])

        scores = bench.tag_scores(tagged, relevant)

        self.assertEqual(scores["precision"], 0.5)
        # Half of the first label's photos, none of the second's.
        self.assertEqual(scores["recall"], 0.25)
        self.assertEqual(scores["per_photo"], 0.5)

    def test_thresholds_are_scored_on_the_photos_they_were_not_fitted_on(self):
        # Even photos right under 0.3, odd ones right only under 0.1: a
        # threshold fitted on the even half lets the odd half's mistakes in.
        matrix = np.array([[0.1, 0.05, 0.2, 0.08, 0.25, 0.2, 0.9, 0.25]])
        relevant = np.array([[True, True, True, True, True, False, False, False]])

        scores = bench.held_out_tags(matrix, relevant, per_label=True)

        self.assertLess(scores["precision"], 1.0)
        self.assertEqual(scores["of"], 1)

    def test_one_shared_threshold_tags_every_label_alike(self):
        matrix = np.array([[0.1, 0.2], [0.15, 0.6]])
        relevant = np.array([[True, True], [True, False]])

        thresholds = bench.tag_thresholds(matrix, relevant, per_label=False)

        self.assertEqual(list(thresholds), [0.2, 0.2])

    def test_examples_are_left_out_of_the_tags_learnt_from_them(self):
        images = np.array(
            [_direction(0), _direction(10), _direction(5), _direction(90)]
        )
        relevant = np.array([[True, True, True, False]])

        matrix, remaining = bench.example_distances(images, relevant, count=2)

        # Learnt from the first two: the third is near their mean, the
        # fourth far, the examples themselves never scored.
        self.assertEqual(list(remaining[0]), [False, False, True, False])
        self.assertTrue(np.isinf(matrix[0, :2]).all())
        self.assertLess(matrix[0, 2], 0.01)
        self.assertGreater(matrix[0, 3], 0.5)


class FrameScoreTests(SimpleTestCase):
    def _vectors(self, frames, shots):
        # Photos 0 and 1 show a dog, photo 2 a cat; "dog" points at the dogs.
        return {
            "images": np.array([_direction(0), _direction(10), _direction(120)]),
            "frames": np.array(frames),
            "shots": np.array(shots),
            "english": np.array([_direction(5)]),
            "thresholds": {"duplicate": 0.01, "similar": 0.1, "description": 0.1},
        }

    def test_scores_the_frames_against_the_photos_they_were_filmed_from(self):
        vectors = self._vectors(
            frames=[_direction(2), _direction(12), _direction(121)],
            shots=[_direction(3), _direction(30)],
        )
        relevant = np.array([[True, True, False]])
        labels = [frozenset({"Dog"}), frozenset({"Dog"}), frozenset({"Cat"})]

        scores = bench.score_frames(vectors, relevant, labels)

        self.assertEqual(scores["duplicates"]["at_threshold"]["recall"], 1.0)
        self.assertEqual(set(scores["duplicates"]["by_variant"]), {"frame"})
        # The first shot's two frames are a degree apart, the second's 18.
        self.assertEqual(scores["shot"]["found"], 0.5)
        self.assertEqual(scores["search"]["map"], 1.0)
        # Each dog finds the other dog's frame, never its own.
        self.assertEqual(scores["similar"]["map"], (1 + 1 + 0) / 3)

    def test_nothing_without_frames(self):
        vectors = self._vectors(frames=np.empty((0, 2)), shots=np.empty((0, 2)))

        self.assertIsNone(bench.score_frames(vectors, np.zeros((1, 3)), []))


class VideoTagScoreTests(SimpleTestCase):
    def test_photos_are_dealt_into_videos_of_distinct_scenes(self):
        videos = bench.video_groups(np.arange(10), 3)

        self.assertEqual(videos.shape, (3, 3))
        # Each photo in one video at most; the one left over in none.
        self.assertEqual(len(set(videos.ravel())), 9)
        np.testing.assert_array_equal(videos, bench.video_groups(np.arange(10), 3))

    def test_a_video_lies_at_its_closest_key_frame_or_its_mean(self):
        frames = np.array([_direction(0), _direction(60)])
        tags = np.array([_direction(0)])
        matrix = bench.distances(tags, frames)
        videos = np.array([[0, 1]])

        closest = bench.video_distances(matrix, tags, frames, videos, "closest")
        mean = bench.video_distances(matrix, tags, frames, videos, "mean")

        self.assertAlmostEqual(closest[0, 0], 0.0)
        # The mean of the two points 30 degrees from the tag.
        self.assertAlmostEqual(mean[0, 0], 1 - np.cos(np.radians(30)))

    def test_a_video_shows_a_tag_one_of_its_scenes_shows(self):
        # Eight dogs then eight cats, "dog" pointing at the dogs.
        frames = np.array([_direction(i) for i in range(8)] + [_direction(90)] * 8)
        tags = np.array([_direction(3)])
        relevant = np.array([[True] * 8 + [False] * 8])
        thresholds = {"tags": {"dog": 0.01}, "video_tags": {"dog": 0.01}}

        report = bench.score_video_tags(tags, frames, relevant, ["dog"], thresholds)

        self.assertEqual(report["closest_1"]["precision"], 1.0)
        self.assertEqual(report["photos_1"]["recall"], 1.0)
        # Every video of three scenes holding a dog is a right one.
        self.assertEqual(report["closest_3"]["precision"], 1.0)
        self.assertEqual(report["at_threshold"]["labels"], 1)
        self.assertEqual(set(report["suggested"]), {"dog"})

    def test_a_tag_the_photos_table_leaves_out_is_left_out_of_videos(self):
        frames = np.array([_direction(i) for i in range(8)] + [_direction(90)] * 8)
        tags = np.array([_direction(3)])
        relevant = np.array([[True] * 8 + [False] * 8])
        thresholds = {"tags": {}, "video_tags": {}}

        report = bench.score_video_tags(tags, frames, relevant, ["dog"], thresholds)

        self.assertEqual(report["suggested"], {})
        self.assertEqual(report["closest_3"]["per_photo"], 0.0)


@requires_ffmpeg
class FilmTests(SimpleTestCase):
    def test_a_filmed_photo_comes_back_as_two_frames_of_its_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            Image.new("RGB", (65, 48), (200, 30, 30)).save(path)

            bench.film(path, tmp)

            frames = bench.frame_paths(tmp, "photo")
            self.assertEqual(len(frames), len(bench.FRAME_NUMBERS))
            for frame in frames:
                with Image.open(frame) as img:
                    self.assertEqual(img.size, (64, 48))
                    red, green, _blue = img.convert("RGB").getpixel((32, 24))
                    self.assertGreater(red, 150)
                    self.assertLess(green, 80)
            self.assertFalse((Path(tmp) / "photo.mp4").exists())


class VariantTests(SimpleTestCase):
    def test_every_variant_is_a_picture_of_the_same_scene(self):
        image = Image.new("RGB", (200, 100), (200, 30, 30))

        copies = bench.variants(image)

        self.assertEqual(len(copies), len(bench.VARIANTS))
        for copy in copies:
            self.assertEqual(copy.mode, "RGB")
            self.assertGreater(copy.width, copy.height)
        self.assertEqual(copies[1].size, (100, 50))


class WeightsTests(SimpleTestCase):
    def test_a_snapshot_linked_to_its_blob_counts_once(self):
        from workspace.photos.services.embedding import local

        backend = local.Siglip2BaseBackend()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(
                tmp,
                "fastembed",
                "models--onnx-community--siglip2-base-patch16-224-ONNX",
            )
            blob = repo / "blobs" / "0123abcd"
            blob.parent.mkdir(parents=True)
            blob.write_bytes(b"\0" * 1_000_000)
            snapshot = repo / "snapshots" / "rev" / "onnx" / "vision_model.onnx"
            snapshot.parent.mkdir(parents=True)
            try:
                snapshot.symlink_to(blob)
            except OSError:
                self.skipTest("symbolic links are not allowed here")

            with override_settings(PHOTOS_MODEL_DIR=tmp):
                self.assertEqual(bench._weights_mb(backend), 1.0)


class DatasetTests(SimpleTestCase):
    CLASSES = "LabelName,DisplayName\n/m/dog,Dog\n/m/snow,Snow\n/m/car,Car\n"
    LABELS = (
        "ImageID,Source,LabelName,Confidence\n"
        "a,verification,/m/dog,1\n"
        "a,verification,/m/snow,1\n"
        "b,verification,/m/dog,1\n"
        "b,verification,/m/snow,0\n"
        "c,verification,/m/car,1\n"
        "d,verification,/m/car,0\n"
    )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        (directory / "classes.csv").write_text(self.CLASSES, encoding="utf-8")
        (directory / "labels.csv").write_text(self.LABELS, encoding="utf-8")
        classes = dataset.read_classes(directory / "classes.csv")
        self.labels = dataset.read_labels(directory / "labels.csv", classes)

    def test_only_confirmed_labels_count(self):
        self.assertEqual(self.labels["a"], {"Dog", "Snow"})
        self.assertEqual(self.labels["b"], {"Dog"})
        # Verified absent: on no photo, but the photo is there.
        self.assertEqual(self.labels["d"], frozenset())

    def test_picks_the_right_answers_then_distractors(self):
        queries = (
            dataset.Query("dog", "chien", ("Dog",)),
            dataset.Query("dog in the snow", "chien dans la neige", ("Dog", "Snow")),
        )

        photos = dataset.pick(self.labels, queries, per_query=5, distractors=5)

        self.assertEqual({p.image_id for p in photos}, {"a", "b", "c", "d"})
        self.assertEqual(
            [q.text for q in dataset.usable_queries(photos, queries, minimum=2)],
            ["dog"],
        )
        # The same order on every machine.
        self.assertEqual(
            photos, dataset.pick(self.labels, queries, per_query=5, distractors=5)
        )

    def test_a_distractor_shows_none_of_the_queries(self):
        queries = (dataset.Query("car", "voiture", ("Car",)),)

        photos = dataset.pick(self.labels, queries, per_query=0, distractors=10)

        self.assertNotIn("c", {p.image_id for p in photos})

    def test_every_classic_tag_is_one_label(self):
        self.assertGreaterEqual(len(dataset.TAGS), 30)
        self.assertEqual(len({t.text for t in dataset.TAGS}), len(dataset.TAGS))
        for tag in dataset.TAGS:
            with self.subTest(tag=tag.text):
                self.assertEqual(len(tag.labels), 1)

    def test_every_query_asks_for_labels_and_speaks_french(self):
        for query in dataset.QUERIES:
            with self.subTest(query=query.text):
                self.assertTrue(query.labels)
                self.assertTrue(query.french)


@override_settings(PHOTOS_EMBEDDING_BACKEND="fake")
class EndToEndTests(SimpleTestCase):
    """The worker's side and the scoring, on drawn photos: a red photo is
    what "red" describes for the fake backend."""

    def test_embeds_scores_and_renders_a_backend(self):
        colours = {"red": (255, 0, 0), "blue": (0, 0, 255)}
        photos, paths = [], {}
        with tempfile.TemporaryDirectory() as tmp:
            # Four of each colour in either half of the photos: tag
            # thresholds are fitted on one half and scored on the other,
            # and the tags learnt from examples need some left to find.
            for index in range(16):
                name = "red" if index % 4 < 2 else "blue"
                path = Path(tmp) / f"{index}.jpg"
                Image.new("RGB", (64, 48), colours[name]).save(path)
                photo = dataset.BenchPhoto(str(index), frozenset({name.title()}))
                photos.append(photo)
                paths[photo.image_id] = path
            queries = [
                dataset.Query("red", "rouge", ("Red",)),
                dataset.Query("blue", "bleu", ("Blue",)),
            ]
            with patch.dict(os.environ):
                run = bench.embed(
                    "fake", photos, paths, queries, duplicates=2, tags=queries
                )

        report = bench.score(run, photos, queries, queries)
        report |= {k: run[k] for k in ("label", "dims", "cost", "thresholds")}

        self.assertEqual(report["search"]["map"], 1.0)
        self.assertEqual(report["duplicates"]["at_threshold"]["recall"], 1.0)
        self.assertEqual(run["copies"].shape, (2, len(bench.VARIANTS), 768))
        tagging = report["tagging"]
        self.assertEqual(tagging["at_threshold"]["precision"], 1.0)
        self.assertEqual(tagging["shared"]["recall"], 1.0)
        # Learnt from three red photos, the tag finds the other five.
        self.assertEqual(tagging["examples_3"]["recall"], 1.0)
        # The fake backend's table recognizes red alone.
        self.assertEqual(tagging["at_threshold"]["labels"], 1)
        self.assertEqual(set(tagging["suggested"]), {"red", "blue"})
        self.assertIsNone(report["frames"])
        table = bench.render([report])
        self.assertIn("fake (768-d)", table)
        self.assertIn("mAP (English)  ", table)
        self.assertIn("100.0%", table)
        self.assertIn("2/2 labels", table)

    @requires_ffmpeg
    def test_measures_the_video_frames_of_the_photos_filmed(self):
        colours = {"red": (255, 0, 0), "blue": (0, 0, 255)}
        photos, paths = [], {}
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(4):
                name = "red" if index % 2 else "blue"
                path = Path(tmp) / f"{index}.jpg"
                Image.new("RGB", (64, 48), colours[name]).save(path)
                photo = dataset.BenchPhoto(str(index), frozenset({name.title()}))
                photos.append(photo)
                paths[photo.image_id] = path
            queries = [
                dataset.Query("red", "rouge", ("Red",)),
                dataset.Query("blue", "bleu", ("Blue",)),
            ]
            frames_dir = Path(tmp) / "frames"
            frames_dir.mkdir()
            bench.film_all(photos, paths, frames_dir)
            with patch.dict(os.environ):
                run = bench.embed(
                    "fake", photos, paths, queries, duplicates=2, frames_dir=frames_dir
                )

        report = bench.score(run, photos, queries, [])

        self.assertEqual(run["frames"].shape, (4, 768))
        self.assertEqual(run["shots"].shape, (2, 768))
        self.assertEqual(report["frames"]["search"]["map"], 1.0)
        self.assertEqual(report["frames"]["shot"]["found"], 1.0)
