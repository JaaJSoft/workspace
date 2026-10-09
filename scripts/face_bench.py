#!/usr/bin/env python
"""Measure face detection and grouping on real photo libraries.

Runs the face pipeline exactly as a worker runs it - upload, analysis photo by
photo in the order they were taken (each new face voting for a group, a
grouping run once enough faces wait), then the nightly grouping pass - over
the libraries of scripts/photo_dataset.py, and scores what comes out against
who is really in each photo:

- **Faces found**: annotated people a face was found for, by head size. The
  detector's own recall is shown next to it, before the pipeline drops the
  faces too small to keep.
- **Grouping**, on the faces found: pairwise and BCubed precision and recall,
  and what a user would have to fix by hand - groups to merge, faces to take
  out of a group that is not theirs, faces left in no group.
- **Cost**: CPU time per photo, and the size of the weights.

Every run gets its own throwaway SQLite database and media root, so the
development database is never touched. The migrated database is cached per
migration state, and the photos and weights are downloaded once into the
cache directory.

Usage:
    uv run python scripts/face_bench.py
    uv run python scripts/face_bench.py --backend scrfd_arcface --json arcface.json
    uv run python scripts/face_bench.py --compare arcface.json
    uv run python scripts/face_bench.py --setting PHOTOS_FACES_MIN_SIZE=16
    uv run python scripts/face_bench.py --libraries 3 --keep /tmp/bench

``--keep`` leaves the database and the photos behind, with a password on each
library's user, so the result can be browsed in the app (the command to start
it is printed at the end).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import photo_dataset  # noqa: E402

# Head height, in px of the image, at which each size bucket starts.
SIZE_BUCKETS = (0, 24, 48, 96)
BENCH_PASSWORD = "bench1234"

# A face box sits inside the head box PIPA drew: matched when its centre is
# in the head box and its area is between these shares of the head's.
_MIN_AREA_RATIO = 0.1
_MAX_AREA_RATIO = 1.5


class BenchError(RuntimeError):
    pass


# -- scoring -----------------------------------------------------------------


def match_faces(found, heads):
    """[(found index, head index)] for the found faces that are annotated heads.

    *found* and *heads* are (x, y, w, h) boxes in the same pixels. Each face
    and each head is used once, the best-overlapping pairs first.
    """
    candidates = []
    for i, (fx, fy, fw, fh) in enumerate(found):
        cx, cy = fx + fw / 2, fy + fh / 2
        for j, (hx, hy, hw, hh) in enumerate(heads):
            if not (hx <= cx <= hx + hw and hy <= cy <= hy + hh):
                continue
            ratio = (fw * fh) / max(hw * hh, 1e-9)
            if _MIN_AREA_RATIO <= ratio <= _MAX_AREA_RATIO:
                candidates.append((_iou(found[i], heads[j]), i, j))
    pairs, used_found, used_heads = [], set(), set()
    for _score, i, j in sorted(candidates, reverse=True):
        if i in used_found or j in used_heads:
            continue
        used_found.add(i)
        used_heads.add(j)
        pairs.append((i, j))
    return sorted(pairs)


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def size_bucket(height):
    """The label of the size bucket a head *height* px tall falls in."""
    label = None
    for low, high in zip(SIZE_BUCKETS, SIZE_BUCKETS[1:] + (None,), strict=True):
        if height >= low:
            label = f"{low}-{high}px" if high else f"{low}px+"
    return label


@dataclass
class GroupingScore:
    """How one library's faces were grouped, against who they really are.

    Counts, so libraries add up: the rates are computed from the sums.
    """

    faces: int = 0
    grouped: int = 0
    # Pairs of faces in one group, of one person, and both.
    predicted_pairs: int = 0
    true_pairs: int = 0
    correct_pairs: int = 0
    # Sums over faces of their BCubed precision and recall.
    bcubed_precision: float = 0.0
    bcubed_recall: float = 0.0
    # People with two faces or more, and those a group was made for.
    people: int = 0
    people_found: int = 0
    groups: int = 0
    # Groups a user merges into another one of the same person, groups
    # holding more than one person, and the faces to take out of them.
    merges: int = 0
    mixed_groups: int = 0
    misplaced: int = 0

    def add(self, other):
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        return self

    def rates(self):
        precision = _ratio(self.correct_pairs, self.predicted_pairs)
        recall = _ratio(self.correct_pairs, self.true_pairs)
        b_precision = _ratio(self.bcubed_precision, self.faces)
        b_recall = _ratio(self.bcubed_recall, self.faces)
        return {
            "grouped": _ratio(self.grouped, self.faces),
            "pairwise_precision": precision,
            "pairwise_recall": recall,
            "pairwise_f1": _f1(precision, recall),
            "bcubed_precision": b_precision,
            "bcubed_recall": b_recall,
            "bcubed_f1": _f1(b_precision, b_recall),
            "people_found": _ratio(self.people_found, self.people),
        }


def score_grouping(faces):
    """A GroupingScore for *faces*: [(identity, group id or None)].

    A face in no group counts as a group of its own for BCubed, which is what
    the user sees: a face nobody put with anyone.
    """
    score = GroupingScore(faces=len(faces))
    by_identity = Counter(identity for identity, _group in faces)
    members = defaultdict(Counter)
    for identity, group in faces:
        if group is not None:
            members[group][identity] += 1
    score.grouped = sum(1 for _identity, group in faces if group is not None)
    score.groups = len(members)
    score.true_pairs = sum(_pairs(n) for n in by_identity.values())
    for counts in members.values():
        size = sum(counts.values())
        score.predicted_pairs += _pairs(size)
        score.correct_pairs += sum(_pairs(n) for n in counts.values())
        for identity, n in counts.items():
            score.bcubed_precision += n * n / size
            score.bcubed_recall += n * n / by_identity[identity]
        if len(counts) > 1:
            score.mixed_groups += 1
            score.misplaced += size - max(counts.values())
    for identity, group in faces:
        if group is None:
            score.bcubed_precision += 1
            score.bcubed_recall += 1 / by_identity[identity]
    majority_groups = Counter(
        counts.most_common(1)[0][0] for counts in members.values()
    )
    score.merges = sum(n - 1 for n in majority_groups.values())
    people = {identity for identity, n in by_identity.items() if n >= 2}
    score.people = len(people)
    score.people_found = len(people & set(majority_groups))
    return score


def _pairs(n):
    return n * (n - 1) // 2


def _ratio(part, whole):
    return part / whole if whole else 0.0


def _f1(precision, recall):
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


@dataclass
class LibraryResult:
    library: str
    photos: int = 0
    heads: int = 0
    found: Counter = field(default_factory=Counter)
    detected: Counter = field(default_factory=Counter)
    by_size: Counter = field(default_factory=Counter)
    faces: int = 0
    extra_faces: int = 0
    grouping: GroupingScore = field(default_factory=GroupingScore)
    analysis_cpu: float = 0.0
    analysis_wall: float = 0.0
    model_cpu: float = 0.0
    clustering_cpu: float = 0.0


def score_library(library, photos, stored, raw):
    """A LibraryResult for one library.

    *stored* maps a photo id to the faces the pipeline kept, as
    [(box, group id or None)]; *raw* to every box the detector returned.
    """
    result = LibraryResult(library=library, photos=len(photos))
    grouped = []
    for photo in photos:
        heads = [face.box for face in photo.faces]
        result.heads += len(heads)
        for box in heads:
            result.by_size[size_bucket(box[3])] += 1
        kept = stored.get(photo.photo_id, [])
        result.faces += len(kept)
        pairs = match_faces([box for box, _group in kept], heads)
        result.extra_faces += len(kept) - len(pairs)
        for i, j in pairs:
            result.found[size_bucket(heads[j][3])] += 1
            grouped.append((photo.faces[j].identity, kept[i][1]))
        for _i, j in match_faces(raw.get(photo.photo_id, []), heads):
            result.detected[size_bucket(heads[j][3])] += 1
    result.grouping = score_grouping(grouped)
    return result


def summarize(results, run):
    """The JSON report of a run: totals, per size, per library."""
    grouping = GroupingScore()
    totals = Counter()
    found, detected, by_size = Counter(), Counter(), Counter()
    for result in results:
        grouping.add(result.grouping)
        found.update(result.found)
        detected.update(result.detected)
        by_size.update(result.by_size)
        for name in (
            "photos",
            "heads",
            "faces",
            "extra_faces",
            "analysis_cpu",
            "analysis_wall",
            "model_cpu",
            "clustering_cpu",
        ):
            totals[name] += getattr(result, name)
    photos = totals["photos"]
    heads = totals["heads"]
    report = {
        **run,
        "photos": photos,
        "libraries": len(results),
        "heads": heads,
        "faces": totals["faces"],
        "extra_faces": totals["extra_faces"],
        "found": _ratio(sum(found.values()), heads),
        "detector_found": _ratio(sum(detected.values()), heads),
        "by_size": {
            bucket: {
                "heads": by_size[bucket],
                "found": _ratio(found[bucket], by_size[bucket]),
                "detector_found": _ratio(detected[bucket], by_size[bucket]),
            }
            for bucket in (size_bucket(low) for low in SIZE_BUCKETS)
        },
        "grouping": {**vars(grouping), **grouping.rates()},
        "cpu_ms_per_photo": 1000 * _ratio(totals["analysis_cpu"], photos),
        "wall_ms_per_photo": 1000 * _ratio(totals["analysis_wall"], photos),
        "model_cpu_ms_per_photo": 1000 * _ratio(totals["model_cpu"], photos),
        "clustering_cpu_s": totals["clustering_cpu"],
        "per_library": [
            {
                "library": result.library,
                "photos": result.photos,
                "heads": result.heads,
                "found": _ratio(sum(result.found.values()), result.heads),
                **{
                    name: value
                    for name, value in result.grouping.rates().items()
                    if name in ("pairwise_precision", "pairwise_recall", "bcubed_f1")
                },
                "merges": result.grouping.merges,
                "misplaced": result.grouping.misplaced,
            }
            for result in results
        ],
    }
    return report


# -- report ------------------------------------------------------------------

# (label, path in the report, format, whether higher is better)
_LINES = (
    ("Faces found", ("found",), "pct", True),
    ("  detector alone", ("detector_found",), "pct", True),
    ("Faces not annotated", ("extra_faces",), "int", None),
    ("Grouped", ("grouping", "grouped"), "pct", True),
    ("Pairwise precision", ("grouping", "pairwise_precision"), "pct", True),
    ("Pairwise recall", ("grouping", "pairwise_recall"), "pct", True),
    ("BCubed F1", ("grouping", "bcubed_f1"), "pct", True),
    ("People found", ("grouping", "people_found"), "pct", True),
    ("Groups", ("grouping", "groups"), "int", None),
    ("Groups to merge", ("grouping", "merges"), "int", False),
    ("Mixed groups", ("grouping", "mixed_groups"), "int", False),
    ("Faces to take out", ("grouping", "misplaced"), "int", False),
    ("CPU ms / photo", ("cpu_ms_per_photo",), "ms", False),
    ("  of which models", ("model_cpu_ms_per_photo",), "ms", False),
    ("Grouping CPU s", ("clustering_cpu_s",), "s", False),
    ("Weights MB", ("weights_mb",), "mb", None),
)


def render(report, baseline=None):
    lines = [
        f"Backend {report['backend']} - {report['photos']} photos, "
        f"{report['libraries']} libraries, {report['heads']} annotated people",
        "",
    ]
    for label, path, kind, higher_is_better in _LINES:
        value = _lookup(report, path)
        text = f"{label:<22}{_format(value, kind):>10}"
        if baseline is not None:
            before = _lookup(baseline, path)
            if before is not None:
                text += f"   was {_format(before, kind):>8}"
                text += _delta(value, before, kind, higher_is_better)
        lines.append(text)
    lines += ["", f"{'Head size':<12}{'people':>8}{'found':>9}{'detector':>10}"]
    for bucket, row in report["by_size"].items():
        lines.append(
            f"{bucket:<12}{row['heads']:>8}{_format(row['found'], 'pct'):>9}"
            f"{_format(row['detector_found'], 'pct'):>10}"
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
    if kind == "ms":
        return f"{value:.0f}"
    if kind == "s":
        return f"{value:.1f}"
    return f"{value:.1f}"


def _delta(value, before, kind, higher_is_better):
    if value is None or before is None or value == before:
        return ""
    diff = value - before
    if kind == "pct":
        shown = f"{100 * diff:+.1f} pt"
    elif kind == "int":
        shown = f"{diff:+d}"
    else:
        shown = f"{diff:+.1f}"
    if higher_is_better is None:
        return f"  ({shown})"
    better = (diff > 0) == higher_is_better
    return f"  ({shown}, {'better' if better else 'worse'})"


# -- running the pipeline ----------------------------------------------------


def _migration_state():
    """A digest of everything the migrated schema depends on."""
    digest = hashlib.sha256()
    for path in sorted(REPO_ROOT.glob("workspace/*/migrations/*.py")):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    lock = REPO_ROOT / "uv.lock"
    if lock.is_file():
        digest.update(lock.read_bytes())
    return digest.hexdigest()[:16]


def _configure(args, workdir, cache_dir):
    """Point Django at the throwaway database and media root, then set it up."""
    database = workdir / "bench.sqlite3"
    template = cache_dir / "databases" / f"{_migration_state()}.sqlite3"
    if template.is_file() and not database.exists():
        shutil.copyfile(template, database)
    os.environ.update(
        {
            "DJANGO_SETTINGS_MODULE": "workspace.settings",
            "DATABASE_URL": f"sqlite:///{database}",
            "MEDIA_ROOT": str(workdir / "media"),
            "PHOTOS_FACES_ENABLED": "1",
            "PHOTOS_FACE_BACKEND": args.backend,
            "PHOTOS_ONNX_THREADS": str(args.onnx_threads),
            # Out of the way of whatever the developer runs: no Redis cache
            # or broker shared with them, no SQL logging.
            "REDIS_URL": "",
            "DJANGO_REDIS_URL": "",
            "DEBUG": "false",
        }
    )
    os.environ.setdefault("PHOTOS_MODEL_DIR", str(cache_dir / "models"))
    for setting in args.setting:
        name, _, value = setting.partition("=")
        os.environ[name] = value

    import django

    django.setup()

    from django.core.management import call_command

    if not template.is_file():
        print("Migrating a fresh database (cached for the next runs) ...")
        call_command("migrate", verbosity=0)
        from django.db import connections

        connections.close_all()
        template.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(database, template)
    # The migration froze the default backend's embedding size; another
    # backend's needs the index recreated, as on a real instance.
    call_command(
        "rebuild_vector_index", "workspace.photos.indexes.FACE_EMBEDDINGS", verbosity=0
    )
    from workspace.celery import app

    # Uploads queue their usual follow-up tasks: left in the in-memory
    # broker, they never run. The face pipeline is driven by hand below.
    app.conf.task_always_eager = False
    return database


class _Probe:
    """Times the models and records what the detector returned, per photo."""

    def __init__(self):
        self.current = None
        self.raw = defaultdict(list)
        self.model_cpu = 0.0
        self.clustering_cpu = 0.0

    def install(self, stack):
        from unittest.mock import patch

        from workspace.photos.services import face_grouping
        from workspace.photos.services.detection.registry import get_face_backend

        backend_class = type(get_face_backend())
        detect, embed = backend_class.detect, backend_class.embed
        cluster_owner = face_grouping.cluster_owner
        probe = self

        def timed_detect(backend, image):
            started = time.process_time()
            faces = detect(backend, image)
            probe.model_cpu += time.process_time() - started
            probe.raw[probe.current].extend(face.box for face in faces)
            return faces

        def timed_embed(backend, aligned):
            started = time.process_time()
            vector = embed(backend, aligned)
            probe.model_cpu += time.process_time() - started
            return vector

        def timed_cluster_owner(owner_id):
            started = time.process_time()
            try:
                return cluster_owner(owner_id)
            finally:
                probe.clustering_cpu += time.process_time() - started

        stack.enter_context(patch.object(backend_class, "detect", timed_detect))
        stack.enter_context(patch.object(backend_class, "embed", timed_embed))
        stack.enter_context(
            patch.object(face_grouping, "cluster_owner", timed_cluster_owner)
        )


def run_library(index, library, photos, paths, *, keep):
    """Upload *photos* into a new user's library, analyze them in the order
    they were taken, run the nightly grouping, and score the result."""
    from contextlib import ExitStack

    from django.contrib.auth import get_user_model
    from django.core.files.base import ContentFile

    from workspace.celery import app
    from workspace.files.services import FileService
    from workspace.photos.models import Face
    from workspace.photos.services import face_grouping
    from workspace.photos.services.face_analysis import analyze_faces
    from workspace.photos.services.face_preferences import FACES_ENABLED, MODULE
    from workspace.users.services.settings import set_setting

    user = get_user_model().objects.create_user(
        username=f"bench{index:02d}",
        password=BENCH_PASSWORD if keep else None,
        first_name=photos[0].author[:150],
    )
    folder = FileService.create_folder(owner=user, name="Pictures")
    files = []
    for photo in photos:
        path = paths[photo.photo_id]
        data = path.read_bytes()
        name = f"{photo.taken[:10]} {photo.photo_id}{path.suffix}"
        files.append(
            FileService.create_file(
                owner=user,
                name=name,
                parent=folder,
                content=ContentFile(data, name=name),
            )
        )
    set_setting(user, MODULE, FACES_ENABLED, True)

    probe = _Probe()
    started_cpu, started_wall = time.process_time(), time.perf_counter()
    with ExitStack() as stack:
        probe.install(stack)
        # A grouping run queued once enough faces wait runs at once, as a
        # worker would pick it up between two analyses.
        stack.callback(
            setattr, app.conf, "task_always_eager", app.conf.task_always_eager
        )
        app.conf.task_always_eager = True
        for photo, file_obj in zip(photos, files, strict=True):
            probe.current = photo.photo_id
            if analyze_faces(file_obj) is None:
                # The pipeline logs the failure and moves on, which would
                # score as a photo with nobody in it.
                raise BenchError(
                    f"the analysis of photo {photo.photo_id} failed: see the log above"
                )
        analysis_cpu = time.process_time() - started_cpu - probe.clustering_cpu
        analysis_wall = time.perf_counter() - started_wall
        face_grouping.cluster_owner(user.pk)

    stored = defaultdict(list)
    photo_of = {
        file_obj.pk: photo for photo, file_obj in zip(photos, files, strict=True)
    }
    for face in Face.objects.filter(owner=user):
        photo = photo_of[face.file_id]
        box = (
            face.box_x * photo.width,
            face.box_y * photo.height,
            face.box_width * photo.width,
            face.box_height * photo.height,
        )
        stored[photo.photo_id].append((box, face.cluster_id))
    result = score_library(library, photos, stored, probe.raw)
    result.analysis_cpu = analysis_cpu
    result.analysis_wall = analysis_wall
    result.model_cpu = probe.model_cpu
    result.clustering_cpu = probe.clustering_cpu
    return result


def _weights_mb():
    from workspace.photos.services.detection.registry import get_face_backend
    from workspace.photos.services.detection.weights import model_path

    backend = get_face_backend()
    return sum(model_path(model).stat().st_size for model in backend.models) / 2**20


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


def _select(libraries, args):
    if args.library:
        missing = [name for name in args.library if name not in libraries]
        if missing:
            sys.exit(f"Unknown libraries: {', '.join(missing)}")
        chosen = {name: libraries[name] for name in args.library}
    else:
        chosen = dict(list(libraries.items())[: args.libraries])
    if args.max_photos:
        chosen = {name: photos[: args.max_photos] for name, photos in chosen.items()}
    return chosen


def main():
    parser = argparse.ArgumentParser(
        description="Measure face detection and grouping on real photo libraries.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--backend", default="yunet_sface", help="PHOTOS_FACE_BACKEND")
    parser.add_argument(
        "--libraries",
        type=int,
        default=1000,
        help="run the N largest libraries of the dataset",
    )
    parser.add_argument(
        "--library", action="append", default=[], help="run this library (repeatable)"
    )
    parser.add_argument(
        "--max-photos", type=int, default=0, help="at most N photos per library"
    )
    parser.add_argument(
        "--setting",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="set an environment setting for the run, e.g. PHOTOS_FACES_MIN_SIZE=16",
    )
    parser.add_argument("--onnx-threads", type=int, default=1)
    parser.add_argument("--json", type=Path, help="write the report to this file")
    parser.add_argument("--compare", type=Path, help="an earlier --json report")
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="photos, weights and migrated database (default: PHOTO_DATASET_CACHE "
        "or ~/.cache/workspace/photo-dataset)",
    )
    parser.add_argument(
        "--keep",
        type=Path,
        help="keep the database and media here, to browse the result in the app",
    )
    args = parser.parse_args()

    cache_dir = (args.cache or photo_dataset.default_cache_dir()).resolve()
    baseline = json.loads(args.compare.read_text()) if args.compare else None

    libraries = photo_dataset.by_library(photo_dataset.load_manifest())
    chosen = _select(libraries, args)
    photos = [photo for members in chosen.values() for photo in members]
    print(f"Fetching {len(photos)} photos into {cache_dir} ...", flush=True)
    annotations = photo_dataset.read_annotations(
        photo_dataset.fetch_annotations(cache_dir)
    )
    paths = photo_dataset.fetch_images(photos, cache_dir)
    chosen = {
        name: photo_dataset.with_faces(members, annotations)
        for name, members in chosen.items()
    }

    if args.keep:
        workdir = args.keep.resolve()
        if workdir.exists() and any(workdir.iterdir()):
            sys.exit(f"{workdir} is not empty")
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="face-bench-"))
    try:
        database = _configure(args, workdir, cache_dir)
        from django.conf import settings

        from workspace.photos.services.face_grouping import max_distance

        run = {
            "backend": args.backend,
            "commit": _git_commit(),
            "date": datetime.now().isoformat(timespec="seconds"),
            "settings": {
                name: getattr(settings, name)
                for name in (
                    "PHOTOS_FACES_DECODE_SIZE",
                    "PHOTOS_FACES_MIN_SIZE",
                    "PHOTOS_FACES_MAX_PER_PHOTO",
                    "PHOTOS_FACES_CLUSTER_PENDING",
                )
            },
            "max_distance": max_distance(),
        }
        results = []
        started = time.perf_counter()
        for index, (library, members) in enumerate(chosen.items(), 1):
            result = run_library(index, library, members, paths, keep=bool(args.keep))
            results.append(result)
            rates = result.grouping.rates()
            print(
                f"  [{index}/{len(chosen)}] {library}: {result.photos} photos, "
                f"found {_format(_ratio(sum(result.found.values()), result.heads), 'pct')}, "
                f"pairwise P/R {_format(rates['pairwise_precision'], 'pct')}"
                f"/{_format(rates['pairwise_recall'], 'pct')}",
                flush=True,
            )
        run["weights_mb"] = _weights_mb()
        run["seconds"] = round(time.perf_counter() - started, 1)
        report = summarize(results, run)
    except BenchError as exc:
        sys.exit(f"Bench aborted: {exc}")
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)

    print()
    print(render(report, baseline))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nReport written to {args.json}")
    if args.keep:
        print(
            "\nBrowse the result (users bench01, bench02, ... / "
            f"{BENCH_PASSWORD}):\n"
            f"  DATABASE_URL=sqlite:///{database} MEDIA_ROOT={workdir / 'media'} "
            f"PHOTOS_FACES_ENABLED=1 PHOTOS_FACE_BACKEND={args.backend} "
            "uv run python manage.py runserver 127.0.0.1:<port> --noreload"
        )


if __name__ == "__main__":
    main()
