"""Retype the camera RAW photos that were detected as TIFF or unknown.

Content detection knew no RAW: a NEF passed for a TIFF whose picture was the
160x120 thumbnail of its first directory, an ARW for a TIFF nothing could
decode. A row is retyped when its name carries a RAW extension and LibRaw
opens its blob, so a plain TIFF renamed .nef keeps its type. The extension
table is a copy of ``services/raw_images.py`` at the time of writing.

The thumbnail made from the wrong picture goes with it, and so does the
failure ledger, so the catch-up decodes the photo at its next pass.
"""

import io
import logging

import rawpy
from django.db import migrations

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

_RAW_FORMATS = {
    "dng": ("image/x-adobe-dng", ["dng"]),
    "cr2": ("image/x-canon-cr2", ["cr2"]),
    "cr3": ("image/x-canon-cr3", ["cr3"]),
    "nef": ("image/x-nikon-nef", ["nef", "nrw"]),
    "arw": ("image/x-sony-arw", ["arw", "srf", "sr2"]),
    "orf": ("image/x-olympus-orf", ["orf"]),
    "rw2": ("image/x-panasonic-rw2", ["rw2"]),
    "pef": ("image/x-pentax-pef", ["pef"]),
    "raf": ("image/x-fuji-raf", ["raf"]),
}
_LABEL_BY_EXTENSION = {
    extension: label
    for label, (_, extensions) in _RAW_FORMATS.items()
    for extension in extensions
}
_NAME_PATTERN = r"\.(" + "|".join(_LABEL_BY_EXTENSION) + ")$"


def _opens_as_raw(data):
    try:
        with rawpy.imread(io.BytesIO(data)):
            return True
    except rawpy.LibRawError:
        return False


def retype_raws(apps, schema_editor):
    File = apps.get_model("files", "File")
    ProcessingFailure = apps.get_model("files", "ProcessingFailure")
    db = schema_editor.connection.alias

    candidates = (
        File.objects.using(db)
        .filter(name__iregex=_NAME_PATTERN)
        .exclude(type__in=_RAW_FORMATS)
        .exclude(content="")
        .exclude(content__isnull=True)
    )
    for row in candidates.iterator():
        try:
            with row.content.open("rb") as handle:
                data = handle.read()
        except OSError as exc:
            logger.warning(
                "Cannot read %s to retype it: %s", scrub(row.content.name), exc
            )
            continue
        if not _opens_as_raw(data):
            continue
        label = _LABEL_BY_EXTENSION[row.name.rpartition(".")[2].lower()]
        File.objects.using(db).filter(pk=row.pk).update(
            type=label,
            category="image",
            mime_type=_RAW_FORMATS[label][0],
            viewer="",
            has_thumbnail=False,
        )
        ProcessingFailure.objects.using(db).filter(file_id=row.pk).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0073_file_image_edit"),
    ]

    operations = [
        migrations.RunPython(retype_raws, migrations.RunPython.noop),
    ]
