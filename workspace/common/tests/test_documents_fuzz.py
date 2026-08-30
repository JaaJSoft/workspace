"""Damaged documents must fail one predictable way, never a surprising one.

These parsers are pointed at whatever a user chose to upload, so the shape of
the input is not ours to assume. Enumerating the exceptions a corrupt archive
can raise is a game that cannot be won by reading the code - zipfile, zlib and
pypdf each have their own idea of what a damaged byte means, and which one
fires depends on which byte it was. Mutating a real document and asserting the
boundary holds is how that gets checked instead.

The corpus is seeded, so a failure here reproduces exactly.
"""

from __future__ import annotations

import io
import logging
import random
import zipfile

from django.test import SimpleTestCase

from workspace.common.documents.extraction import extract_document
from workspace.common.tests.office_fixtures import make_docx
from workspace.common.tests.pdf_fixtures import make_pdf

CASES = 120
SEED = 20260906


def _mutations(source: bytes, count: int) -> list[bytes]:
    """Damage *source* the four ways a stored blob actually gets damaged."""
    rng = random.Random(SEED)
    cases = []
    for index in range(count):
        data = bytearray(source)
        strategy = index % 4
        if strategy == 0:
            data = data[: rng.randint(1, len(data))]
        elif strategy == 1:
            for _ in range(rng.randint(1, 40)):
                position = rng.randrange(len(data))
                data[position] ^= 1 << rng.randrange(8)
        elif strategy == 2:
            position = rng.randrange(len(data))
            data[position:position] = rng.randbytes(rng.randint(1, 200))
        else:
            for _ in range(rng.randint(1, 10)):
                position = rng.randrange(max(1, len(data) - 4))
                data[position : position + 4] = rng.randbytes(4)
        cases.append(bytes(data))
    return cases


class OfficeFuzzTests(SimpleTestCase):
    def test_a_damaged_archive_only_ever_raises_value_error(self):
        source = make_docx(["The kraken sleeps."], table=[["alpha", "beta"]])
        for index, payload in enumerate(_mutations(source, CASES)):
            with self.subTest(case=index):
                try:
                    extract_document(payload, max_chars=100_000)
                except ValueError:
                    pass

    def test_the_body_stays_bounded_whatever_the_input(self):
        source = make_docx(["the kraken sleeps beneath the waves"] * 200)
        for index, payload in enumerate(_mutations(source, CASES)):
            with self.subTest(case=index):
                try:
                    body = extract_document(payload, max_chars=500).text
                except ValueError:
                    continue
                self.assertLessEqual(len(body), 500)


class ZipBombTests(SimpleTestCase):
    def test_a_member_that_inflates_enormously_stays_within_the_ceiling(self):
        # Written in chunks rather than one buffer: holding the inflated size
        # in memory to build the fixture would be the very cost being tested.
        inflated = 256 * 1024 * 1024
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            with archive.open("word/document.xml", "w") as member:
                member.write(b"<w:document><w:body>")
                for _ in range(inflated // (1024 * 1024)):
                    member.write(b"A" * (1024 * 1024))
                member.write(b"</w:body></w:document>")

        payload = buffer.getvalue()
        self.assertLess(len(payload), inflated // 100, "fixture is not a bomb")
        try:
            body = extract_document(payload, max_chars=1000).text
        except ValueError:
            return
        self.assertLessEqual(len(body), 1000)


class PdfFuzzTests(SimpleTestCase):
    def setUp(self):
        # A damaged document is exactly what the parser warns about.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_a_damaged_pdf_only_ever_raises_value_error(self):
        source = make_pdf(["quarterly budget", "second page"])
        for index, payload in enumerate(_mutations(source, CASES)):
            with self.subTest(case=index):
                try:
                    body = extract_document(payload, max_chars=100_000).text
                except ValueError:
                    continue
                self.assertLessEqual(len(body), 100_000)
