"""Hand-built PDF documents for the extraction tests.

Writing the bytes out is what keeps the suite free of a PDF-authoring
dependency, and a fixture whose every object is visible is also the only way
to build the degenerate cases — a page with no text at all, a truncated file.
"""


def make_pdf(page_texts: list[str], *, padding_bytes: int = 0) -> bytes:
    """Build a minimal uncompressed PDF with one Helvetica line per page.

    *padding_bytes* adds an unreferenced stream, the cheap way to reach the
    size of a document whose weight is images rather than words.
    """
    page_ids = [4 + 2 * i for i in range(len(page_texts))]
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_texts)),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, page_texts, strict=True):
        stream = b"BT /F1 24 Tf 72 700 Td (%s) Tj ET" % text.encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % (page_id + 1)
        )
        objects.append(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
    if padding_bytes:
        objects.append(
            b"<< /Length %d >>\nstream\n%s\nendstream"
            % (padding_bytes, b"\0" * padding_bytes)
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)

    startxref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        startxref,
    )
    return bytes(out)
