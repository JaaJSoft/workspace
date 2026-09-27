import numpy as np
from django.test import SimpleTestCase

from workspace.photos.services.face_images import Detection
from workspace.photos.services.face_tracking import BEST_FACES, Tracker

THRESHOLD = 0.5


def _vector(seed, dims=16):
    return np.random.default_rng(seed).standard_normal(dims)


def _near(vector, distance, seed=99):
    """A unit vector at cosine *distance* from *vector*."""
    unit = vector / np.linalg.norm(vector)
    other = _vector(seed, len(vector))
    other -= (other @ unit) * unit
    other /= np.linalg.norm(other)
    similarity = 1 - distance
    return similarity * unit + np.sqrt(1 - similarity**2) * other


def _face(vector, box=(10, 10, 50, 50), quality=0.9):
    return Detection(
        box=box,
        landmarks=np.zeros((5, 2)),
        score=0.99,
        quality=quality,
        embedding=vector,
        crop=b"",
        width=400,
        height=300,
    )


ALICE = _vector(1)
BOB = _vector(2)


class ChainingTests(SimpleTestCase):
    def test_a_face_that_stays_in_place_is_one_track(self):
        tracker = Tracker(THRESHOLD)
        for frame in range(3):
            tracker.add(frame, [_face(ALICE, box=(10 + frame * 5, 10, 50, 50))])

        (track,) = tracker.tracks()

        self.assertEqual(track.frames, {0, 1, 2})

    def test_two_people_side_by_side_are_two_tracks(self):
        tracker = Tracker(THRESHOLD)
        for frame in range(3):
            tracker.add(
                frame,
                [_face(ALICE, box=(10, 10, 50, 50)), _face(BOB, box=(200, 10, 50, 50))],
            )

        self.assertEqual(len(tracker.tracks()), 2)

    def test_someone_else_stepping_into_the_same_place_starts_a_track(self):
        tracker = Tracker(THRESHOLD)
        tracker.add(0, [_face(ALICE)])
        tracker.add(1, [_face(BOB)])

        tracks = tracker.tracks()

        self.assertEqual(len(tracks), 2)
        self.assertEqual(
            {frozenset(t.frames) for t in tracks}, {frozenset({0}), frozenset({1})}
        )


class MergingTests(SimpleTestCase):
    def test_someone_who_comes_back_is_merged_into_their_track(self):
        tracker = Tracker(THRESHOLD)
        tracker.add(0, [_face(ALICE, box=(10, 10, 50, 50))])
        tracker.add(1, [])
        tracker.add(2, [_face(_near(ALICE, 0.1), box=(300, 200, 50, 50))])

        (track,) = tracker.tracks()

        self.assertEqual(track.frames, {0, 2})

    def test_a_look_alike_past_the_merge_limit_stays_apart(self):
        tracker = Tracker(THRESHOLD)
        tracker.add(0, [_face(ALICE)])
        tracker.add(1, [])
        # Close enough for grouping to consider, too far for one video.
        tracker.add(2, [_face(_near(ALICE, 0.4))])

        self.assertEqual(len(tracker.tracks()), 2)

    def test_two_faces_of_one_frame_never_merge(self):
        tracker = Tracker(THRESHOLD)
        tracker.add(0, [_face(ALICE, box=(10, 10, 50, 50))])
        # A twin, or a mirror: the same embedding twice in one frame.
        tracker.add(
            1,
            [_face(ALICE, box=(200, 10, 50, 50)), _face(ALICE, box=(300, 10, 50, 50))],
        )

        tracks = tracker.tracks()

        self.assertEqual(len(tracks), 2)
        for track in tracks:
            self.assertEqual(len(track.faces), len(track.frames))


class TrackFaceTests(SimpleTestCase):
    def test_the_best_face_stands_for_the_track(self):
        tracker = Tracker(THRESHOLD)
        tracker.add(0, [_face(ALICE, quality=0.3)])
        tracker.add(1, [_face(ALICE, quality=0.9)])
        tracker.add(2, [_face(ALICE, quality=0.6)])

        (track,) = tracker.tracks()

        self.assertEqual(track.best().frame, 1)

    def test_the_embedding_is_the_weighted_mean_of_the_best_faces(self):
        sharp = [
            (_near(ALICE, 0.05, seed=10 + i), 0.9 - 0.1 * i) for i in range(BEST_FACES)
        ]
        blurred = _near(ALICE, 0.2, seed=5)
        tracker = Tracker(THRESHOLD)
        for frame, (vector, quality) in enumerate(sharp):
            tracker.add(frame, [_face(vector, quality=quality)])
        tracker.add(len(sharp), [_face(blurred, quality=0.2)])

        (track,) = tracker.tracks()

        expected = sum(v / np.linalg.norm(v) * q for v, q in sharp)
        np.testing.assert_allclose(
            track.embedding(), expected / np.linalg.norm(expected), atol=1e-9
        )
