"""The face bench (scripts/face_bench.py) and its photo dataset.

The scoring is what decides whether a change to detection or grouping is an
improvement, so it is pinned here on hand-made cases. The scripts are loaded
from their path: they live outside the ``workspace`` package. The bench
itself downloads real photos and weights; here it runs on drawn pictures
with the fake backend.
"""

import gzip
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from PIL import ExifTags, Image

from workspace.files.models import File
from workspace.photos.services.detection.fake import FakeFaceBackend

from .faces import FacesTestMixin, faces_on
from .images import ALICE, BOB, faces_png

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"

User = get_user_model()


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bench = _load("face_bench")
dataset = _load("photo_dataset")


class MatchFacesTests(SimpleTestCase):
    HEAD = (100, 100, 60, 80)

    def test_a_face_inside_a_head_box_is_that_person(self):
        self.assertEqual(bench.match_faces([(110, 120, 40, 45)], [self.HEAD]), [(0, 0)])

    def test_a_face_whose_centre_is_outside_the_head_is_not(self):
        self.assertEqual(bench.match_faces([(150, 120, 40, 45)], [self.HEAD]), [])

    def test_a_box_far_smaller_or_larger_than_the_head_is_not_its_face(self):
        tiny = (128, 138, 4, 4)
        huge = (40, 40, 180, 200)
        self.assertEqual(bench.match_faces([tiny, huge], [self.HEAD]), [])

    def test_each_head_takes_the_face_that_overlaps_it_most(self):
        loose, tight = (105, 105, 25, 30), (108, 110, 45, 60)
        self.assertEqual(bench.match_faces([loose, tight], [self.HEAD]), [(1, 0)])


class SizeBucketTests(SimpleTestCase):
    def test_buckets_by_head_height(self):
        self.assertEqual(
            [bench.size_bucket(h) for h in (10, 24, 50, 200)],
            ["0-24px", "24-48px", "48-96px", "96px+"],
        )


def _one_album(*faces):
    """(identity, group) pairs as faces of a single album."""
    return [(identity, "album", group) for identity, group in faces]


class ScoreGroupingTests(SimpleTestCase):
    def test_everyone_in_their_own_group_is_perfect(self):
        score = bench.score_grouping(
            _one_album(("ann", 1), ("ann", 1), ("ann", 1), ("bob", 2), ("bob", 2))
        )
        rates = score.rates()

        self.assertEqual(rates["pairwise_precision"], 1.0)
        self.assertEqual(rates["pairwise_recall"], 1.0)
        self.assertEqual(rates["bcubed_f1"], 1.0)
        self.assertEqual(rates["people_found"], 1.0)
        self.assertEqual((score.merges, score.mixed_groups, score.misplaced), (0, 0, 0))

    def test_a_person_split_over_two_groups_needs_a_merge(self):
        score = bench.score_grouping(
            _one_album(("ann", 1), ("ann", 1), ("ann", 2), ("ann", 2))
        )
        rates = score.rates()

        self.assertEqual(score.merges, 1)
        self.assertEqual(rates["pairwise_precision"], 1.0)
        # Two right pairs out of the six ann's four faces make.
        self.assertAlmostEqual(rates["pairwise_recall"], 2 / 6)

    def test_two_people_in_one_group_count_the_faces_to_take_out(self):
        score = bench.score_grouping(_one_album(("ann", 1), ("ann", 1), ("bob", 1)))
        rates = score.rates()

        self.assertEqual((score.mixed_groups, score.misplaced), (1, 1))
        self.assertAlmostEqual(rates["pairwise_precision"], 1 / 3)
        self.assertEqual(rates["pairwise_recall"], 1.0)
        # ann's faces: 2/3 precise, bob's: 1/3; all of them complete.
        self.assertAlmostEqual(rates["bcubed_precision"], (2 / 3 + 2 / 3 + 1 / 3) / 3)
        self.assertEqual(rates["bcubed_recall"], 1.0)

    def test_a_face_in_no_group_is_a_group_of_its_own(self):
        score = bench.score_grouping(_one_album(("ann", 1), ("ann", 1), ("ann", None)))
        rates = score.rates()

        self.assertAlmostEqual(rates["grouped"], 2 / 3)
        self.assertEqual(rates["bcubed_precision"], 1.0)
        self.assertAlmostEqual(rates["bcubed_recall"], (2 / 3 + 2 / 3 + 1 / 3) / 3)
        self.assertEqual(score.merges, 0)

    def test_only_people_seen_twice_count_as_people_to_find(self):
        score = bench.score_grouping(_one_album(("ann", 1), ("ann", 1), ("bob", None)))

        self.assertEqual((score.people, score.people_found), (1, 1))

    def test_two_ids_of_two_albums_in_one_group_are_not_a_mistake(self):
        """PIPA often gives the same child a new id in each album: whether
        two ids of two albums are one person is unknown, not "no"."""
        score = bench.score_grouping(
            [("kid-a", "a", 1), ("kid-a", "a", 1), ("kid-b", "b", 1)]
        )
        rates = score.rates()

        self.assertEqual(rates["pairwise_precision"], 1.0)
        self.assertEqual(rates["bcubed_precision"], 1.0)
        self.assertEqual(
            (score.wrong_pairs, score.mixed_groups, score.misplaced), (0, 0, 0)
        )

    def test_one_id_across_two_albums_is_one_person(self):
        score = bench.score_grouping(
            [("ann", "a", 1), ("ann", "a", 1), ("ann", "b", 2), ("ann", "b", 2)]
        )

        self.assertEqual(score.merges, 1)
        self.assertAlmostEqual(score.rates()["pairwise_recall"], 2 / 6)

    def test_two_ids_of_one_album_in_a_group_are_two_people(self):
        score = bench.score_grouping(
            [("ann", "a", 1), ("bob", "a", 1), ("kid", "b", 1)]
        )

        self.assertEqual(
            (score.wrong_pairs, score.mixed_groups, score.misplaced), (1, 1, 1)
        )

    def test_scores_add_up_across_libraries(self):
        total = bench.GroupingScore()
        total.add(bench.score_grouping(_one_album(("ann", 1), ("ann", 1))))
        total.add(bench.score_grouping(_one_album(("bob", 7), ("bob", 8))))

        self.assertEqual(
            (total.faces, total.true_pairs, total.correct_pairs), (4, 2, 1)
        )
        self.assertEqual(total.merges, 1)


class ReportTests(SimpleTestCase):
    def _report(self, found, merges):
        result = bench.LibraryResult(library="lib", photos=2, heads=4)
        result.found["48-96px"] = found
        result.matched = _one_album(("ann", 1), ("ann", 1 + merges))
        result.grouping = bench.score_grouping(result.matched)
        return bench.summarize([result], {"backend": "fake", "weights_mb": 1.0})

    def test_an_earlier_report_can_be_scored_again(self):
        report = json.loads(json.dumps(self._report(3, 1)))

        again = bench.rescore(report)

        self.assertEqual(again["grouping"], report["grouping"])
        self.assertEqual(again["by_size"], report["by_size"])
        self.assertEqual(again["backend"], "fake")

    def test_compares_a_run_with_an_earlier_one(self):
        text = bench.render(self._report(3, 0), baseline=self._report(2, 1))

        self.assertIn("75.0%   was    50.0%  (+25.0 pt, better)", text)
        self.assertIn("Groups to merge", text)
        self.assertRegex(text, r"Groups to merge\s+0\s+was\s+1  \(-1, better\)")

    def test_the_report_is_json(self):
        json.dumps(self._report(3, 0))


class DatasetTests(SimpleTestCase):
    def _photo(self, **fields):
        return dataset.Photo(
            **{
                "photo_id": 1,
                "library": "12@N00",
                "author": "Jo",
                "taken": "2010-03-06T18:57:24",
                "title": "Molly",
                "tags": (),
                "license_url": "http://creativecommons.org/licenses/by/2.0/",
                "key": "0" * 32,
                "sha256": "0" * 64,
                "width": 500,
                "height": 250,
                "annotated_at": 1024,
                **fields,
            }
        )

    def test_scales_pipa_boxes_to_the_downloaded_copy(self):
        annotations = {1: [("album9", 512.0, 100.0, 64.0, 128.0, "57")]}

        (photo,) = dataset.with_faces([self._photo()], annotations)

        scale = 500 / 1024
        self.assertEqual(photo.album, "album9")
        self.assertEqual(
            photo.faces,
            (dataset.Face((512 * scale, 100 * scale, 64 * scale, 128 * scale), "57"),),
        )

    def test_a_photo_annotated_at_its_own_size_keeps_its_boxes(self):
        annotations = {1: [("a", 10.0, 20.0, 30.0, 40.0, "57")]}

        (photo,) = dataset.with_faces([self._photo(annotated_at=500)], annotations)

        self.assertEqual(photo.faces[0].box, (10.0, 20.0, 30.0, 40.0))

    def test_libraries_come_largest_first_and_oldest_photo_first(self):
        photos = [
            self._photo(photo_id=1, library="a", taken="2011-01-01T00:00:00"),
            self._photo(photo_id=2, library="b", taken="2012-01-01T00:00:00"),
            self._photo(photo_id=3, library="b", taken="2010-01-01T00:00:00"),
        ]

        libraries = dataset.by_library(photos)

        self.assertEqual(list(libraries), ["b", "a"])
        self.assertEqual([p.photo_id for p in libraries["b"]], [3, 2])

    def test_credits_the_author_as_cc_by_asks(self):
        self.assertEqual(
            self._photo().credit,
            '"Molly" by Jo (https://www.flickr.com/photos/12@N00/1/), CC BY 2.0',
        )

    def test_a_download_is_checked_against_its_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            source.write_bytes(b"photo bytes")
            target = Path(directory) / "cache" / "photo.jpg"
            good = hashlib.sha256(b"photo bytes").hexdigest()

            with self.assertRaises(dataset.DatasetError):
                dataset._ensure(target, source.as_uri(), "0" * 64)
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [])

            dataset._ensure(target, source.as_uri(), good)
            self.assertEqual(target.read_bytes(), b"photo bytes")


class ManifestTests(SimpleTestCase):
    """The committed manifest, whatever rebuilt it, is one the bench can use."""

    def test_every_photo_is_cc_by_and_pinned(self):
        with gzip.open(dataset.MANIFEST, "rt") as handle:
            rows = [json.loads(line) for line in handle]
        photos = dataset.load_manifest()

        self.assertEqual(len(photos), len(rows))
        self.assertEqual(len({photo.photo_id for photo in photos}), len(photos))
        for photo in photos:
            self.assertEqual(
                photo.license_url, "http://creativecommons.org/licenses/by/2.0/"
            )
            self.assertRegex(photo.sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(photo.key, r"^[0-9a-f]{32}$")
            self.assertRegex(photo.taken, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
            self.assertIn(photo.annotated_at, (1024, max(photo.width, photo.height)))
            self.assertTrue(photo.author)


class SeedRealPhotosTests(FacesTestMixin, TestCase):
    """seed_demo.py --real-photos, on a two-photo library and no network."""

    def setUp(self):
        self.seeder = _load("seed_demo")
        self.user = User.objects.create_user(username="demo", password="p")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.photos, paths = [], {}
        for n in (1, 2):
            path = Path(directory.name) / f"{n}.jpg"
            Image.new("RGB", (40, 30), (n * 60, 10, 10)).save(path, format="JPEG")
            paths[n] = path
            self.photos.append(
                dataset.Photo(
                    photo_id=n,
                    library="12@N00",
                    author="Jo",
                    taken=f"2010-01-0{n}T10:00:00",
                    title="",
                    tags=(),
                    license_url="",
                    key="",
                    sha256="",
                    width=40,
                    height=30,
                    annotated_at=40,
                )
            )
        for name, value in (
            ("load_manifest", lambda: self.photos),
            ("fetch_images", lambda photos: paths),
        ):
            patcher = patch.object(dataset, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _names(self):
        return sorted(
            File.objects.filter(owner=self.user, parent__name="Pictures").values_list(
                "name", flat=True
            )
        )

    def test_an_import_that_stops_halfway_leaves_nothing_to_collide_with(self):
        """The library is only marked imported by CREDITS.txt, written last:
        photos left behind by a failed run made the next one fail on their
        names."""
        create_file = self.seeder.FileService.create_file
        calls = []

        def failing_second_time(*args, **kwargs):
            calls.append(kwargs["name"])
            if len(calls) == 2:
                raise OSError("disk full")
            return create_file(*args, **kwargs)

        with (
            patch.object(
                self.seeder.FileService, "create_file", side_effect=failing_second_time
            ),
            self.assertRaises(OSError),
            redirect_stdout(io.StringIO()),
        ):
            self.seeder.import_real_photos([self.user], 1, history_days=30)
        self.assertEqual(self._names(), [])

        with redirect_stdout(io.StringIO()):
            self.seeder.import_real_photos([self.user], 1, history_days=30)

        self.assertEqual(self._names(), ["CREDITS.txt", "IMG_0001.jpg", "IMG_0002.jpg"])

    def test_says_when_fewer_libraries_than_asked_can_be_given(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.seeder.import_real_photos([self.user], 3, history_days=30)

        self.assertIn("1 libraries for the 3 asked", out.getvalue())


class SeedCaptureDateTests(SimpleTestCase):
    def test_restores_the_capture_date_without_reencoding(self):
        seeder = _load("seed_demo")
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), (200, 10, 10)).save(buffer, format="JPEG")
        original = buffer.getvalue()

        dated = seeder._with_capture_date(original, "2010-03-06T18:57:24")

        with Image.open(io.BytesIO(dated)) as image:
            exif = image.getexif().get_ifd(ExifTags.IFD.Exif)
            self.assertEqual(
                exif[ExifTags.Base.DateTimeOriginal], "2010:03:06 18:57:24"
            )
        # The scan data is the original's, byte for byte.
        scan = original.index(b"\xff\xda")
        self.assertTrue(dated.endswith(original[scan:]))


@faces_on
class RunLibraryTests(FacesTestMixin, TestCase):
    """One library through the real pipeline, on the fake backend."""

    ANN, BOB = (60, 60, 50), (250, 60, 50)

    def _library(self, drawn):
        photos, paths = [], {}
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for n, faces in enumerate(drawn, 1):
            path = Path(directory.name) / f"{n}.png"
            path.write_bytes(faces_png(*faces))
            heads = tuple(
                # The head box: the drawn face and some hair around it.
                dataset.Face(
                    (x - 10, y - 15, side + 20, side + 25),
                    "ann" if colour == ALICE else "bob",
                )
                for colour, (x, y, side) in faces
            )
            photos.append(
                dataset.Photo(
                    photo_id=n,
                    library="lib",
                    author="Jo",
                    taken=f"2010-01-0{n}T10:00:00",
                    title="",
                    tags=(),
                    license_url="",
                    key="",
                    sha256="",
                    width=400,
                    height=300,
                    annotated_at=400,
                    faces=heads,
                )
            )
            paths[n] = path
        return photos, paths

    def test_scores_what_the_pipeline_found_and_grouped(self):
        photos, paths = self._library(
            [
                ((ALICE, self.ANN), (BOB, self.BOB)),
                ((ALICE, self.ANN),),
                ((BOB, self.BOB),),
                ((ALICE, self.ANN),),
            ]
        )

        result = bench.run_library(1, "lib", photos, paths, keep=False)

        self.assertEqual((result.photos, result.heads), (4, 5))
        self.assertEqual(sum(result.found.values()), 5)
        self.assertEqual(sum(result.detected.values()), 5)
        self.assertEqual(result.extra_faces, 0)
        rates = result.grouping.rates()
        self.assertEqual(rates["pairwise_precision"], 1.0)
        self.assertEqual(rates["pairwise_recall"], 1.0)
        self.assertGreater(result.model_cpu, 0)
        self.assertTrue(re.match(r"bench01", User.objects.get().username))

    def test_a_failed_analysis_stops_the_bench(self):
        """The pipeline logs a failure and moves on: scored, it would read as
        a photo with nobody in it."""
        photos, paths = self._library([((ALICE, self.ANN),)])

        with (
            patch.object(FakeFaceBackend, "detect", side_effect=RuntimeError("boom")),
            self.assertLogs("workspace.photos.services.face_analysis", "ERROR"),
            self.assertRaises(bench.BenchError),
        ):
            bench.run_library(1, "lib", photos, paths, keep=False)
