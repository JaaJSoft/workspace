"""Real photo libraries, for the face bench and the demo seed.

Each library is one Flickr member's photos: family days, trips, parties,
weddings - the daylight portraits and the dark, blurred group shots a real
library is made of. Two public sources, joined:

- **The photos** come from YFCC100M, frozen in 2014 on AWS Open Data (the
  Multimedia Commons bucket) at the 500 px rendition Flickr served then. Every
  one picked is licensed CC BY 2.0 by its author; ``Photo.credit`` is the
  attribution that licence asks for, and has to travel with any copy shown.
- **Who is who** comes from PIPA (People In Photo Albums, Zhang et al., CVPR
  2015): a head box and a person id for the people in those albums. The
  annotation file states no licence, so it is not copied here: it is
  downloaded from a pinned commit at run time and checked against its sha256.

The manifest (``data/photo-dataset.jsonl.gz``) lists the photos picked, the
S3 key and sha256 of each image, the size PIPA drew its boxes at, and what
Flickr's URL of the photo is built from. ``scripts/build_photo_dataset.py``
regenerates it. Nothing here needs Django.

Flickr still serves most of the photos at 1024 px, the size of a library's
faces at ``PHOTOS_FACES_DECODE_SIZE`` once halved: ``fetch_large_images``
takes them from there, for a bench closer to a real library than the frozen
500 px copies.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image

MANIFEST = Path(__file__).resolve().parent / "data" / "photo-dataset.jsonl.gz"

IMAGE_URL = "https://multimedia-commons.s3.amazonaws.com/data/images/{}/{}/{}.jpg"
# Flickr's "large" rendition: server, photo id, secret.
LARGE_URL = "https://live.staticflickr.com/{}/{}_{}_b.jpg"
LARGE_SIZE = 1024
PIPA_URL = (
    "https://raw.githubusercontent.com/coallaoh/PIPA_dataset/"
    "261ccd8794b737d0bbf80bab24c3f502701be1a1/all_data.txt"
)
PIPA_SHA256 = "5c420824e9538e521def2dc3e3948cb72061b6b1456c06b03889c5223ca007f2"

LICENSE_NAME = "CC BY 2.0"

_TIMEOUT = 60
_CHUNK = 1024 * 1024


class DatasetError(RuntimeError):
    """A file could not be downloaded or failed its hash check."""


@dataclass(frozen=True)
class Face:
    """One annotated person: a head box, in the pixels of the downloaded image.

    PIPA boxes the whole head, hair included, and draws it even when the
    person faces away: a head is not always a face a detector can find.
    """

    box: tuple[float, float, float, float]
    identity: str


@dataclass(frozen=True)
class Photo:
    photo_id: int
    # The Flickr member whose library it is (their NSID).
    library: str
    author: str
    # Local time, as the camera recorded it: "2010-03-06T18:57:24".
    taken: str
    title: str
    tags: tuple[str, ...]
    license_url: str
    # The image on the Multimedia Commons bucket, and its sha256.
    key: str
    sha256: str
    width: int
    height: int
    # The long side, in px, of the copy PIPA drew its boxes on.
    annotated_at: int
    # What Flickr's URL of the photo is built from.
    server: str = ""
    secret: str = ""
    album: str = ""
    faces: tuple[Face, ...] = field(default=())

    @property
    def url(self):
        return IMAGE_URL.format(self.key[:3], self.key[3:6], self.key)

    @property
    def large_url(self):
        return LARGE_URL.format(self.server, self.photo_id, self.secret)

    @property
    def page_url(self):
        return f"https://www.flickr.com/photos/{self.library}/{self.photo_id}/"

    @property
    def credit(self):
        title = self.title or "Untitled"
        return f'"{title}" by {self.author} ({self.page_url}), {LICENSE_NAME}'


def load_manifest(path=MANIFEST):
    """The photos of the manifest, without their faces."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [_photo(json.loads(line)) for line in handle if line.strip()]


def _photo(row):
    return Photo(
        photo_id=row["id"],
        library=row["library"],
        author=row["author"],
        taken=row["taken"],
        title=row["title"],
        tags=tuple(row["tags"]),
        license_url=row["license"],
        key=row["key"],
        sha256=row["sha256"],
        width=row["width"],
        height=row["height"],
        annotated_at=row["annotated_at"],
        server=row["server"],
        secret=row["secret"],
    )


def by_library(photos):
    """{library: its photos, oldest first}, the largest libraries first."""
    libraries = defaultdict(list)
    for photo in photos:
        libraries[photo.library].append(photo)
    for members in libraries.values():
        members.sort(key=lambda photo: (photo.taken, photo.photo_id))
    return dict(sorted(libraries.items(), key=lambda item: (-len(item[1]), item[0])))


def default_cache_dir():
    configured = os.environ.get("PHOTO_DATASET_CACHE")
    if configured:
        return Path(configured)
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "workspace" / "photo-dataset"


def read_annotations(path):
    """{photo id: [(album, x, y, w, h, identity)]} from PIPA's all_data.txt.

    Boxes are in the pixels of the copy PIPA annotated.
    """
    annotations = defaultdict(list)
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 7:
                continue
            album, photo_id, x, y, w, h, identity = parts[:7]
            annotations[int(photo_id)].append(
                (album, float(x), float(y), float(w), float(h), identity)
            )
    return annotations


def with_faces(photos, annotations):
    """*photos* with their annotated faces, in the downloaded image's pixels.

    Identities are kept as PIPA gives them. One id is one person wherever it
    appears, but PIPA often gives the same person a new id in each album:
    two ids only say "two people" within one album (face_bench.GroupingScore).
    """
    annotated = []
    for photo in photos:
        rows = annotations.get(photo.photo_id, [])
        scale = max(photo.width, photo.height) / photo.annotated_at
        faces = tuple(
            Face(box=(x * scale, y * scale, w * scale, h * scale), identity=identity)
            for _album, x, y, w, h, identity in rows
        )
        album = rows[0][0] if rows else ""
        annotated.append(replace(photo, faces=faces, album=album))
    return annotated


def fetch_annotations(cache_dir=None):
    """The local path of PIPA's annotation file, downloaded when missing."""
    cache_dir = Path(cache_dir or default_cache_dir())
    path = cache_dir / "pipa_all_data.txt"
    _ensure(path, PIPA_URL, PIPA_SHA256)
    return path


def fetch_images(photos, cache_dir=None, *, workers=8, progress=None):
    """{photo id: local path} of every photo, downloading the missing ones.

    Each file is checked against the manifest's sha256. Raises DatasetError
    on the first photo that cannot be had.
    """
    images = Path(cache_dir or default_cache_dir()) / "images"

    def fetch(photo):
        path = images / photo.key[:3] / f"{photo.key}.jpg"
        _ensure(path, photo.url, photo.sha256)
        return photo.photo_id, path

    paths = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (photo_id, path) in enumerate(pool.map(fetch, photos), 1):
            paths[photo_id] = path
            if progress:
                progress(done, len(photos))
    return paths


def fetch_large_images(photos, cache_dir=None, *, workers=8, progress=None):
    """(photos, paths): those of *photos* Flickr still serves at 1024 px,
    sized as that copy, and {photo id: local path} of each copy.

    Flickr is no frozen archive: a photo deleted or made private since 2014
    is gone, and its copies are pinned nowhere. So the first answer for each
    photo is kept in the cache - the copy and its sha256, or that it was
    gone - and every later run uses exactly that: the same photos and the
    same bytes on one machine, whatever Flickr serves by then. A network
    error is no answer: it raises DatasetError and records nothing.
    """
    large = Path(cache_dir or default_cache_dir()) / "large"
    index_path = large / "index.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {}
    )

    def fetch(photo):
        path = large / photo.key[:3] / f"{photo.key}.jpg"
        known = index.get(str(photo.photo_id))
        if known is None:
            return photo, path, _fetch_large(photo, path)
        if known.get("gone"):
            return photo, path, known
        if not path.is_file() or _sha256(path) != known["sha256"]:
            _ensure(path, photo.large_url, known["sha256"])
        return photo, path, known

    kept, paths = [], {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for done, (photo, path, entry) in enumerate(pool.map(fetch, photos), 1):
                index[str(photo.photo_id)] = entry
                if not entry.get("gone"):
                    kept.append(
                        replace(photo, width=entry["width"], height=entry["height"])
                    )
                    paths[photo.photo_id] = path
                if progress:
                    progress(done, len(photos))
    finally:
        large.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=large, delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(index, tmp, sort_keys=True)
        os.replace(tmp.name, index_path)
    return kept, paths


def _fetch_large(photo, path):
    """The index entry of *photo*'s 1024 px copy, saved at *path* if Flickr
    still has it."""
    try:
        with urllib.request.urlopen(photo.large_url, timeout=_TIMEOUT) as response:
            # A photo gone from Flickr redirects to a "no longer available"
            # picture rather than failing.
            gone = "photo_unavailable" in response.geturl()
            data = b"" if gone else response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 410):
            raise DatasetError(f"cannot download {photo.large_url}: {exc}") from exc
        data = b""
    except OSError as exc:
        raise DatasetError(f"cannot download {photo.large_url}: {exc}") from exc
    size = _jpeg_size(data)
    if size is None or not _is_large_copy(size, photo):
        return {"gone": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
    os.replace(tmp.name, path)
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": size[0],
        "height": size[1],
    }


def _jpeg_size(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size if image.format == "JPEG" else None
    except OSError:
        return None


def _is_large_copy(size, photo):
    """Whether *size* is that of *photo*'s 1024 px rendition.

    The scale of the boxes assumes that long side, and a copy of another
    shape is another picture.
    """
    width, height = size
    return (
        max(size) == LARGE_SIZE
        and abs(width * photo.height - height * photo.width)
        <= 0.02 * width * photo.height
    )


def _ensure(path, url, sha256):
    if path.is_file():
        if _sha256(path) == sha256:
            return path
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed into place: an interrupted run
    # never leaves half a file that would pass for a cached one.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        try:
            digest = _download(url, tmp)
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
    if digest != sha256:
        Path(tmp.name).unlink(missing_ok=True)
        raise DatasetError(f"{url} does not match its pinned sha256")
    os.replace(tmp.name, path)
    return path


def _download(url, out):
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
            while chunk := response.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        raise DatasetError(f"cannot download {url}: {exc}") from exc
    return digest.hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
