"""Shared setup for the tests of the people found by their body: the fake
backends, every feature they need turned on, and rows written directly."""

import io
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from workspace.common.vectors.encoding import normalize, to_bytes
from workspace.people.services.persons import create_person
from workspace.photos.models import Appearance, AppearanceAnalysis, Face, FaceCluster
from workspace.photos.services.appearances import APPEARANCE_ANALYSIS_VERSION
from workspace.photos.services.embedding.fake import FakeEmbeddingBackend, colour_vector

from .images import make_photo

appearances_on = override_settings(
    PHOTOS_FACES_ENABLED=True,
    PHOTOS_FACE_BACKEND="fake",
    PHOTOS_FACES_MAX_DISTANCE=None,
    PHOTOS_EMBEDDINGS_ENABLED=True,
    PHOTOS_EMBEDDING_BACKEND="fake",
    PHOTOS_APPEARANCES_ENABLED=True,
)

RED = (255, 0, 0)
DARK_RED = (140, 0, 0)
BLUE = (0, 0, 255)

NOON = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)

# Boxes beside the one ``seen`` puts a person in by default, and over it,
# from the waist down: the same person boxed a second time.
BESIDE = (0.6, 0.1, 0.3, 0.8)
LEGS = (0.1, 0.45, 0.3, 0.45)


def torso_png(*people, size=(800, 600)):
    """A grey picture with a torso of each ``(box, colour)`` of *people*
    painted where the pipeline cuts it from that box (see
    appearances.torso_box)."""
    image = Image.new("RGB", size, (128, 128, 128))
    draw = ImageDraw.Draw(image)
    for (x, y, w, h), colour in people:
        draw.rectangle(
            [x, y + 0.4 * w, x + w - 1, min(y + h, y + 1.6 * w) - 1], fill=colour
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def photo_at(owner, name, hours=0.0):
    """A photo of *owner*'s library taken *hours* after noon."""
    return make_photo(owner, name, NOON + timedelta(hours=hours))


def seen(owner, photo, colour, *, box=(0.1, 0.1, 0.3, 0.8), **fields):
    """A person found in *photo* by their body, wearing *colour*, as the
    analysis would have stored them."""
    AppearanceAnalysis.objects.get_or_create(
        file=photo,
        defaults={
            "owner": owner,
            "content_hash": photo.content_hash,
            "model": FakeEmbeddingBackend.key,
            "version": APPEARANCE_ANALYSIS_VERSION,
            "analyzed_at": timezone.now(),
        },
    )
    dims = FakeEmbeddingBackend.dims
    x, y, w, h = box
    return Appearance.objects.create(
        file=photo,
        owner=owner,
        box_x=x,
        box_y=y,
        box_width=w,
        box_height=h,
        detector_score=0.9,
        embedding=to_bytes(normalize(colour_vector(colour, dims), dims)),
        **fields,
    )


def contact(owner, name):
    return create_person(owner=owner, display_name=name)


def named_face(owner, photo, person, *, box=(0.15, 0.12, 0.1, 0.12), cluster=None):
    """A face of *person* in *photo*: by default where the head of the person
    ``seen`` puts in it is. With *person* None, a face of *cluster*, or of a
    new unnamed one."""
    if cluster is None and person is not None:
        cluster = FaceCluster.objects.filter(owner=owner, person=person).first()
    if cluster is None:
        cluster = FaceCluster.objects.create(owner=owner, person=person)
    x, y, w, h = box
    return Face.objects.create(
        file=photo,
        owner=owner,
        box_x=x,
        box_y=y,
        box_width=w,
        box_height=h,
        detector_score=0.99,
        quality=0.9,
        cluster=cluster,
    )
