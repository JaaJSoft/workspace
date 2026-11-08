"""Retype the e-books that content detection took for something else.

Magika knows EPUB alone: a Mobipocket or Kindle book was stored as unknown
data, a FictionBook as XML, a comic book archive or a zipped FictionBook as a
plain zip, so none of them opened in the e-book reader. A row is retyped when
its name carries an e-book extension and its content is the e-book that
extension names, so a zip of anything renamed .cbz keeps its type. Files
uploaded without the extension stay as they are: the next upload of their
content is detected right.

The MIME types and the content checks are a copy of
``services/ebooks.py`` at the time of writing. A pinned viewer goes with the
old type: it was chosen for it. So does the search document's record: a
FictionBook's text is indexed under its new type, and dropping the record
makes the search catch-up read the file again.
"""

import logging
import re
import zipfile

from django.db import migrations

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

_MIME_TYPES = {
    "mobi": "application/x-mobipocket-ebook",
    "azw": "application/vnd.amazon.ebook",
    "azw3": "application/vnd.amazon.mobi8-ebook",
    "fb2": "application/x-fictionbook+xml",
    "fbz": "application/x-zip-compressed-fb2",
    "cbz": "application/vnd.comicbook+zip",
}
_NAME_PATTERN = r"\.(mobi|prc|azw|azw3|fb2|fbz|cbz|fb2\.zip)$"
_SNIFF_SIZE = 4096
_FB2_ROOT = re.compile(rb"<FictionBook[\s>]")
_IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".avif",
    ".jxl",
    ".svg",
)
_MAX_ZIP_ENTRIES = 10_000


def _zip_names(handle):
    try:
        with zipfile.ZipFile(handle) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                return []
            return [info.filename.lower() for info in infos if not info.is_dir()]
    except zipfile.BadZipFile:
        return []


def _label(name, handle):
    """The e-book label of the content in *handle*, or None."""
    name = name.lower()
    extension = name.rpartition(".")[2]
    if name.endswith(".cbz"):
        names = _zip_names(handle)
        return "cbz" if any(n.endswith(_IMAGE_EXTENSIONS) for n in names) else None
    if name.endswith((".fbz", ".fb2.zip")):
        names = _zip_names(handle)
        return "fbz" if any(n.endswith(".fb2") for n in names) else None
    head = handle.read(_SNIFF_SIZE)
    if head[60:68] == b"BOOKMOBI":
        return extension if extension in ("azw", "azw3") else "mobi"
    if extension == "fb2" and _FB2_ROOT.search(head):
        return "fb2"
    return None


def retype_ebooks(apps, schema_editor):
    File = apps.get_model("files", "File")
    SearchIndexState = apps.get_model("files", "SearchIndexState")
    db = schema_editor.connection.alias

    candidates = (
        File.objects.using(db)
        .filter(name__iregex=_NAME_PATTERN)
        .exclude(type__in=["epub", *_MIME_TYPES])
        .exclude(content="")
        .exclude(content__isnull=True)
    )
    for row in candidates.iterator():
        try:
            with row.content.open("rb") as handle:
                label = _label(row.name, handle)
        except OSError as exc:
            logger.warning(
                "Cannot read %s to retype it: %s", scrub(row.content.name), exc
            )
            continue
        if label is None:
            continue
        File.objects.using(db).filter(pk=row.pk).update(
            type=label,
            category="document",
            mime_type=_MIME_TYPES[label],
            viewer="",
        )
        SearchIndexState.objects.using(db).filter(file_id=row.pk).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0075_file_shortcut"),
    ]

    operations = [
        migrations.RunPython(retype_ebooks, migrations.RunPython.noop),
    ]
