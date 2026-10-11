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


def score(vectors, photos, queries):
    """The report of one backend's run, from what it embedded."""
    wanted = [frozenset(q.labels) for q in queries]
    relevant = np.array([[w <= p.labels for p in photos] for w in wanted])
    query_labels = frozenset().union(*wanted)
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
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        # PROCESS_MEMORY_COUNTERS: the call refuses a shorter buffer.
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
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
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
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
    total = 0
    for encoder in {backend.image_encoder.repo, backend.text_encoder.repo}:
        repo = Path(local.cache_dir()) / f"models--{encoder.replace('/', '--')}"
        total += sum(f.stat().st_size for f in repo.rglob("*") if f.is_file())
    return total / 1e6


def _load_image(path, size):
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((size, size), Image.LANCZOS)
        return img


def embed(key, photos, paths, queries, duplicates):
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

    return {
        "images": np.asarray(images, dtype=np.float64),
        "copies": np.asarray(copies, dtype=np.float64),
        "english": np.asarray(english, dtype=np.float64),
        "french": np.asarray(french, dtype=np.float64),
        "thresholds": {
            "similar": registry.similar_max_distance(),
            "duplicate": registry.duplicate_max_distance(),
            "description": registry.description_max_distance(),
        },
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
    run = embed(args.worker, photos, paths, queries, min(args.duplicates, len(photos)))
    meta = {k: run.pop(k) for k in ("thresholds", "cost", "label", "dims")}
    np.savez(args.out, **run)
    Path(args.out).with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")


def run_backend(key, args):
    """Run backend *key* in a process of its own; its report."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "vectors.npz"
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
        loaded = dict(np.load(out))
        meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    photos = embedding_dataset.load(args.cache_dir, limit=args.images)
    queries = embedding_dataset.usable_queries(photos)
    report = score(loaded | meta, photos, queries)
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
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        _worker(args)
        return 0

    photos = embedding_dataset.load(args.cache_dir, limit=args.images)
    embedding_dataset.fetch_images(
        photos,
        args.cache_dir,
        progress=lambda done, total: _progress("photos", done, total),
    )
    queries = embedding_dataset.usable_queries(photos)
    print(
        f"{len(photos)} photos, {len(queries)} queries, "
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
