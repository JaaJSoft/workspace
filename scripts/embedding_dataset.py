"""Photos with what they show, for the embedding bench.

Open Images V7's validation set: 41,620 photos from Flickr, licensed CC BY 2.0
by their authors, each verified by people for the labels it carries (Google's
annotations, CC BY 4.0). A label confirmed on a photo is what makes a search
for it right: "dog" should find the photos labelled Dog, and nothing else
ranks for free.

The two annotation files are downloaded from a pinned version and checked
against their sha256. The photos are picked from them deterministically (the
same ones on every machine), downloaded from the Open Images bucket on AWS
Open Data, and kept downscaled to PREVIEW_SIZE px: the size the library hands
a backend (``EmbeddingBackend.decode_size``) and a tenth of the originals'
weight. Nothing here needs Django.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import tempfile
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import photo_dataset  # noqa: E402

LABELS_URL = (
    "https://storage.googleapis.com/openimages/v7/"
    "oidv7-val-annotations-human-imagelabels.csv"
)
LABELS_SHA256 = "92ddbdfceb3626e044df5e89100b24f6c22a79c1888a4bddd00a6f231d86d56a"
CLASSES_URL = (
    "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions.csv"
)
CLASSES_SHA256 = "84a4373a0efb7fd6d93fe19b0e7ceb6c1b855c233d13b9b78a9a33655c9fdce3"
IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/validation/{}.jpg"

PREVIEW_SIZE = 512
_JPEG_QUALITY = 90


@dataclass(frozen=True)
class Query:
    """A search as a user types it, in English and in French, and the labels
    a photo must all carry to be a right answer."""

    text: str
    french: str
    labels: tuple[str, ...]


# What people look for in their own photos, among the labels Open Images
# verified on enough of its validation photos. The pairs ask for both labels.
QUERIES = (
    Query("dog", "chien", ("Dog",)),
    Query("cat", "chat", ("Cat",)),
    Query("horse", "cheval", ("Horse",)),
    Query("bird", "oiseau", ("Bird",)),
    Query("car", "voiture", ("Car",)),
    Query("bicycle", "vélo", ("Bicycle",)),
    Query("motorcycle", "moto", ("Motorcycle",)),
    Query("boat", "bateau", ("Boat",)),
    Query("airplane", "avion", ("Airplane",)),
    Query("train", "train", ("Train",)),
    Query("bus", "bus", ("Bus",)),
    Query("flowers", "fleurs", ("Flower",)),
    Query("tree", "arbre", ("Tree",)),
    Query("mountain", "montagne", ("Mountain",)),
    Query("snow", "neige", ("Snow",)),
    Query("sea", "mer", ("Sea",)),
    Query("river", "rivière", ("River",)),
    Query("waterfall", "cascade", ("Waterfall",)),
    Query("skyscraper", "gratte-ciel", ("Skyscraper",)),
    Query("statue", "statue", ("Statue",)),
    Query("tent", "tente", ("Tent",)),
    Query("people swimming", "baignade", ("Swimming",)),
    Query("cake", "gâteau", ("Cake",)),
    Query("pizza", "pizza", ("Pizza",)),
    Query("bread", "pain", ("Bread",)),
    Query("fruit", "fruits", ("Fruit",)),
    Query("vegetables", "légumes", ("Vegetable",)),
    Query("wine", "vin", ("Wine",)),
    Query("beer", "bière", ("Beer",)),
    Query("guitar", "guitare", ("Guitar",)),
    Query("book", "livre", ("Book",)),
    Query("laptop", "ordinateur portable", ("Laptop",)),
    Query("mobile phone", "téléphone portable", ("Mobile phone",)),
    Query("baby", "bébé", ("Baby",)),
    Query("toys", "jouets", ("Toy",)),
    Query("balloons", "ballons de baudruche", ("Balloon",)),
    Query("candles", "bougies", ("Candle",)),
    Query("umbrella", "parapluie", ("Umbrella",)),
    Query("painting", "tableau", ("Painting",)),
    Query("boat on the sea", "bateau sur la mer", ("Boat", "Sea")),
    Query("snowy mountain", "montagne enneigée", ("Mountain", "Snow")),
    Query("dog in the snow", "chien dans la neige", ("Dog", "Snow")),
    Query("cake with candles", "gâteau avec des bougies", ("Cake", "Candle")),
    Query("child with a toy", "enfant avec un jouet", ("Child", "Toy")),
    Query("bicycle in the city", "vélo en ville", ("Bicycle", "Building")),
)

# Photos picked per query, at most, and how many show none of the queries'
# labels: the rest of a library, which a search must leave out.
PER_QUERY = 30
DISTRACTORS = 300


@dataclass(frozen=True)
class BenchPhoto:
    image_id: str
    # The display names of every label confirmed on the photo.
    labels: frozenset[str]

    @property
    def url(self):
        return IMAGE_URL.format(self.image_id)


def read_classes(path):
    """{label id: display name}."""
    with open(path, encoding="utf-8", newline="") as handle:
        return {row["LabelName"]: row["DisplayName"] for row in csv.DictReader(handle)}


def read_labels(path, classes):
    """{image id: display names confirmed on it} for every image of the file.

    Only confirmed labels count (Confidence 1): a label verified absent, or
    never asked about, is not on the photo.
    """
    labels = defaultdict(set)
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # Touched for every image: one with no confirmed label is a distractor.
            labels[row["ImageID"]]
            if row["Confidence"] in ("1", "1.0"):
                labels[row["ImageID"]].add(classes.get(row["LabelName"], ""))
    return {image_id: frozenset(names) for image_id, names in labels.items()}


def _order(image_id):
    """A stable shuffle: the same photos come first on every machine."""
    return hashlib.sha256(image_id.encode()).hexdigest()


def pick(labels, queries=QUERIES, *, per_query=PER_QUERY, distractors=DISTRACTORS):
    """The bench's photos, from {image id: labels}: up to *per_query* right
    answers for each query, then *distractors* photos none of them wants.

    A query with no right answer among them is left out of the bench by the
    caller (``usable_queries``).
    """
    ordered = sorted(labels, key=_order)
    picked = {}
    for query in queries:
        wanted = set(query.labels)
        matching = [i for i in ordered if wanted <= labels[i]]
        for image_id in matching[:per_query]:
            picked[image_id] = labels[image_id]
    every_label = {name for query in queries for name in query.labels}
    others = [i for i in ordered if i not in picked and not labels[i] & every_label]
    for image_id in others[:distractors]:
        picked[image_id] = labels[image_id]
    return [BenchPhoto(i, picked[i]) for i in sorted(picked, key=_order)]


def usable_queries(photos, queries=QUERIES, *, minimum=5):
    """The queries with at least *minimum* right answers among *photos*."""
    return [
        query
        for query in queries
        if sum(set(query.labels) <= photo.labels for photo in photos) >= minimum
    ]


def default_cache_dir():
    return photo_dataset.default_cache_dir().parent / "embedding-dataset"


def load(cache_dir=None, *, limit=None):
    """The bench's photos, their annotation files downloaded when missing.

    *limit* keeps the first ones (the order is stable), for a quick run.
    """
    cache_dir = Path(cache_dir or default_cache_dir())
    classes_path = cache_dir / "class-descriptions.csv"
    labels_path = cache_dir / "val-human-imagelabels.csv"
    photo_dataset._ensure(classes_path, CLASSES_URL, CLASSES_SHA256)
    photo_dataset._ensure(labels_path, LABELS_URL, LABELS_SHA256)
    photos = pick(read_labels(labels_path, read_classes(classes_path)))
    return photos[:limit] if limit else photos


def fetch_images(photos, cache_dir=None, *, workers=8, progress=None):
    """{image id: local path} of the downscaled copy of every photo,
    downloading the missing ones. Raises photo_dataset.DatasetError on the
    first photo that cannot be had."""
    images = Path(cache_dir or default_cache_dir()) / "images"

    def fetch(photo):
        path = images / photo.image_id[:2] / f"{photo.image_id}.jpg"
        if not path.is_file():
            _fetch_preview(photo.url, path)
        return photo.image_id, path

    paths = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (image_id, path) in enumerate(pool.map(fetch, photos), 1):
            paths[image_id] = path
            if progress:
                progress(done, len(photos))
    return paths


def _fetch_preview(url, path):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    except OSError as exc:
        raise photo_dataset.DatasetError(f"cannot download {url}: {exc}") from exc
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed into place: an interrupted
        # run never leaves half a file that would pass for a cached one.
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            img.save(tmp, format="JPEG", quality=_JPEG_QUALITY)
        os.replace(tmp.name, path)
