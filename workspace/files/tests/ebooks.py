"""Minimal e-books for tests, one builder per format the reader opens.

Each follows the rules its readers rely on, and foliate-js opens all of them
(e2e/test_ebook_reader.py):

- EPUB: the OCF container, ``mimetype`` first and stored uncompressed, then
  ``META-INF/container.xml`` pointing at the package document;
- MOBI: a Palm database typed ``BOOKMOBI``, record 0 carrying the PalmDOC and
  MOBI headers, then uncompressed text records of HTML split by
  ``<mbp:pagebreak/>``;
- FB2: FictionBook 2.0 XML; FBZ: that XML zipped;
- CBZ: a zip of page images.
"""

from __future__ import annotations

import io
import struct
import zipfile
from html import escape

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>
"""


def epub_bytes(
    title: str = "Test Book",
    chapters: list[tuple[str, str]] | None = None,
    resources: dict[str, tuple[str, str]] | None = None,
) -> bytes:
    """An EPUB holding *chapters*, a list of ``(title, xhtml body)`` pairs.

    The bodies are inserted as they are, so a test can put markup - a script
    included - into a chapter. *resources* maps more files of the book, by
    their path next to the chapters, to ``(media type, content)``.
    """
    if chapters is None:
        chapters = [
            ("Chapter One", "<p>It was a bright cold day in April.</p>"),
            ("Chapter Two", "<p>The clocks were striking thirteen.</p>"),
        ]

    manifest = []
    spine = []
    nav_items = []
    files = {}
    for number, (chapter_title, body) in enumerate(chapters, start=1):
        name = f"chapter{number}.xhtml"
        manifest.append(
            f'<item id="c{number}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="c{number}"/>')
        nav_items.append(f'<li><a href="{name}">{escape(chapter_title)}</a></li>')
        files[f"OEBPS/{name}"] = _CHAPTER.format(title=escape(chapter_title), body=body)

    for number, (href, (media_type, content)) in enumerate(
        (resources or {}).items(), start=1
    ):
        manifest.append(
            f'<item id="r{number}" href="{href}" media-type="{media_type}"/>'
        )
        files[f"OEBPS/{href}"] = content

    files["OEBPS/nav.xhtml"] = _CHAPTER.format(
        title="Contents",
        body='<nav epub:type="toc"><ol>' + "".join(nav_items) + "</ol></nav>",
    )
    files["OEBPS/content.opf"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000000</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    {"".join(manifest)}
  </manifest>
  <spine>{"".join(spine)}</spine>
</package>
"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr(
            "META-INF/container.xml", _CONTAINER, compress_type=zipfile.ZIP_DEFLATED
        )
        for name, content in files.items():
            archive.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


_DEFAULT_CHAPTERS = [
    ("Chapter One", "It was a bright cold day in April."),
    ("Chapter Two", "The clocks were striking thirteen."),
]

_MOBI_RECORD_SIZE = 4096
_MOBI_HEADER_LENGTH = 232
_NO_INDEX = 0xFFFFFFFF


def mobi_bytes(
    title: str = "Test Book", chapters: list[tuple[str, str]] | None = None
) -> bytes:
    """A Mobipocket book (MOBI 6, uncompressed) of *chapters*, a list of
    ``(title, paragraph text)`` pairs."""
    chapters = chapters or _DEFAULT_CHAPTERS
    body = "<mbp:pagebreak/>".join(
        f"<h1>{escape(name)}</h1><p>{escape(text)}</p>" for name, text in chapters
    )
    text = f"<html><head></head><body>{body}</body></html>".encode()
    text_records = [
        text[i : i + _MOBI_RECORD_SIZE] for i in range(0, len(text), _MOBI_RECORD_SIZE)
    ]
    end_of_records = len(text_records) + 1

    title_bytes = title.encode()
    record0 = bytearray(16 + _MOBI_HEADER_LENGTH)
    # PalmDOC header: no compression, the text's length and records, no DRM.
    struct.pack_into(
        ">HHIHHH", record0, 0, 1, 0, len(text), len(text_records), _MOBI_RECORD_SIZE, 0
    )
    struct.pack_into(">4sIIII", record0, 16, b"MOBI", _MOBI_HEADER_LENGTH, 2, 65001, 1)
    struct.pack_into(">I", record0, 36, 6)  # format version
    for offset in range(40, 80, 4):  # the dictionary indexes it has none of
        struct.pack_into(">I", record0, offset, _NO_INDEX)
    struct.pack_into(">I", record0, 80, end_of_records)  # first non-book record
    struct.pack_into(">II", record0, 84, len(record0), len(title_bytes))
    struct.pack_into(">I", record0, 92, 9)  # locale: English
    struct.pack_into(">I", record0, 108, end_of_records)  # first resource
    struct.pack_into(">I", record0, 244, _NO_INDEX)  # no NCX index
    record0 += title_bytes + b"\0" * (4 - len(title_bytes) % 4)

    records = [bytes(record0), *text_records, b"\xe9\x8e\r\n"]
    header = bytearray(78)
    header[:32] = title_bytes[:31].ljust(32, b"\0")
    header[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", header, 76, len(records))
    offset = len(header) + 8 * len(records) + 2
    record_list = bytearray()
    for index, record in enumerate(records):
        record_list += struct.pack(">II", offset, index * 2)
        offset += len(record)
    return bytes(header + record_list + b"\0\0" + b"".join(records))


def fb2_bytes(
    title: str = "Test Book", chapters: list[tuple[str, str]] | None = None
) -> bytes:
    """A FictionBook 2.0 book of *chapters*, ``(title, paragraph text)`` pairs."""
    chapters = chapters or _DEFAULT_CHAPTERS
    sections = "".join(
        f"<section><title><p>{escape(name)}</p></title><p>{escape(text)}</p></section>"
        for name, text in chapters
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
  <description>
    <title-info>
      <genre>prose</genre>
      <author><first-name>Test</first-name><last-name>Author</last-name></author>
      <book-title>{escape(title)}</book-title>
      <lang>en</lang>
    </title-info>
  </description>
  <body>{sections}</body>
</FictionBook>
""".encode()


def fbz_bytes(
    title: str = "Test Book", chapters: list[tuple[str, str]] | None = None
) -> bytes:
    """The FictionBook of :func:`fb2_bytes`, zipped."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("book.fb2", fb2_bytes(title, chapters))
    return buffer.getvalue()


def cbz_bytes(pages: int = 3, size: tuple[int, int] = (60, 90)) -> bytes:
    """A comic book of *pages* PNG pages, each a different shade."""
    from PIL import Image

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number in range(1, pages + 1):
            page = io.BytesIO()
            shade = 40 * number % 256
            Image.new("RGB", size, (shade, 120, 200)).save(page, format="PNG")
            archive.writestr(f"page{number:03}.png", page.getvalue())
    return buffer.getvalue()
