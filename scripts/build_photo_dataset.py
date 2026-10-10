#!/usr/bin/env python
"""Rebuild scripts/data/photo-dataset.jsonl.gz from its sources.

The manifest is committed: this only runs to change what it holds. It needs
apsw, which is not a dependency of the project:

    uv run --with apsw python scripts/build_photo_dataset.py

1. Every photo PIPA annotated is looked up in YFCC100M's metadata. That
   SQLite database is 65 GB on the Multimedia Commons bucket; it is read
   through HTTP range requests, a few pages per photo, never downloaded.
2. Photos licensed CC BY 2.0 whose image the bucket holds are kept,
   downloaded and hashed.
3. PIPA drew its boxes on one of two renditions of each photo: the 500 px one
   the bucket holds, or Flickr's 1024 px one. Which one is decided per album
   (an album was fetched all alike) by detecting faces with the default
   backend and keeping the scale that puts more of them inside the head boxes.
4. The libraries (Flickr members) with at least --min-photos photos are
   written, the largest first, until --max-photos photos are in.

Lookups and detections are cached in the dataset cache, so a second run only
redoes the selection.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import threading
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote_plus

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import photo_dataset  # noqa: E402

YFCC_URL = "https://multimedia-commons.s3.amazonaws.com/tools/etc/yfcc100m_dataset.sql"
CC_BY = "http://creativecommons.org/licenses/by/2.0/"
_FIELDS = (
    "photoid",
    "uid",
    "unickname",
    "datetaken",
    "title",
    "usertags",
    "licenseurl",
    "downloadurl",
    "marker",
)
FLICKR_LARGE = 1024


# -- YFCC100M over HTTP --------------------------------------------------------


class _RemoteFile:
    """A read-only file whose bytes come from HTTP range requests."""

    block = 4096
    cache_blocks = 60000

    def __init__(self, url):
        import httpx

        self.url = url
        self._local = threading.local()
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self.size = int(httpx.head(url, timeout=60).headers["content-length"])

    def _client(self):
        import httpx

        client = getattr(self._local, "client", None)
        if client is None:
            client = self._local.client = httpx.Client(timeout=60)
        return client

    def _block(self, index):
        with self._lock:
            data = self._cache.get(index)
            if data is not None:
                self._cache.move_to_end(index)
                return data
        start = index * self.block
        for attempt in range(5):
            try:
                response = self._client().get(
                    self.url,
                    headers={"Range": f"bytes={start}-{start + self.block - 1}"},
                )
                response.raise_for_status()
                break
            except Exception:
                if attempt == 4:
                    raise
        with self._lock:
            self._cache[index] = response.content
            while len(self._cache) > self.cache_blocks:
                self._cache.popitem(last=False)
        return response.content

    def read(self, amount, offset):
        out = bytearray()
        position, end = offset, min(offset + amount, self.size)
        while position < end:
            index = position // self.block
            data = self._block(index)
            low = position - index * self.block
            take = min(end - position, len(data) - low)
            out += data[low : low + take]
            position += take
        return bytes(out) + b"\0" * (amount - len(out))


def _yfcc_connection(remote):
    import apsw

    class File:
        def __init__(self, *_args):
            pass

        def xRead(self, amount, offset):
            return remote.read(amount, offset)

        def xFileSize(self):
            return remote.size

        def xClose(self):
            pass

        def xLock(self, level):
            pass

        def xUnlock(self, level):
            pass

        def xCheckReservedLock(self):
            return False

        def xFileControl(self, op, pointer):
            return False

        def xSectorSize(self):
            return 1024

        def xDeviceCharacteristics(self):
            return 0

        def xSync(self, flags):
            pass

        def xTruncate(self, size):
            raise apsw.ReadOnlyError()

        def xWrite(self, data, offset):
            raise apsw.ReadOnlyError()

    class VFS(apsw.VFS):
        def __init__(self):
            super().__init__("yfcc-http", "")

        def xOpen(self, name, flags):
            return File()

        def xAccess(self, pathname, flags):
            return False

        def xFullPathname(self, name):
            return name

    if not hasattr(_yfcc_connection, "vfs"):
        _yfcc_connection.vfs = VFS()
    return apsw.Connection(
        "yfcc100m.sqlite", flags=apsw.SQLITE_OPEN_READONLY, vfs="yfcc-http"
    )


def lookup(photo_ids, cache_dir):
    """{photo id: YFCC100M row} for the photos YFCC100M holds."""
    path = cache_dir / "build" / "yfcc.jsonl"
    rows = {}
    if path.is_file():
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            rows[row["photoid"]] = row
    todo = sorted(set(photo_ids) - set(rows) - _missing(cache_dir))
    if not todo:
        return {pid: row for pid, row in rows.items() if row.get("downloadurl")}
    print(f"Looking up {len(todo)} photos in YFCC100M ...", flush=True)
    remote = _RemoteFile(YFCC_URL)
    local = threading.local()
    query = f"select {', '.join(_FIELDS)} from yfcc100m_dataset where photoid = ?"

    def one(photo_id):
        connection = getattr(local, "connection", None)
        if connection is None:
            connection = local.connection = _yfcc_connection(remote)
        found = list(connection.execute(query, (photo_id,)))
        return photo_id, dict(zip(_FIELDS, found[0], strict=True)) if found else None

    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("a", encoding="utf-8") as out,
        (cache_dir / "build" / "yfcc-missing.txt").open(
            "a", encoding="utf-8"
        ) as missing,
        ThreadPoolExecutor(32) as pool,
    ):
        for done, (photo_id, row) in enumerate(pool.map(one, todo), 1):
            if row is None:
                missing.write(f"{photo_id}\n")
            else:
                rows[photo_id] = row
                out.write(json.dumps(row) + "\n")
            if done % 2000 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
    return {pid: row for pid, row in rows.items() if row.get("downloadurl")}


def _missing(cache_dir):
    path = cache_dir / "build" / "yfcc-missing.txt"
    return (
        {int(line) for line in path.open(encoding="utf-8")} if path.is_file() else set()
    )


# -- images ------------------------------------------------------------------


def image_key(download_url):
    return hashlib.md5(download_url.encode()).hexdigest()


def fetch_images(rows, cache_dir):
    """{photo id: (path, sha256, width, height)} for the images the bucket has."""
    import httpx
    from PIL import Image

    index_path = cache_dir / "build" / "images.jsonl"
    known = {}
    if index_path.is_file():
        for line in index_path.open(encoding="utf-8"):
            entry = json.loads(line)
            known[entry["photoid"]] = entry
    todo = [pid for pid in rows if pid not in known]
    images = cache_dir / "images"
    local = threading.local()

    def one(photo_id):
        client = getattr(local, "client", None)
        if client is None:
            client = local.client = httpx.Client(timeout=60)
        key = image_key(rows[photo_id]["downloadurl"])
        entry = {"photoid": photo_id, "key": key}
        response = client.get(photo_dataset.IMAGE_URL.format(key[:3], key[3:6], key))
        if response.status_code != 200:
            return entry
        data = response.content
        try:
            with Image.open(io.BytesIO(data)) as image:
                entry["width"], entry["height"] = image.size
        except OSError:
            return entry
        path = images / key[:3] / f"{key}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        return entry

    if todo:
        print(f"Fetching {len(todo)} images ...", flush=True)
        with (
            index_path.open("a", encoding="utf-8") as out,
            ThreadPoolExecutor(16) as pool,
        ):
            for done, entry in enumerate(pool.map(one, todo), 1):
                known[entry["photoid"]] = entry
                out.write(json.dumps(entry) + "\n")
                if done % 1000 == 0:
                    print(f"  {done}/{len(todo)}", flush=True)
    return {
        pid: entry
        for pid, entry in known.items()
        if pid in rows and entry.get("sha256")
    }


# -- which rendition PIPA annotated -----------------------------------------


def detect_all(images, cache_dir, backend_key):
    """{photo id: [face boxes]} found by *backend_key* on each image."""
    import numpy as np
    from PIL import Image

    path = cache_dir / "build" / f"detections-{backend_key}.jsonl"
    found = {}
    if path.is_file():
        for line in path.open(encoding="utf-8"):
            entry = json.loads(line)
            found[entry["photoid"]] = entry["boxes"]
    todo = [pid for pid in images if pid not in found]
    if not todo:
        return found
    from workspace.photos.services.detection.registry import get_face_backend

    backend = get_face_backend(backend_key)
    backend.prepare()
    print(f"Detecting faces in {len(todo)} images ...", flush=True)
    with path.open("a", encoding="utf-8") as out:
        for done, photo_id in enumerate(todo, 1):
            key = images[photo_id]["key"]
            with Image.open(cache_dir / "images" / key[:3] / f"{key}.jpg") as image:
                pixels = np.asarray(image.convert("RGB"))
            boxes = [list(face.box) for face in backend.detect(pixels)]
            found[photo_id] = boxes
            out.write(json.dumps({"photoid": photo_id, "boxes": boxes}) + "\n")
            if done % 500 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
    return found


def _hits(boxes, heads, scale):
    from face_bench import match_faces

    scaled = [(x * scale, y * scale, w * scale, h * scale) for x, y, w, h in heads]
    return len(match_faces(boxes, scaled))


def annotated_sizes(images, annotations, detections):
    """{photo id: long side PIPA drew its boxes at}, decided album by album.

    An album where no scale wins (no face found at either) takes the 1024 px
    rendition when a box reaches past the 500 px image, the 500 px one
    otherwise.
    """
    votes = defaultdict(int)
    overflow = defaultdict(bool)
    album_of = {}
    for photo_id, entry in images.items():
        rows = annotations[photo_id]
        album = rows[0][0]
        album_of[photo_id] = album
        long_side = max(entry["width"], entry["height"])
        heads = [(x, y, w, h) for _album, x, y, w, h, _identity in rows]
        boxes = detections.get(photo_id, [])
        votes[album] += _hits(boxes, heads, long_side / FLICKR_LARGE) - _hits(
            boxes, heads, 1.0
        )
        overflow[album] |= any(
            x > entry["width"] or y > entry["height"] for x, y, _w, _h in heads
        )
    sizes = {}
    for photo_id, entry in images.items():
        album = album_of[photo_id]
        large = votes[album] > 0 or (votes[album] == 0 and overflow[album])
        sizes[photo_id] = (
            FLICKR_LARGE if large else max(entry["width"], entry["height"])
        )
    return sizes


# -- the manifest ------------------------------------------------------------


def _taken(value):
    # "2010-03-06 18:57:24.0" -> "2010-03-06T18:57:24"
    return value.split(".")[0].replace(" ", "T")


def manifest_rows(rows, images, sizes, min_photos, max_photos):
    libraries = defaultdict(list)
    for photo_id in images:
        libraries[rows[photo_id]["uid"]].append(photo_id)
    picked = []
    for _library, members in sorted(
        libraries.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        if len(members) < min_photos or len(picked) + len(members) > max_photos:
            continue
        picked += members
    out = []
    for photo_id in picked:
        row, entry = rows[photo_id], images[photo_id]
        out.append(
            {
                "id": photo_id,
                "library": row["uid"],
                "author": unquote_plus(row["unickname"] or ""),
                "taken": _taken(row["datetaken"]),
                "title": unquote_plus(row["title"] or ""),
                "tags": [
                    unquote_plus(tag)
                    for tag in (row["usertags"] or "").split(",")
                    if tag
                ],
                "license": row["licenseurl"],
                "key": entry["key"],
                "sha256": entry["sha256"],
                "width": entry["width"],
                "height": entry["height"],
                "annotated_at": sizes[photo_id],
            }
        )
    out.sort(key=lambda r: (r["library"], r["taken"], r["id"]))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-photos", type=int, default=30)
    parser.add_argument("--max-photos", type=int, default=3000)
    parser.add_argument("--detector", default="yunet_sface")
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=photo_dataset.MANIFEST)
    args = parser.parse_args()
    cache_dir = (args.cache or photo_dataset.default_cache_dir()).resolve()

    annotations = photo_dataset.read_annotations(
        photo_dataset.fetch_annotations(cache_dir)
    )
    rows = {
        pid: row
        for pid, row in lookup(list(annotations), cache_dir).items()
        if row["licenseurl"] == CC_BY and row["marker"] == 0
    }
    images = fetch_images(rows, cache_dir)
    print(f"{len(images)} CC BY photos on the bucket", flush=True)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
    os.environ.setdefault("PHOTOS_MODEL_DIR", str(cache_dir / "models"))
    sys.path.insert(0, str(SCRIPTS.parent))
    import django

    django.setup()
    detections = detect_all(images, cache_dir, args.detector)
    sizes = annotated_sizes(images, annotations, detections)
    out = manifest_rows(rows, images, sizes, args.min_photos, args.max_photos)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # No name, no mtime: the same content always compresses to the same bytes.
    with (
        open(args.output, "wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle,
    ):
        for row in out:
            handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
    libraries = len({row["library"] for row in out})
    print(f"Wrote {len(out)} photos of {libraries} libraries to {args.output}")


if __name__ == "__main__":
    main()
