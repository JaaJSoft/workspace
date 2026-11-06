"""E-books the reader opens, and how a file is recognised as one.

Magika knows EPUB and nothing else of the family. It reads a Mobipocket or
Kindle book as unknown data, a FictionBook as XML, and a comic book archive
or a zipped FictionBook as a plain zip. Each is told by what its container
says about itself:

- the Mobipocket family (MOBI, AZW, AZW3) is a Palm database whose type and
  creator read ``BOOKMOBI``, the signature foliate-js checks too; the
  extension only says which of the three names the file carries;
- a FictionBook is XML whose root element is ``FictionBook``;
- a comic book is a zip named ``.cbz`` that holds at least one image, and a
  zipped FictionBook a zip named ``.fbz`` or ``.fb2.zip`` that holds a
  ``.fb2``: a zip of anything says nothing about being a book.
"""

import re
import zipfile

# Label -> MIME type, extensions, description. The MIME types are the
# shared-mime-info names, which foliate-js also reads to tell the formats
# apart.
EBOOK_FORMATS = {
    "mobi": ("application/x-mobipocket-ebook", ["mobi", "prc"], "Mobipocket e-book"),
    "azw": ("application/vnd.amazon.ebook", ["azw"], "Kindle e-book"),
    "azw3": ("application/vnd.amazon.mobi8-ebook", ["azw3"], "Kindle KF8 e-book"),
    "fb2": ("application/x-fictionbook+xml", ["fb2"], "FictionBook e-book"),
    "fbz": ("application/x-zip-compressed-fb2", ["fbz"], "Zipped FictionBook e-book"),
    "cbz": ("application/vnd.comicbook+zip", ["cbz"], "Comic book archive"),
}
# Every format the e-book reader opens, EPUB included: Magika knows that one.
EBOOK_LABELS = frozenset({"epub", *EBOOK_FORMATS})

_PALM_TYPE_CREATOR = slice(60, 68)
_MOBI_SIGNATURE = b"BOOKMOBI"
_MOBI_LABEL_BY_EXTENSION = {"azw": "azw", "azw3": "azw3"}
# The root element follows the XML declaration and, at most, a few comments
# or a doctype: past this, a file is not a FictionBook anyone wrote.
_SNIFF_SIZE = 4096
_FB2_ROOT = re.compile(rb"<(?:[\w.-]+:)?FictionBook[\s>]")
# Content labels a FictionBook comes out as.
_XML_LIKE_LABELS = frozenset({"xml", "html", "txt", "unknown"})
# A central directory past this many entries is not read to find one image.
_MAX_ZIP_ENTRIES = 10_000


def _extension(filename):
    _, dot, extension = (filename or "").rpartition(".")
    return extension.lower() if dot else ""


def _head(stream):
    position = stream.tell()
    try:
        return stream.read(_SNIFF_SIZE)
    finally:
        stream.seek(position)


def _zip_names(stream):
    """The entry names of the zip in *stream*, or None; its position is kept."""
    position = stream.tell()
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                return None
            return [info.filename for info in infos if not info.is_dir()]
    except zipfile.BadZipFile, OSError:
        return None
    finally:
        stream.seek(position)


def _holds_an_image(names):
    from .detection import get_label_info, label_from_name

    return any(
        get_label_info(label_from_name(name)).get("group") == "image" for name in names
    )


def _zipped_ebook_label(stream, filename):
    name = (filename or "").lower()
    if name.endswith(".cbz"):
        names = _zip_names(stream)
        return "cbz" if names and _holds_an_image(names) else None
    if name.endswith((".fbz", ".fb2.zip")):
        names = _zip_names(stream)
        return (
            "fbz" if names and any(n.lower().endswith(".fb2") for n in names) else None
        )
    return None


def ebook_label(content_label, stream, filename):
    """The e-book label of the file in *stream*, or None when it is no e-book
    Magika missed. *stream* must be seekable; its position is kept.
    """
    if content_label == "zip":
        return _zipped_ebook_label(stream, filename)
    head = _head(stream)
    if head[_PALM_TYPE_CREATOR] == _MOBI_SIGNATURE:
        return _MOBI_LABEL_BY_EXTENSION.get(_extension(filename), "mobi")
    if content_label in _XML_LIKE_LABELS and _FB2_ROOT.search(head):
        return "fb2"
    return None
