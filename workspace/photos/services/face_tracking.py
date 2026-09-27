"""Follow the people of a video across its sampled frames.

Two passes, over the faces the frames were found to hold:

- **Chaining**: a face joins the track of a face of the previous frame when
  their boxes overlap and their embeddings are much closer than the grouping
  threshold - the same person, a moment later, where they were.
- **Merging**: tracks whose embeddings are close are one person who left the
  frame and came back. Two tracks sharing a frame never merge: two faces of
  one frame are two people.

Each track then stands for one person of the video (see face_video.py), and
keeps the frame each of its faces came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .face_grouping import face_weight
from .face_images import Detection

# Neighbouring faces chain into one track when their boxes overlap this much
# (intersection over union)...
_LINK_IOU = 0.3
# ...and their embeddings are within this share of the grouping threshold.
_LINK_DISTANCE = 0.5
# Tracks merge when their embeddings are within this share of the threshold:
# stricter than grouping, which still gets to compare them with the library.
_MERGE_DISTANCE = 0.6
# A track's embedding is the quality-weighted mean of this many of its best
# faces: the blurred ones in between add noise, not information.
BEST_FACES = 5


@dataclass
class TrackedFace:
    frame: int
    detection: Detection
    vector: np.ndarray


@dataclass
class Track:
    """One person across the frames of a video."""

    faces: list[TrackedFace] = field(default_factory=list)

    @property
    def frames(self):
        return {face.frame for face in self.faces}

    @property
    def last(self):
        return self.faces[-1]

    def best_faces(self):
        """Its BEST_FACES best faces, the best first."""
        ranked = sorted(self.faces, key=lambda f: f.detection.quality, reverse=True)
        return ranked[:BEST_FACES]

    def best(self):
        return self.best_faces()[0]

    def embedding(self):
        """The quality-weighted mean of its best faces, unit length."""
        return _unit(
            sum(
                face.vector * face_weight(face.detection.quality)
                for face in self.best_faces()
            )
        )

    def centroid(self):
        # Merging compares every face of the track, the weak ones included:
        # a track seen only in profile still has to find its other half.
        return _unit(
            sum(
                face.vector * face_weight(face.detection.quality) for face in self.faces
            )
        )


class Tracker:
    """Chain the faces of each sampled frame, in frame order, into tracks.

    *threshold* is the grouping's cosine distance for one person.
    """

    def __init__(self, threshold):
        self.threshold = threshold
        self._tracks = []

    def add(self, frame, detections):
        """Take in the faces found in frame number *frame*."""
        faces = [TrackedFace(frame, d, _unit(d.embedding)) for d in detections]
        open_tracks = [t for t in self._tracks if t.last.frame == frame - 1]
        pairs = sorted(
            (
                (_iou(track.last.detection.box, face.detection.box), i, j)
                for i, track in enumerate(open_tracks)
                for j, face in enumerate(faces)
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        limit = self.threshold * _LINK_DISTANCE
        linked_tracks, linked_faces = set(), set()
        for iou, i, j in pairs:
            if iou < _LINK_IOU:
                break
            if i in linked_tracks or j in linked_faces:
                continue
            if _distance(open_tracks[i].last.vector, faces[j].vector) > limit:
                continue
            open_tracks[i].faces.append(faces[j])
            linked_tracks.add(i)
            linked_faces.add(j)
        for j, face in enumerate(faces):
            if j not in linked_faces:
                self._tracks.append(Track(faces=[face]))

    def tracks(self):
        """The people of the video: its tracks once merged, longest first."""
        return merge_tracks(self._tracks, self.threshold)


def merge_tracks(tracks, threshold):
    """Merge the tracks of one person, longest first.

    Greedy: each track, the longest first, joins the closest merged track
    within the limit that shares none of its frames, or stands alone.
    """
    limit = threshold * _MERGE_DISTANCE
    merged = []
    for track in sorted(tracks, key=lambda t: len(t.faces), reverse=True):
        centroid = track.centroid()
        frames = track.frames
        best, best_distance = None, limit
        for other in merged:
            if frames & other.frames:
                continue
            distance = _distance(centroid, other.centroid())
            if distance <= best_distance:
                best, best_distance = other, distance
        if best is None:
            merged.append(Track(faces=list(track.faces)))
        else:
            best.faces = sorted(best.faces + track.faces, key=lambda f: f.frame)
    return sorted(merged, key=lambda t: len(t.faces), reverse=True)


def _unit(vector):
    vector = np.asarray(vector, dtype=np.float64)
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


def _distance(a, b):
    return 1.0 - float(a @ b)


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0
