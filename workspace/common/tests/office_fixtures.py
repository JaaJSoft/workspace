"""Hand-built OOXML and OpenDocument files for the extraction tests.

Writing the parts out is what keeps the suite free of an office-authoring
dependency, and a fixture whose every member is visible is also the only way
to build the degenerate cases - a part that is not XML, a member that
decompresses to far more than it claims, a container with no content at all.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"

_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def make_zip(members: dict[str, str | bytes], *, first: str | None = None) -> bytes:
    """Zip *members* verbatim. *first* is stored uncompressed, as ODF wants."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if first is not None:
            archive.writestr(zipfile.ZipInfo(first), members[first])
        for name, payload in members.items():
            if name != first:
                archive.writestr(name, payload)
    return buffer.getvalue()


def make_docx(paragraphs: list[str], *, extra: dict[str, str] | None = None) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs
    )
    members = {
        "word/document.xml": (
            f'{_DECLARATION}<w:document xmlns:w="{_W}"><w:body>{body}</w:body>'
            "</w:document>"
        )
    }
    members.update(extra or {})
    return make_zip(members)


def make_pptx(slides: list[list[str]]) -> bytes:
    members = {}
    for number, paragraphs in enumerate(slides, start=1):
        body = "".join(
            f"<a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p>" for text in paragraphs
        )
        members[f"ppt/slides/slide{number}.xml"] = (
            f'{_DECLARATION}<p:sld xmlns:p="{_P}" xmlns:a="{_A}">'
            f"<p:cSld><p:spTree><p:sp><p:txBody>{body}</p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>"
        )
    return make_zip(members)


def make_xlsx(
    *,
    shared_strings: list[str] = (),
    numbers: list[str] = (),
    inline_strings: list[str] = (),
) -> bytes:
    """A workbook whose one sheet uses all three ways a cell can hold a value."""
    items = "".join(f"<si><t>{escape(text)}</t></si>" for text in shared_strings)
    cells = [f'<c t="s"><v>{index}</v></c>' for index in range(len(shared_strings))]
    cells += [f"<c><v>{escape(value)}</v></c>" for value in numbers]
    cells += [
        f'<c t="inlineStr"><is><t>{escape(text)}</t></is></c>'
        for text in inline_strings
    ]
    return make_zip(
        {
            "xl/sharedStrings.xml": (f'{_DECLARATION}<sst xmlns="{_S}">{items}</sst>'),
            "xl/worksheets/sheet1.xml": (
                f'{_DECLARATION}<worksheet xmlns="{_S}"><sheetData>'
                f'<row r="1">{"".join(cells)}</row>'
                "</sheetData></worksheet>"
            ),
        }
    )


def make_odf(mime_type: str, paragraphs: list[str]) -> bytes:
    """An OpenDocument container of *mime_type*; odt, ods and odp only differ there."""
    body = "".join(f"<text:p>{escape(text)}</text:p>" for text in paragraphs)
    return make_zip(
        {
            "mimetype": mime_type,
            "content.xml": (
                f'{_DECLARATION}<office:document-content xmlns:office="{_OFFICE}"'
                f' xmlns:text="{_TEXT}"><office:body><office:text>{body}'
                "</office:text></office:body></office:document-content>"
            ),
        },
        first="mimetype",
    )
