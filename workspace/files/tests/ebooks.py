"""Minimal EPUB 3 books for tests, built with zipfile.

The container follows the OCF rules a reader and Magika rely on: the
``mimetype`` entry first and stored uncompressed, then ``META-INF/container.xml``
pointing at the package document.
"""

from __future__ import annotations

import io
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
