#!/usr/bin/env python
"""Measure the photo embedding models on photos people search their library for.

Runs each backend exactly as a worker runs it - one photo at a time, at the
size the library decodes it, through ``EmbeddingBackend.embed_image`` and
``embed_text`` - over the Open Images photos of scripts/embedding_dataset.py,
and scores what comes out against what people verified the photos show:

- **Search by description**: for each query ("dog", "snowy mountain"...),
  the average precision of the ranking, its precision in the first ten, in
  English and in French. Then what the backend's description threshold
  returns - its precision, its recall, how many photos - and the threshold
  that would have done best.
- **Near-duplicates**: each of DUPLICATES photos re-encoded, halved, cropped,
  brightened and tilted, as a burst or a re-export would; what the duplicate
  threshold finds of these copies and how many other photos it mistakes for
  one, and the loosest threshold that keeps 99% of what it finds right.
- **Similar photos**: each photo against the others, a photo showing one of
  the same things being a right answer; precision of what the similar
  threshold returns, and the threshold that would have done best.
- **Automatic tags**: each of the app's classic tags placed on every photo
  under a threshold from its name: one shared, or one per tag, fitted on half
  the photos and scored on the other half; the backend's own table; and the
  table to ship, fitted on every photo. Then tags learnt from the mean of 3,
  5 or 10 photos tagged by hand.
- **Cost**: time to load the models, CPU time per photo and per query (one
  thread unless PHOTOS_ONNX_THREADS says otherwise), the weights on disk and
  the memory the process grew to.

Every backend runs in a process of its own, so the memory each needs is
measured alone; the weights are downloaded once into PHOTOS_MODEL_DIR, the
photos once into the dataset cache.

Usage:
    uv run python scripts/embedding_bench.py
    uv run python scripts/embedding_bench.py --backend siglip2_base --json siglip.json
    uv run python scripts/embedding_bench.py --compare siglip.json
    uv run python scripts/embedding_bench.py --images 300 --duplicates 50
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import embedding_dataset  # noqa: E402

LOCAL_BACKENDS = ("clip_vit_b32", "siglip2_base", "jina_clip_v1")
DUPLICATES = 200
# What a near-duplicate threshold must keep right, at the least.
DUPLICATE_PRECISION = 0.99
# What an automatic tag must keep right, at the least: it shows on the photo
# alone, with no ranking to bury a mistake under better answers.
TAG_PRECISION = 0.9
# What a tag's own threshold is fitted for: tighter than TAG_PRECISION, since
# 30 photos a tag are few, and a threshold fitted on them keeps less on the
# photos it never saw. And the average precision a tag's ranking needs to be
# placed at all: under it, no threshold separates the tag from the rest.
TAG_FIT_PRECISION = 0.97
TAG_MIN_AP = 0.75
# How many photos a user tagged by hand, in the tags learnt from them.
EXAMPLES = (3, 5, 10)
# Distances the thresholds are searched over: cosine distance, 0 to 2.
_GRID = np.round(np.arange(0.0, 2.0005, 0.005), 3)


class BenchError(RuntimeError):
    pass


# -- variants ----------------------------------------------------------------


def _jpeg(image, quality):
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def _crop(image, share):
    width, height = image.size
    dx, dy = round(width * (1 - share) / 2), round(height * (1 - share) / 2)
    return image.crop((dx, dy, width - dx, height - dy))


def _tilt(image, degrees):
    tilted = image.rotate(degrees, resample=Image.BICUBIC, expand=False)
    # Cut the corners the rotation filled.
    return _crop(tilted, 0.92)


VARIANTS = (
    ("re-encoded", lambda image: _jpeg(image, 40)),
    ("halved", lambda image: image.resize((image.width // 2, image.height // 2))),
    ("cropped", lambda image: _crop(image, 0.85)),
    ("brighter", lambda image: ImageEnhance.Brightness(image).enhance(1.25)),
    ("tilted", lambda image: _tilt(image, 3)),
)


def variants(image):
    """The near-duplicates of *image* a library ends up with, in VARIANTS order."""
    return [make(image) for _name, make in VARIANTS]


# -- scoring -----------------------------------------------------------------


def unit(vectors):
    vectors = np.asarray(vectors, dtype=np.float64)
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


def distances(queries, items):
    """Cosine distances, [query][item], as the vector index computes them."""
    return 1.0 - unit(queries) @ unit(items).T


def average_precision(row, relevant):
    """Average precision of the ranking of *row* (distances, nearest first)
    for the boolean *relevant*. 0 when nothing is relevant."""
    relevant = np.asarray(relevant, dtype=bool)
    if not relevant.any():
        return 0.0
    hits = relevant[np.argsort(row, kind="stable")]
    precision_at = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float(precision_at[hits].mean())


def precision_at(row, relevant, k=10):
    order = np.argsort(row, kind="stable")[:k]
    return float(np.asarray(relevant, dtype=bool)[order].mean())


def at_threshold(matrix, relevant, threshold):
    """What a threshold returns, averaged over the rows of *matrix*:
    precision (over the rows returning anything), recall, results per row."""
    returned = matrix <= threshold
    counts = returned.sum(axis=1)
    hits = (returned & relevant).sum(axis=1)
    wanted = relevant.sum(axis=1)
    answering = counts > 0
    return {
        "threshold": float(threshold),
        "precision": float((hits[answering] / counts[answering]).mean())
        if answering.any()
        else 0.0,
        "recall": float((hits / np.maximum(wanted, 1)).mean()),
        "results": float(counts.mean()),
    }


def best_threshold(matrix, relevant):
    """The threshold of _GRID with the best mean F1 over the rows."""
    best = None
    for threshold in _GRID:
        scores = at_threshold(matrix, relevant, threshold)
        p, r = scores["precision"], scores["recall"]
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        if best is None or f1 > best["f1"]:
            best = scores | {"f1": f1}
    return best


def ranking(matrix, relevant):
    return {
        "map": float(
            np.mean(
                [average_precision(r, m) for r, m in zip(matrix, relevant, strict=True)]
            )
        ),
        "p10": float(
            np.mean([precision_at(r, m) for r, m in zip(matrix, relevant, strict=True)])
        ),
    }


def score_search(text_vectors, image_vectors, relevant, threshold):
    matrix = distances(text_vectors, image_vectors)
    return ranking(matrix, relevant) | {
        "at_threshold": at_threshold(matrix, relevant, threshold),
        "suggested": best_threshold(matrix, relevant),
    }


def score_duplicates(images, copies, threshold):
    """*copies* (n x v x d) of the first n of *images* (m x d), the library.

    Each of those photos' right answers are its own copies; every other
    photo of the library, and every other photo's copies, is a mistake.
    """
    n, v, _ = copies.shape
    candidates = np.concatenate([copies.reshape(n * v, -1), images])
    matrix = distances(images[:n], candidates)
    relevant = np.zeros(matrix.shape, dtype=bool)
    for i in range(n):
        relevant[i, i * v : (i + 1) * v] = True
        # A photo is not a copy of itself.
        matrix[i, n * v + i] = np.inf
    # Loosened step by step until the first mistake that costs the precision.
    loosest = None
    for t in _GRID:
        returned = matrix <= t
        found = returned.sum()
        if not found:
            continue
        if (returned & relevant).sum() / found < DUPLICATE_PRECISION:
            break
        loosest = t
    variant_recall = {
        name: float((matrix[np.arange(n), np.arange(n) * v + j] <= threshold).mean())
        for j, (name, _make) in enumerate(VARIANTS[:v])
    }
    others = matrix[~relevant]
    return {
        "at_threshold": at_threshold(matrix, relevant, threshold),
        "by_variant": variant_recall,
        "suggested": (
            at_threshold(matrix, relevant, loosest) if loosest is not None else None
        ),
        # How close two photos that are not copies of each other come.
        "closest_other": float(others[np.isfinite(others)].min()),
    }


def score_similar(image_vectors, labels, threshold):
    """Each photo showing one of the queries' labels against all the others:
    a right answer shows one of the same."""
    vectors = np.asarray(image_vectors)
    rows = [i for i, own in enumerate(labels) if own]
    matrix = distances(vectors[rows], vectors)
    relevant = np.array([[bool(labels[i] & other) for other in labels] for i in rows])
    for row, i in enumerate(rows):
        # A photo is not an answer to itself.
        matrix[row, i] = np.inf
        relevant[row, i] = False
    return ranking(matrix, relevant) | {
        "at_threshold": at_threshold(matrix, relevant, threshold),
        "suggested": best_threshold(matrix, relevant),
    }


def loosest_threshold(row, relevant, target):
    """The loosest threshold on the distances *row* whose returns keep
    *target* precision, two right ones at the least; nan when none does."""
    order = np.argsort(row, kind="stable")
    hits = np.cumsum(np.asarray(relevant, dtype=bool)[order])
    precision = hits / np.arange(1, len(hits) + 1)
    keeping = np.flatnonzero((precision >= target) & (hits >= 2))
    return float(row[order][keeping[-1]]) if len(keeping) else np.nan


def tag_thresholds(matrix, relevant, *, per_label):
    """One threshold per label (row), nan for a label never tagged: each
    label's own (calibrate), or the one threshold all of them share."""
    if per_label:
        return calibrate(matrix, relevant)
    shared = loosest_threshold(matrix.ravel(), relevant.ravel(), TAG_PRECISION)
    return np.full(len(matrix), shared)


def calibrate(matrix, relevant):
    """Each tag's threshold, as a backend ships it: its loosest at
    TAG_FIT_PRECISION, nan for a tag whose ranking is under TAG_MIN_AP or
    that cannot reach it."""
    thresholds = []
    for row, wanted in zip(matrix, relevant, strict=True):
        if average_precision(row, wanted) < TAG_MIN_AP:
            thresholds.append(np.nan)
        else:
            thresholds.append(loosest_threshold(row, wanted, TAG_FIT_PRECISION))
    return np.array(thresholds)


def tag_scores(tagged, relevant):
    """Precision over every tag placed (what a user sees on the photos),
    recall averaged over the labels, tags per photo."""
    placed = tagged.sum()
    right = tagged & relevant
    return {
        "precision": float(right.sum() / placed) if placed else 0.0,
        "recall": float(
            np.mean(right.sum(axis=1) / np.maximum(relevant.sum(axis=1), 1))
        ),
        "per_photo": float(placed / tagged.shape[1]),
    }


def held_out_tags(matrix, relevant, *, per_label):
    """Each half of the photos tagged with thresholds fitted on the other
    half: a threshold fitted on the photos it is scored on flatters itself,
    the more so with 30 photos a label."""
    even = np.arange(matrix.shape[1]) % 2 == 0
    tagged = np.zeros(matrix.shape, dtype=bool)
    kept = []
    for fit, applied in ((even, ~even), (~even, even)):
        thresholds = tag_thresholds(
            matrix[:, fit], relevant[:, fit], per_label=per_label
        )
        # A nan threshold tags nothing: no distance compares under it.
        tagged[:, applied] = matrix[:, applied] <= thresholds[:, None]
        kept.append(np.count_nonzero(~np.isnan(thresholds)))
    scores = tag_scores(tagged, relevant) | {
        "labels": float(np.mean(kept)),
        "of": len(matrix),
    }
    if not per_label:
        # Fitted on every photo: what a backend would ship.
        scores["threshold"] = float(
            tag_thresholds(matrix, relevant, per_label=False)[0]
        )
    return scores


def example_distances(images, relevant, count):
    """Distances from every photo to the mean of the first *count* photos of
    each label, standing for the photos a user tagged by hand; and the right
    answers left once those examples are out of the scoring."""
    units = unit(images)
    matrix = np.empty(relevant.shape)
    remaining = relevant.copy()
    for row, wanted in enumerate(relevant):
        examples = np.flatnonzero(wanted)[:count]
        matrix[row] = 1.0 - units @ unit(units[examples].mean(axis=0))
        matrix[row, examples] = np.inf
        remaining[row, examples] = False
    return matrix, remaining


def score_tagging(tag_vectors, images, relevant, names, thresholds):
    """The classic tags (*names*) placed on every photo under a threshold
    from their name: the description threshold, the one threshold keeping
    TAG_PRECISION, one fitted per tag (calibrate), and the backend's own
    table. Then tags learnt from the mean of a few photos tagged by hand,
    for want of a better way to learn the owner's own tags."""
    matrix = distances(tag_vectors, images)
    table = thresholds["tags"]
    shipped = np.array([table.get(name, np.nan) for name in names])
    fitted = calibrate(matrix, relevant)
    report = {
        "description": tag_scores(matrix <= thresholds["description"], relevant)
        | {"threshold": float(thresholds["description"])},
        "shared": held_out_tags(matrix, relevant, per_label=False),
        "per_label": held_out_tags(matrix, relevant, per_label=True),
        # Scored on the photos the table was fitted on: flattered, as the
        # held-out row above tells.
        "at_threshold": tag_scores(matrix <= shipped[:, None], relevant)
        | {"labels": float(np.count_nonzero(~np.isnan(shipped))), "of": len(names)},
        "suggested": {
            name: round(float(t), 3)
            for name, t in zip(names, fitted, strict=True)
            if not np.isnan(t)
        },
        "by_tag": {
            name: {"ap": average_precision(row, wanted), "photos": int(wanted.sum())}
            for name, row, wanted in zip(names, matrix, relevant, strict=True)
        },
    }
    for count in EXAMPLES:
        learnt, remaining = example_distances(images, relevant, count)
        report[f"examples_{count}"] = held_out_tags(learnt, remaining, per_label=False)
    return report


def score(vectors, photos, queries, tags):
    """The report of one backend's run, from what it embedded."""
    wanted = [frozenset(q.labels) for q in queries]
    relevant = np.array([[w <= p.labels for p in photos] for w in wanted])
    query_labels = frozenset().union(*wanted)
    tagged = np.array([[set(t.labels) <= p.labels for p in photos] for t in tags])
    thresholds = vectors["thresholds"]
    images = vectors["images"]
    english = score_search(
        vectors["english"], images, relevant, thresholds["description"]
    )
    french = ranking(distances(vectors["french"], images), relevant)
    return {
        "search": english | {"french": french},
        "duplicates": score_duplicates(
            images, vectors["copies"], thresholds["duplicate"]
        ),
        "similar": score_similar(
            images, [p.labels & query_labels for p in photos], thresholds["similar"]
        ),
        "tagging": score_tagging(
            vectors["tags"], images, tagged, [t.text for t in tags], thresholds
        ),
    }


# -- running a backend -------------------------------------------------------


def _maxrss_mb():
    if sys.platform == "win32":
        return _peak_working_set() / (1024 * 1024)
    import resource

    # Kilobytes on Linux, bytes on macOS.
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def _peak_working_set():
    """Windows' ru_maxrss, in bytes: the module resource does not exist there."""
    import ctypes.wintypes

    class Counters(ctypes.Structure):
        # PROCESS_MEMORY_COUNTERS: the call refuses a shorter buffer.
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("PageFaultCount", ctypes.wintypes.DWORD),
            *(
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ),
        ]

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(Counters),
        ctypes.wintypes.DWORD,
    )
    counters = Counters(cb=ctypes.sizeof(Counters))
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize


def _weights_mb(backend):
    from workspace.photos.services.embedding import local

    if not isinstance(backend, local.LocalBackend):
        return 0.0
    files = set()
    for encoder in {backend.image_encoder.repo, backend.text_encoder.repo}:
        repo = Path(local.cache_dir()) / f"models--{encoder.replace('/', '--')}"
        # Resolved: the Hub's snapshots/ are links into blobs/, the same file
        # twice.
        files |= {f.resolve() for f in repo.rglob("*") if f.is_file()}
    return sum(f.stat().st_size for f in files) / 1e6


def _load_image(path, size):
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((size, size), Image.LANCZOS)
        return img


def embed(key, photos, paths, queries, duplicates, tags=()):
    """Run backend *key* over the bench, in this process: its vectors, its
    thresholds and what it cost."""
    os.environ["PHOTOS_EMBEDDING_BACKEND"] = key
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
    sys.path.insert(0, str(REPO_ROOT))
    import django

    django.setup()
    from workspace.photos.services.embedding import registry

    backend = registry.get_embedding_backend()
    if isinstance(backend, registry.MisconfiguredBackend):
        raise BenchError(backend.health().detail)
    baseline = _maxrss_mb()
    started = time.perf_counter()
    backend.prepare()
    load_s = time.perf_counter() - started

    images, image_ms, copies = [], [], []
    for index, photo in enumerate(photos):
        image = _load_image(paths[photo.image_id], backend.decode_size)
        started = time.process_time()
        images.append(backend.embed_image(image))
        image_ms.append((time.process_time() - started) * 1000)
        if index < duplicates:
            copies.append([backend.embed_image(copy) for copy in variants(image)])
        _progress(f"{key}: photos", index + 1, len(photos))

    english, text_ms = [], []
    for query in queries:
        started = time.process_time()
        english.append(backend.embed_text(query.text))
        text_ms.append((time.process_time() - started) * 1000)
    french = [backend.embed_text(query.french) for query in queries]
    tag_vectors = [backend.embed_text(tag.text) for tag in tags]

    return {
        "images": np.asarray(images, dtype=np.float64),
        "copies": np.asarray(copies, dtype=np.float64),
        "english": np.asarray(english, dtype=np.float64),
        "french": np.asarray(french, dtype=np.float64),
        "tags": np.asarray(tag_vectors, dtype=np.float64),
        "thresholds": _thresholds(registry),
        "cost": {
            "load_s": load_s,
            # The mean, not the median: Windows advances process_time by
            # 15.6 ms ticks, so each sample is 0 or a tick, and only their
            # mean converges on the real cost.
            "image_ms": float(np.mean(image_ms)),
            "text_ms": float(np.mean(text_ms)),
            "weights_mb": _weights_mb(backend),
            "memory_mb": _maxrss_mb() - baseline,
        },
        "label": getattr(backend, "label", "") or key,
        "dims": int(len(images[0])),
    }


def _progress(label, done, total):
    if sys.stderr.isatty() or done == total:
        print(
            f"\r{label} {done}/{total}",
            end="\n" if done == total else "",
            file=sys.stderr,
        )


def _worker(args):
    """The --worker side: embed, then hand the vectors back through a file."""
    photos = embedding_dataset.load(args.cache_dir, limit=args.images)
    # Already downloaded by the parent: this only resolves their paths.
    paths = embedding_dataset.fetch_images(photos, args.cache_dir)
    queries = embedding_dataset.usable_queries(photos)
    tags = embedding_dataset.usable_queries(photos, embedding_dataset.TAGS)
    duplicates = min(args.duplicates, len(photos))
    run = embed(args.worker, photos, paths, queries, duplicates, tags)
    meta = {k: run.pop(k) for k in ("thresholds", "cost", "label", "dims")}
    np.savez(args.out, **run)
    Path(args.out).with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")


def _fingerprint(photos, queries, tags, duplicates):
    """What a run's vectors depend on, the model aside."""
    import hashlib

    parts = [p.image_id for p in photos]
    parts += [f"{q.text}|{q.french}" for q in queries]
    parts += [t.text for t in tags]
    parts.append(str(duplicates))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _current_thresholds(key):
    """The backend's thresholds as the app reads them now, in a process of
    its own: they may have moved since the cached vectors were embedded."""
    command = [sys.executable, str(Path(__file__).resolve()), "--thresholds", key]
    result = subprocess.run(
        command, cwd=REPO_ROOT, check=False, capture_output=True, text=True
    )
    if result.returncode:
        raise BenchError(f"{key}: cannot read its thresholds: {result.stderr}")
    return json.loads(result.stdout)


def _thresholds_worker(key):
    os.environ["PHOTOS_EMBEDDING_BACKEND"] = key
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
    sys.path.insert(0, str(REPO_ROOT))
    import django

    django.setup()
    from workspace.photos.services.embedding import registry

    print(json.dumps(_thresholds(registry)))


def _thresholds(registry):
    return {
        "similar": registry.similar_max_distance(),
        "duplicate": registry.duplicate_max_distance(),
        "description": registry.description_max_distance(),
        "tags": registry.tag_thresholds(),
    }


def _embed_in_worker(key, args, out):
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        key,
        "--out",
        str(out),
        "--duplicates",
        str(args.duplicates),
    ]
    if args.images:
        command += ["--images", str(args.images)]
    if args.cache_dir:
        command += ["--cache-dir", str(args.cache_dir)]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if result.returncode:
        raise BenchError(f"{key}: the run failed (exit {result.returncode})")


def run_backend(key, args):
    """Run backend *key* in a process of its own; its report.

    With --vectors, the vectors of a run on the same photos, queries and tags
    are read back instead of embedded again, and scored at the thresholds
    the app reads now: a scoring change or a threshold change takes seconds.
    """
    photos = embedding_dataset.load(args.cache_dir, limit=args.images)
    queries = embedding_dataset.usable_queries(photos)
    tags = embedding_dataset.usable_queries(photos, embedding_dataset.TAGS)
    fingerprint = _fingerprint(photos, queries, tags, args.duplicates)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(args.vectors) if args.vectors else Path(tmp)
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{key}.npz"
        meta_path = out.with_suffix(".json")
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if args.vectors and out.is_file() and meta_path.is_file()
            else None
        )
        if meta is None or meta.get("fingerprint") != fingerprint:
            _embed_in_worker(key, args, out)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["fingerprint"] = fingerprint
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        else:
            meta["thresholds"] = _current_thresholds(key)
            print(f"{key}: vectors read back from {out}", file=sys.stderr)
        loaded = dict(np.load(out))
    report = score(loaded | meta, photos, queries, tags)
    return {
        "backend": key,
        "label": meta["label"],
        "dims": meta["dims"],
        "thresholds": meta["thresholds"],
        "cost": meta["cost"],
        **report,
    }


# -- rendering ---------------------------------------------------------------

ROWS = (
    ("Search by description", None, None),
    ("  mAP (English)", "search.map", "pct"),
    ("  mAP (French)", "search.french.map", "pct"),
    ("  precision in the first 10", "search.p10", "pct"),
    ("  at its threshold", "search.at_threshold", "cut"),
    ("  best threshold", "search.suggested", "cut"),
    ("Near-duplicates", None, None),
    ("  at its threshold", "duplicates.at_threshold", "cut"),
    ("  loosest at 99% precision", "duplicates.suggested", "cut"),
    ("  closest two other photos", "duplicates.closest_other", "dist"),
    ("Similar photos", None, None),
    ("  mAP", "similar.map", "pct"),
    ("  precision in the first 10", "similar.p10", "pct"),
    ("  at its threshold", "similar.at_threshold", "cut"),
    ("  best threshold", "similar.suggested", "cut"),
    ("Automatic tags", None, None),
    ("  at the description threshold", "tagging.description", "tags"),
    (f"  one threshold at {TAG_PRECISION:.0%} precision", "tagging.shared", "tags"),
    ("  one threshold per tag", "tagging.per_label", "tags"),
    ("  at its tag table (fitted here)", "tagging.at_threshold", "tags"),
    *(
        (f"  from {count} tagged photos", f"tagging.examples_{count}", "tags")
        for count in EXAMPLES
    ),
    ("Cost", None, None),
    ("  load (s)", "cost.load_s", "num"),
    ("  CPU per photo (ms)", "cost.image_ms", "num"),
    ("  CPU per query (ms)", "cost.text_ms", "num"),
    ("  weights (MB)", "cost.weights_mb", "int"),
    ("  memory (MB)", "cost.memory_mb", "int"),
)


def _lookup(report, path):
    value = report
    for part in path.split("."):
        if value is None:
            return None
        value = value.get(part)
    return value


def _format(value, kind):
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "dist":
        return f"{value:.3f}"
    if kind == "int":
        return f"{value:.0f}"
    if kind == "num":
        return f"{value:.1f}"
    if kind == "tags":
        # "P 91% R 40% (1.2/photo, 30/39 labels)", after the threshold when
        # there is a single one.
        shown = f"{value['threshold']:.3f} " if "threshold" in value else ""
        shown += (
            f"P {value['precision'] * 100:.0f}% R {value['recall'] * 100:.0f}% "
            f"({value['per_photo']:.1f}/photo"
        )
        if "labels" in value:
            shown += f", {value['labels']:.0f}/{value['of']} labels"
        return shown + ")"
    # A threshold and what it returns: "0.800 P 62% R 41% (n 12)".
    return (
        f"{value['threshold']:.3f} P {value['precision'] * 100:.0f}% "
        f"R {value['recall'] * 100:.0f}% (n {value['results']:.0f})"
    )


def render(reports):
    """The reports side by side, one column per backend."""
    headers = [f"{r['label']} ({r['dims']}-d)" for r in reports]
    cells = [
        [
            label,
            *("" if path is None else _format(_lookup(r, path), kind) for r in reports),
        ]
        for label, path, kind in ROWS
    ]
    widths = [
        max(len(row[i]) for row in [["", *headers], *cells])
        for i in range(len(headers) + 1)
    ]
    lines = [
        "  ".join(
            cell.ljust(width)
            for cell, width in zip(["", *headers], widths, strict=True)
        )
    ]
    lines.append("  ".join("-" * width for width in widths))
    lines += [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in cells
    ]
    return "\n".join(line.rstrip() for line in lines)


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--backend",
        action="append",
        help=f"Backend to measure (repeatable). Default: {', '.join(LOCAL_BACKENDS)}.",
    )
    parser.add_argument(
        "--images", type=int, help="Measure on the first N photos only."
    )
    parser.add_argument(
        "--duplicates",
        type=int,
        default=DUPLICATES,
        help=f"Photos copied for the near-duplicate test (default {DUPLICATES}).",
    )
    parser.add_argument("--json", help="Write the reports to this file.")
    parser.add_argument("--compare", help="Show the reports of this file alongside.")
    parser.add_argument("--cache-dir", type=Path, help="Where the photos are kept.")
    parser.add_argument(
        "--vectors",
        type=Path,
        help="Keep each backend's vectors here, and read them back on a run "
        "over the same photos instead of embedding again.",
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--thresholds", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        _worker(args)
        return 0
    if args.thresholds:
        _thresholds_worker(args.thresholds)
        return 0

    photos = embedding_dataset.load(args.cache_dir, limit=args.images)
    embedding_dataset.fetch_images(
        photos,
        args.cache_dir,
        progress=lambda done, total: _progress("photos", done, total),
    )
    queries = embedding_dataset.usable_queries(photos)
    tags = embedding_dataset.usable_queries(photos, embedding_dataset.TAGS)
    print(
        f"{len(photos)} photos, {len(queries)} queries, {len(tags)} tags, "
        f"{min(args.duplicates, len(photos))} photos copied",
        file=sys.stderr,
    )
    reports, failed = [], []
    for key in args.backend or LOCAL_BACKENDS:
        try:
            reports.append(run_backend(key, args))
        except BenchError as exc:
            # One model that cannot run (weights out of reach, a remote
            # endpoint down) leaves the others' results standing.
            print(exc, file=sys.stderr)
            failed.append(key)
    run = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "photos": len(photos),
        "queries": len(queries),
        "threads": os.environ.get("PHOTOS_ONNX_THREADS", "1"),
    }
    baseline = (
        json.loads(Path(args.compare).read_text(encoding="utf-8"))["reports"]
        if args.compare
        else []
    )
    shown = [*reports, *({**r, "label": f"{r['label']} [before]"} for r in baseline)]
    if shown:
        print(render(shown))
    for report in reports:
        suggested = report["tagging"]["suggested"]
        if suggested != report["thresholds"]["tags"]:
            print(f"\nTag thresholds fitted for {report['label']}:")
            print(json.dumps(suggested, indent=4))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"run": run, "reports": reports}, indent=2), encoding="utf-8"
        )
    if failed:
        print(f"Not measured: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
