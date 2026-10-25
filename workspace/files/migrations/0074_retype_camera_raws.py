"""Retype the camera RAW photos that were detected as TIFF or unknown.

Content detection had no RAW label, and most RAW formats are TIFFs: a NEF
passed for a TIFF whose picture was the 160x120 thumbnail of its first
directory, an ARW for a TIFF nothing could decode. Only rows whose name
carries a RAW extension are read, and their header decides, so a plain TIFF
renamed .nef keeps its type. The signatures are a copy of
``services/raw_images.py`` at the time of writing.

The thumbnail made from the wrong picture goes with it, and so does the
failure ledger, so the catch-up decodes the photo at its next pass.
"""

import logging
import struct

from django.db import migrations

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

_MIME_TYPES = {
    "dng": "image/x-adobe-dng",
    "cr2": "image/x-canon-cr2",
    "cr3": "image/x-canon-cr3",
    "nef": "image/x-nikon-nef",
    "arw": "image/x-sony-arw",
    "orf": "image/x-olympus-orf",
    "rw2": "image/x-panasonic-rw2",
    "pef": "image/x-pentax-pef",
    "raf": "image/x-fuji-raf",
}
_EXTENSIONS = r"\.(dng|cr2|cr3|nef|nrw|arw|srf|sr2|orf|rw2|pef|raf)$"
_SNIFF_SIZE = 64 * 1024
_TIFF_MAGICS = (b"II*\x00", b"MM\x00*")
_VENDOR_TIFF_MAGICS = {
    b"IIRO": "orf",
    b"IIRS": "orf",
    b"MMOR": "orf",
    b"IIU\x00": "rw2",
}
_TIFF_RAW_MAKES = (
    ("NIKON", "nef"),
    ("SONY", "arw"),
    ("PENTAX", "pef"),
    ("RICOH", "pef"),
)


def _tiff_raw_label(head):
    endian = "<" if head[:2] == b"II" else ">"
    (offset,) = struct.unpack_from(endian + "I", head, 4)
    (count,) = struct.unpack_from(endian + "H", head, offset)
    tags = {}
    for i in range(count):
        tag, typ, n, value = struct.unpack_from(
            endian + "HHI4s", head, offset + 2 + 12 * i
        )
        tags[tag] = (typ, n, value)
    if 0xC612 in tags:
        return "dng"

    def short(tag):
        entry = tags.get(tag)
        return (
            struct.unpack_from(endian + "H", entry[2])[0]
            if entry and entry[0] == 3
            else None
        )

    if 0x014A not in tags and short(0x0106) != 32803 and short(0x0103) != 65535:
        return None
    make = b""
    if (entry := tags.get(0x010F)) and entry[0] == 2:
        _, n, value = entry
        make = (
            value[:n] if n <= 4 else head[struct.unpack(endian + "I", value)[0] :][:n]
        )
    make = make.split(b"\x00", 1)[0].decode("latin-1").strip().upper()
    return next(
        (label for prefix, label in _TIFF_RAW_MAKES if make.startswith(prefix)), None
    )


def _raw_label(head):
    if head.startswith(b"FUJIFILMCCD-RAW"):
        return "raf"
    if head[4:12] == b"ftypcrx ":
        return "cr3"
    if head[:4] in _VENDOR_TIFF_MAGICS:
        return _VENDOR_TIFF_MAGICS[head[:4]]
    if head[:4] not in _TIFF_MAGICS:
        return None
    if head[8:10] == b"CR":
        return "cr2"
    try:
        return _tiff_raw_label(head)
    except struct.error:
        return None


def retype_raws(apps, schema_editor):
    File = apps.get_model("files", "File")
    ProcessingFailure = apps.get_model("files", "ProcessingFailure")
    db = schema_editor.connection.alias

    candidates = (
        File.objects.using(db)
        .filter(type__in=["tiff", "unknown"], name__iregex=_EXTENSIONS)
        .exclude(content="")
        .exclude(content__isnull=True)
    )
    for row in candidates.iterator():
        try:
            with row.content.open("rb") as handle:
                head = handle.read(_SNIFF_SIZE)
        except OSError as exc:
            logger.warning(
                "Cannot read %s to retype it: %s", scrub(row.content.name), exc
            )
            continue
        label = _raw_label(head)
        if label is None:
            continue
        File.objects.using(db).filter(pk=row.pk).update(
            type=label,
            category="image",
            mime_type=_MIME_TYPES[label],
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
