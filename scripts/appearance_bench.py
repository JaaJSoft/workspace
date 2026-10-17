#!/usr/bin/env python
"""Measure how well clothing names people whose face cannot, within an event.

For every person annotated in the libraries of scripts/photo_dataset.py, the
bench crops the torso under the head box - the head left out, so no face is
seen, as from behind - and embeds it with a photo embedding backend, one crop
at a time through ``EmbeddingBackend.embed_image``. Each torso is then named
after the nearest torso of someone whose face the face pipeline would have
named: in another photo of the same album, taken within --window hours but
not in the same burst (--burst minutes), and not one of the people already
named in the photo.

- **Anchored**: the people whose own anchor is among the candidates at all.
  The others can only be named wrongly, and no threshold tells them apart.
- **Nearest anchor right**: for the anchored, how often the nearest anchor is
  them, beside always answering the person most photographed in the album.
- **Named at 90, 95, 98% precision**: the people a distance threshold and a
  margin over the next person name, with what they get right. Both are fitted
  on half the libraries and scored on the other half, then the halves swap;
  the pair fitted on every library is what a backend would ship.

A torso is one person in one photo, so the shares are of the torsos with a
candidate. Other --window or --burst values change that number: two such runs
compare by the torsos named, not by the shares.
- The same by head size, and the CPU time per crop (one thread unless
  --onnx-threads says otherwise).

``histogram`` is no backend: a colour histogram of the crop, what a model has
to beat.

Usage:
    uv run python scripts/appearance_bench.py
    uv run python scripts/appearance_bench.py --backend siglip2_base --json siglip.json
    uv run python scripts/appearance_bench.py --compare siglip.json
    uv run python scripts/appearance_bench.py --full-size --vectors ~/appearance-vectors
    uv run python scripts/appearance_bench.py --window 12 --burst 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import photo_dataset  # noqa: E402
from face_bench import SIZE_BUCKETS, size_bucket  # noqa: E402

LOCAL_BACKENDS = ("clip_vit_b32", "siglip2_base", "jina_clip_v1")
HISTOGRAM = "histogram"

# The face pipeline keeps 77-89% of the heads from 48 px up and 9% of those
# under (face bench, 500 px photos): from there a person counts as named by
# their face.
ANCHOR_MIN_HEAD = 48
# PIPA's body box is three heads wide and six tall, head at the top; the
# torso is its next three heads.
TORSO_WIDTH = 3
TORSO_HEIGHT = 3
# A torso mostly out of the frame (a close-up portrait) shows no clothing:
# a third of it is the shoulders and the chest.
MIN_VISIBLE = 1 / 3
MIN_SIDE = 8
TARGETS = (0.90, 0.95, 0.98)
# The target the by-size table is read at.
SIZE_TARGET = 0.95
_THRESHOLDS = np.round(np.arange(0.0, 1.0005, 0.005), 3)
_MARGINS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08)
# A point naming fewer people than this is fitted on chance.
_MIN_NAMED = 20


class BenchError(RuntimeError):
    pass


# -- the torsos --------------------------------------------------------------


@dataclass(frozen=True)
class Torso:
    photo_id: int
    library: str
    album: str
    taken: datetime | None
    identity: str
    # The head's height, px.
    head: float
    # (left, top, right, bottom), px of the image.
    box: tuple[int, int, int, int]


def torso_box(head, width, height):
    """The torso under *head* (x, y, w, h) in a *width* x *height* image, as
    (left, top, right, bottom), or None when too little of it is in the
    frame."""
    x, y, w, h = head
    centre = x + w / 2
    left, right = centre - TORSO_WIDTH * w / 2, centre + TORSO_WIDTH * w / 2
    top, bottom = y + h, y + h + TORSO_HEIGHT * h
    full = (right - left) * (bottom - top)
    left, top = max(0.0, left), max(0.0, top)
    right, bottom = min(float(width), right), min(float(height), bottom)
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        return None
    if (right - left) * (bottom - top) < MIN_VISIBLE * full:
        return None
    return round(left), round(top), round(right), round(bottom)


def torsos_of(photos):
    """The torsos of *photos*' annotated people, photo by photo."""
    out = []
    for photo in photos:
        taken = datetime.fromisoformat(photo.taken) if photo.taken else None
        for face in photo.faces:
            box = torso_box(face.box, photo.width, photo.height)
            if box is not None:
                out.append(
                    Torso(
                        photo_id=photo.photo_id,
                        library=photo.library,
                        album=photo.album,
                        taken=taken,
                        identity=face.identity,
                        head=face.box[3],
                        box=box,
                    )
                )
    return out


def named_by_face(photos):
    """{photo id: the people its faces name}."""
    return {
        photo.photo_id: {
            face.identity for face in photo.faces if face.box[3] >= ANCHOR_MIN_HEAD
        }
        for photo in photos
    }


# -- embedding ---------------------------------------------------------------


def colour_histogram(image):
    """A 12 x 4 x 4 HSV histogram of *image*, square-rooted: the cosine of two
    is the Bhattacharyya coefficient of their colour distributions."""
    hsv = np.asarray(image.convert("HSV").resize((48, 48)), dtype=np.int64)
    bins = (hsv[..., 0] * 12 // 256) * 16 + (hsv[..., 1] * 4 // 256) * 4
    bins += hsv[..., 2] * 4 // 256
    counts = np.bincount(bins.ravel(), minlength=12 * 4 * 4).astype(np.float64)
    return np.sqrt(counts / counts.sum())


def _setup_django(threads):
    os.environ["PHOTOS_ONNX_THREADS"] = str(threads)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
    sys.path.insert(0, str(REPO_ROOT))
    import django

    django.setup()


def _embedder(key):
    if key == HISTOGRAM:
        return colour_histogram
    from workspace.photos.services.embedding import registry

    backend = registry.get_embedding_backend(key)
    if isinstance(backend, registry.MisconfiguredBackend):
        raise BenchError(backend.health().detail)
    return backend.embed_image


def embed(key, torsos, paths):
    """(unit vectors of *torsos*, CPU ms per crop) with backend *key*."""
    embed_one = _embedder(key)
    vectors, cpu = [], 0.0
    current, image = None, None
    for index, torso in enumerate(torsos):
        if torso.photo_id != current:
            with Image.open(paths[torso.photo_id]) as img:
                image = img.convert("RGB")
            current = torso.photo_id
        crop = image.crop(torso.box)
        started = time.process_time()
        vectors.append(np.asarray(embed_one(crop), dtype=np.float64))
        cpu += time.process_time() - started
        _progress(key, index + 1, len(torsos))
    vectors = np.asarray(vectors)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    # The mean over every crop: Windows advances process_time by 15.6 ms
    # ticks, so only a sum over many converges on the real cost.
    return vectors, 1000 * cpu / len(torsos)


def _fingerprint(key, torsos):
    parts = [key] + [f"{t.photo_id}|{t.box}" for t in torsos]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def vectors_for(key, torsos, paths, folder, rendition):
    """embed(), read back from *folder* when an earlier run embedded the same
    crops with the same backend."""
    if folder is None:
        return embed(key, torsos, paths)
    path = folder / f"{key}-{rendition}.npz"
    fingerprint = _fingerprint(key, torsos)
    if path.is_file():
        with np.load(path) as saved:
            if str(saved["fingerprint"]) == fingerprint:
                print(f"{key}: vectors read back from {path}", file=sys.stderr)
                return saved["vectors"], float(saved["cpu_ms"])
    vectors, cpu_ms = embed(key, torsos, paths)
    folder.mkdir(parents=True, exist_ok=True)
    np.savez(path, vectors=vectors, cpu_ms=cpu_ms, fingerprint=fingerprint)
    return vectors, cpu_ms


def _progress(label, done, total):
    if sys.stderr.isatty() or done == total:
        print(
            f"\r{label} {done}/{total}",
            end="\n" if done == total else "",
            file=sys.stderr,
        )


# -- scoring -----------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """What the nearest anchor says about one person."""

    # The library's rank, which half of the bench it is in.
    library: int
    bucket: str
    nearest: float
    # How much further the nearest anchor of anyone else is.
    margin: float
    right: bool
    anchored: bool
    majority_right: bool


def answers(torsos, vectors, named, libraries, *, window, burst):
    """An Answer for each torso with an anchor around it."""
    anchors = defaultdict(list)
    for index, torso in enumerate(torsos):
        if torso.head >= ANCHOR_MIN_HEAD and torso.taken is not None:
            # PIPA's ids only tell two people apart within one album.
            anchors[torso.library, torso.album].append(index)
    out = []
    for index, query in enumerate(torsos):
        if query.taken is None:
            continue
        # A person is in a photo once: those its faces name are not them.
        others_named = named[query.photo_id] - {query.identity}
        candidates = [
            j
            for j in anchors[query.library, query.album]
            if torsos[j].photo_id != query.photo_id
            and burst <= abs(torsos[j].taken - query.taken) <= window
            and torsos[j].identity not in others_named
        ]
        if not candidates:
            continue
        distances = 1 - vectors[candidates] @ vectors[index]
        identities = [torsos[j].identity for j in candidates]
        order = np.argsort(distances, kind="stable")
        best = identities[order[0]]
        runner_up = next((distances[k] for k in order if identities[k] != best), None)
        out.append(
            Answer(
                library=libraries[query.library],
                bucket=size_bucket(query.head),
                nearest=float(distances[order[0]]),
                margin=2.0
                if runner_up is None
                else float(runner_up - distances[order[0]]),
                right=best == query.identity,
                anchored=query.identity in identities,
                majority_right=Counter(identities).most_common(1)[0][0]
                == query.identity,
            )
        )
    return out


def _arrays(answers):
    return (
        np.array([a.nearest for a in answers]),
        np.array([a.margin for a in answers]),
        np.array([a.right for a in answers], dtype=bool),
    )


def accepted_at(answers, threshold, margin):
    nearest, margins, _right = _arrays(answers)
    return (nearest <= threshold) & (margins >= margin)


def fit(answers, target):
    """The (threshold, margin) naming the most of *answers* at *target*
    precision, or None when no point reaches it."""
    nearest, margins, right = _arrays(answers)
    best = None
    for margin in _MARGINS:
        clear = margins >= margin
        for threshold in _THRESHOLDS:
            accepted = clear & (nearest <= threshold)
            named = int(accepted.sum())
            if named < _MIN_NAMED:
                continue
            if right[accepted].sum() / named >= target and (
                best is None or named > best[0]
            ):
                best = (named, float(threshold), margin)
    return None if best is None else best[1:]


def held_out(answers, target):
    """Which of *answers* are named, each half of the libraries at the point
    fitted on the other half."""
    accepted = np.zeros(len(answers), dtype=bool)
    sides = np.array([a.library % 2 for a in answers])
    for side in (0, 1):
        fitted = fit(
            [a for a, s in zip(answers, sides, strict=True) if s != side], target
        )
        if fitted is None:
            continue
        scored = np.flatnonzero(sides == side)
        accepted[scored] = accepted_at([answers[i] for i in scored], *fitted)
    return accepted


def score(answers):
    """The report of one backend's answers."""
    right = np.array([a.right for a in answers], dtype=bool)
    anchored = np.array([a.anchored for a in answers], dtype=bool)
    majority = np.array([a.majority_right for a in answers], dtype=bool)
    buckets = np.array([a.bucket for a in answers])
    report = {
        "torsos": len(answers),
        "anchored": _share(anchored),
        "nearest_right": _share(right[anchored]),
        "majority_right": _share(majority[anchored]),
        "named": {},
    }
    at_size_target = None
    for target in TARGETS:
        accepted = held_out(answers, target)
        report["named"][f"{target:.2f}"] = {
            "named": _share(accepted),
            "count": int(accepted.sum()),
            "precision": _share(right[accepted]),
            "fitted": fit(answers, target),
        }
        if target == SIZE_TARGET:
            at_size_target = accepted
    report["by_size"] = {}
    for bucket in (size_bucket(low) for low in SIZE_BUCKETS):
        here = buckets == bucket
        report["by_size"][bucket] = {
            "torsos": int(here.sum()),
            "anchored": _share(anchored[here]),
            "nearest_right": _share(right[here & anchored]),
            "named": _share(at_size_target[here]),
            "precision": _share(right[here & at_size_target]),
        }
    return report


def _share(flags):
    return float(flags.mean()) if len(flags) else None


# -- report ------------------------------------------------------------------


def _rows():
    rows = [
        ("Torsos with a candidate", ("torsos",), "int"),
        ("Anchored", ("anchored",), "pct"),
        ("Nearest anchor right", ("nearest_right",), "pct"),
        ("  most photographed instead", ("majority_right",), "pct"),
    ]
    for target in TARGETS:
        key = f"{target:.2f}"
        rows += [
            (f"Named at {target:.0%} precision", ("named", key, "named"), "pct"),
            ("  torsos named", ("named", key, "count"), "int"),
            ("  precision held out", ("named", key, "precision"), "pct"),
            ("  threshold / margin", ("named", key, "fitted"), "point"),
        ]
    rows.append(("CPU ms / crop", ("cpu_ms_per_crop",), "ms"))
    return rows


def render(report, baseline=None):
    keys = list(report["backends"])
    width = max(14, *(len(key) + 2 for key in keys))
    if baseline is not None:
        # Room for the " (+12.3 pt)" after each value.
        width += 11
    header = (
        f"{report['torsos']} torsos in {report['photos']} photos at "
        f"{report['rendition']} px, {report['anchors']} of them anchors; "
        f"candidates within {report['window_hours']:g} h, at least "
        f"{report['burst_minutes']:g} min apart"
    )
    lines = [header, ""]
    if baseline is not None and any(
        baseline.get(name) != report[name]
        for name in ("rendition", "torsos", "window_hours", "burst_minutes")
    ):
        lines[-1:] = [
            "The earlier run saw other photos or candidates: they do not compare.",
            "",
        ]
    lines.append(f"{'':<30}" + "".join(f"{key:>{width}}" for key in keys))
    for label, path, kind in _rows():
        cells = []
        for key in keys:
            value = _lookup(report["backends"][key], path)
            cell = _format(value, kind)
            before = (
                _lookup(baseline.get("backends", {}).get(key, {}), path)
                if baseline
                else None
            )
            if kind != "point" and _format(before, kind) not in ("-", cell):
                cell += f" ({_delta(value, before, kind)})"
            cells.append(f"{cell:>{width}}")
        lines.append(f"{label:<30}" + "".join(cells))
    lines += [
        "",
        f"By head size, at {SIZE_TARGET:.0%} precision: named (precision held out)",
        f"{'':<12}{'torsos':>8}{'anchored':>10}"
        + "".join(f"{key:>{width + 4}}" for key in keys),
    ]
    first = report["backends"][keys[0]]["by_size"]
    for bucket, row in first.items():
        cells = []
        for key in keys:
            cell = report["backends"][key]["by_size"][bucket]
            cells.append(
                f"{_format(cell['named'], 'pct')} ({_format(cell['precision'], 'pct')})"
            )
        lines.append(
            f"{bucket:<12}{row['torsos']:>8}{_format(row['anchored'], 'pct'):>10}"
            + "".join(f"{cell:>{width + 4}}" for cell in cells)
        )
    return "\n".join(lines)


def _lookup(report, path):
    value = report
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _format(value, kind):
    if value is None:
        return "-"
    if kind == "pct":
        return f"{100 * value:.1f}%"
    if kind == "int":
        return str(value)
    if kind == "point":
        return f"{value[0]:.3f} / {value[1]:.2f}"
    return f"{value:.0f}"


def _delta(value, before, kind):
    # From the rounded values, so the difference is the one between the two
    # numbers shown.
    if kind == "pct":
        return f"{round(100 * value, 1) - round(100 * before, 1):+.1f} pt"
    return f"{round(value) - round(before):+d}"


# -- running -----------------------------------------------------------------


def _git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return ""


def load(args, cache_dir):
    """(photos with their faces, {photo id: path}, {library: rank})."""
    libraries = photo_dataset.by_library(photo_dataset.load_manifest())
    chosen = dict(list(libraries.items())[: args.libraries])
    photos = [photo for members in chosen.values() for photo in members]
    print(f"Fetching {len(photos)} photos into {cache_dir} ...", flush=True)
    annotations = photo_dataset.read_annotations(
        photo_dataset.fetch_annotations(cache_dir)
    )
    if args.full_size:
        large, paths = photo_dataset.fetch_large_images(photos, cache_dir)
        print(f"Flickr no longer has {len(photos) - len(large)} of them.", flush=True)
        photos = large
    else:
        paths = photo_dataset.fetch_images(photos, cache_dir)
    photos = photo_dataset.with_faces(photos, annotations)
    return photos, paths, {library: rank for rank, library in enumerate(chosen)}


def main():
    parser = argparse.ArgumentParser(
        description="Measure how well clothing names people within an event.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        help=f"PHOTOS_EMBEDDING_BACKEND to run, or {HISTOGRAM} (repeatable; "
        f"default: {', '.join((*LOCAL_BACKENDS, HISTOGRAM))})",
    )
    parser.add_argument(
        "--window", type=float, default=6, help="hours around a photo to look in"
    )
    parser.add_argument(
        "--burst",
        type=float,
        default=5,
        help="minutes within which another photo is the same moment, left out",
    )
    parser.add_argument(
        "--libraries",
        type=int,
        default=1000,
        help="run the N largest libraries of the dataset",
    )
    parser.add_argument(
        "--full-size",
        action="store_true",
        help="run on Flickr's 1024 px copies instead of the 500 px ones, leaving "
        "out the photos Flickr no longer has",
    )
    parser.add_argument("--onnx-threads", type=int, default=1)
    parser.add_argument(
        "--vectors",
        type=Path,
        help="keep each backend's vectors in this folder, and read them back "
        "on a later run over the same crops",
    )
    parser.add_argument("--json", type=Path, help="write the report to this file")
    parser.add_argument("--compare", type=Path, help="an earlier --json report")
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="photos (default: PHOTO_DATASET_CACHE or ~/.cache/workspace/photo-dataset)",
    )
    args = parser.parse_args()
    keys = args.backend or [*LOCAL_BACKENDS, HISTOGRAM]

    cache_dir = (args.cache or photo_dataset.default_cache_dir()).resolve()
    baseline = (
        json.loads(args.compare.read_text(encoding="utf-8")) if args.compare else None
    )
    photos, paths, libraries = load(args, cache_dir)
    torsos = torsos_of(photos)
    named = named_by_face(photos)
    rendition = photo_dataset.LARGE_SIZE if args.full_size else 500
    if any(key != HISTOGRAM for key in keys):
        _setup_django(args.onnx_threads)

    report = {
        "rendition": rendition,
        "photos": len(photos),
        "torsos": len(torsos),
        "anchors": sum(torso.head >= ANCHOR_MIN_HEAD for torso in torsos),
        "window_hours": args.window,
        "burst_minutes": args.burst,
        "commit": _git_commit(),
        "date": datetime.now().isoformat(timespec="seconds"),
        "backends": {},
    }
    try:
        for key in keys:
            vectors, cpu_ms = vectors_for(key, torsos, paths, args.vectors, rendition)
            found = answers(
                torsos,
                vectors,
                named,
                libraries,
                window=timedelta(hours=args.window),
                burst=timedelta(minutes=args.burst),
            )
            report["backends"][key] = {
                **score(found),
                "dims": int(vectors.shape[1]),
                "cpu_ms_per_crop": cpu_ms,
            }
    except BenchError as exc:
        sys.exit(f"Bench aborted: {exc}")

    print()
    print(render(report, baseline))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nReport written to {args.json}")


if __name__ == "__main__":
    main()
