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


class VariantTests(SimpleTestCase):
    def test_every_variant_is_a_picture_of_the_same_scene(self):
        image = Image.new("RGB", (200, 100), (200, 30, 30))

        copies = bench.variants(image)

        self.assertEqual(len(copies), len(bench.VARIANTS))
        for copy in copies:
            self.assertEqual(copy.mode, "RGB")
            self.assertGreater(copy.width, copy.height)
        self.assertEqual(copies[1].size, (100, 50))


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
            for index in range(6):
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
            with patch.dict(os.environ):
                run = bench.embed("fake", photos, paths, queries, duplicates=2)

        report = bench.score(run, photos, queries)
        report |= {k: run[k] for k in ("label", "dims", "cost", "thresholds")}

        self.assertEqual(report["search"]["map"], 1.0)
        self.assertEqual(report["duplicates"]["at_threshold"]["recall"], 1.0)
        self.assertEqual(run["copies"].shape, (2, len(bench.VARIANTS), 512))
        table = bench.render([report])
        self.assertIn("fake (512-d)", table)
        self.assertIn("mAP (English)  ", table)
        self.assertIn("100.0%", table)
