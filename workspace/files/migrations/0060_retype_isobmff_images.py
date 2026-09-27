"""Retype the HEIF and AVIF photos that were detected as MP4 videos.

The three formats share the ISO base media container, and content detection
used to file a photo straight off an iPhone under mp4. Only rows whose name
carries an image extension are read: their leading ftyp box decides, so a
real video renamed .heic keeps its type. The brand table is a copy of
``services/detection.py`` at the time of writing.

What was derived from the wrong type goes with it: the video probe, and the
thumbnail failure ledger, so the catch-up decodes the photo at its next pass.
"""

import logging

from django.db import migrations

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

_IMAGE_BRANDS = {
    b"heic": "heif",
    b"heix": "heif",
    b"heim": "heif",
    b"heis": "heif",
    b"hevc": "heif",
    b"hevx": "heif",
    b"avif": "avif",
    b"avis": "avif",
}
_GENERIC_IMAGE_BRANDS = frozenset({b"mif1", b"msf1"})
_MIME_TYPES = {"heif": "image/heic", "avif": "image/avif"}


def _image_label(head):
    if head[4:8] != b"ftyp":
        return None
    major = head[8:12]
    if major in _IMAGE_BRANDS:
        return _IMAGE_BRANDS[major]
    if major not in _GENERIC_IMAGE_BRANDS:
        return None
    box_end = min(int.from_bytes(head[:4], "big"), len(head))
    compatible = {head[i : i + 4] for i in range(16, box_end - 3, 4)}
    return "avif" if {b"avif", b"avis"} & compatible else "heif"


def retype_images(apps, schema_editor):
    File = apps.get_model("files", "File")
    MediaInfo = apps.get_model("files", "MediaInfo")
    ThumbnailFailure = apps.get_model("files", "ThumbnailFailure")
    db = schema_editor.connection.alias

    candidates = (
        File.objects.using(db)
        .filter(type__in=["mp4", "qt", "3gp"], name__iregex=r"\.(heic|heif|hif|avif)$")
        .exclude(content="")
        .exclude(content__isnull=True)
    )
    for row in candidates.iterator():
        try:
            with row.content.open("rb") as handle:
                head = handle.read(64)
        except OSError as exc:
            logger.warning("Cannot read %s to retype it: %s", scrub(row.content.name), exc)
            continue
        label = _image_label(head)
        if label is None:
            continue
        File.objects.using(db).filter(pk=row.pk).update(
            type=label, category="image", mime_type=_MIME_TYPES[label], viewer=""
        )
        MediaInfo.objects.using(db).filter(file_id=row.pk).delete()
        ThumbnailFailure.objects.using(db).filter(file_id=row.pk).delete()

    # Detected as avif, but filed under the video group Magika gives it.
    File.objects.using(db).filter(type="avif").exclude(category="image").update(
        category="image"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0059_file_link_state"),
    ]

    operations = [
        migrations.RunPython(retype_images, migrations.RunPython.noop),
    ]
