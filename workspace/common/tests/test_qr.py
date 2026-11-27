import io
import math
import re

from django.test import SimpleTestCase
from PIL import Image

from workspace.common.qr import (
    DARK,
    DOT_RADIUS,
    FINDER_SIZE,
    LIGHT,
    QR_IMAGE_KINDS,
    QRTooLarge,
    make_qr,
    qr_png,
    qr_response,
    qr_svg,
)
from workspace.common.tests.qr import read_qr

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
LINK = "https://workspace.example.test/files/shared/LRsZJ2YfJ_4UYSReoXRbpnAJ-0fOOYTP"
CARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nN:Martin;Alice;;;\r\nFN:Alice Martin\r\n"
    "ORG:Acme\r\nEMAIL;TYPE=WORK:alice@acme.test\r\nTEL;TYPE=CELL:+33612345678\r\n"
    "ADR;TYPE=WORK:;;3 rue de la Paix;Paris;;75002;France\r\nEND:VCARD\r\n"
)
# As much as a QR code holds at the default error level, or close to it.
LARGEST = "".join(f"TEL;TYPE=CELL:+336123456{index:02d}\r\n" for index in range(82))
BORDER = 4


def opened(png):
    return Image.open(io.BytesIO(png))


def in_a_finder_pattern(x, y, size):
    """Whether a module belongs to one of the three corner patterns, by the
    QR layout itself rather than by the drawing under test."""
    far = size - FINDER_SIZE
    return (
        (x < FINDER_SIZE or x >= far)
        and (y < FINDER_SIZE or y >= far)
        and not (x >= far and y >= far)
    )


class MakeQRTests(SimpleTestCase):
    def test_encodes_text(self):
        code = make_qr("hello")
        self.assertGreaterEqual(code.version, 1)

    def test_error_level_is_a_floor_never_a_ceiling(self):
        """segno spends the room a version has left on redundancy, so the
        level it settles on is at or above the one asked for."""
        self.assertIn(make_qr("hello").error, ("M", "Q", "H"))
        self.assertEqual(make_qr("hello", error_level="h").error, "H")

    def test_payload_past_every_version_is_refused(self):
        with self.assertRaises(QRTooLarge):
            make_qr("x" * 4000)


class ReadBackTests(SimpleTestCase):
    """The drawing is ours, so the proof is somebody else's reader."""

    def assertReadsBack(self, text, **options):
        self.assertEqual(read_qr(qr_png(make_qr(text), **options)), text)

    def test_a_link_reads_back(self):
        self.assertReadsBack(LINK)

    def test_a_contact_card_reads_back(self):
        self.assertReadsBack(CARD)

    def test_a_code_reads_back_at_the_size_of_a_thumbnail(self):
        self.assertReadsBack(LINK, scale=3)

    def test_the_largest_code_reads_back(self):
        code = make_qr(LARGEST)
        self.assertEqual(code.version, 40)
        self.assertEqual(read_qr(qr_response(code, "png", "card").content), LARGEST)


class RenderTests(SimpleTestCase):
    def setUp(self):
        self.code = make_qr(LINK)
        self.size = len(self.code.matrix)

    def test_svg_is_an_svg_document(self):
        svg = qr_svg(self.code)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)

    def test_png_carries_its_magic_number(self):
        self.assertTrue(qr_png(self.code).startswith(PNG_MAGIC))

    def test_scale_is_the_pixels_a_module_takes(self):
        modules = self.size + 2 * BORDER
        self.assertIn(
            f'width="{modules * 5}" height="{modules * 5}"', qr_svg(self.code, scale=5)
        )
        self.assertEqual(opened(qr_png(self.code, scale=5)).size, (modules * 5,) * 2)

    def test_border_is_a_quiet_zone_nothing_is_drawn_in(self):
        scale = 6
        image = opened(qr_png(self.code, scale=scale, border=BORDER))
        symbol = (BORDER * scale, BORDER * scale, image.width - BORDER * scale)
        left, top, right = symbol
        outside = [
            image.getpixel((x, y))
            for x in range(image.width)
            for y in range(image.height)
            if not (left <= x < right and top <= y < right)
        ]
        self.assertEqual(set(outside), {255})
        self.assertEqual(
            opened(qr_png(self.code, scale=scale, border=0)).width, self.size * scale
        )

    def test_modules_are_dark_on_a_light_field(self):
        """An inverted code is refused by most scanners, whatever the page
        theme around it."""
        self.assertEqual((DARK, LIGHT), ("#000000", "#ffffff"))
        self.assertEqual(
            set(re.findall(r'fill="([^"]+)"', qr_svg(self.code))), {DARK, LIGHT}
        )
        self.assertEqual(
            sorted({0, 255} & set(opened(qr_png(self.code)).getdata())), [0, 255]
        )

    def test_the_middle_of_every_module_carries_its_colour(self):
        """What a scanner samples. The corners of a finder pattern are rounded
        off, so those patterns are checked by the run a scanner looks for."""
        scale = 10
        image = opened(qr_png(self.code, scale=scale, border=BORDER))

        def middle(x, y):
            return image.getpixel(
                (int((x + BORDER + 0.5) * scale), int((y + BORDER + 0.5) * scale))
            )

        for y, row in enumerate(self.code.matrix):
            for x, dark in enumerate(row):
                if not in_a_finder_pattern(x, y, self.size):
                    self.assertEqual(middle(x, y) < 128, bool(dark), (x, y))

        far = self.size - FINDER_SIZE
        for ox, oy in ((0, 0), (far, 0), (0, far)):
            across = [middle(ox + step, oy + 3) < 128 for step in range(FINDER_SIZE)]
            down = [middle(ox + 3, oy + step) < 128 for step in range(FINDER_SIZE)]
            self.assertEqual(across, [True, False, True, True, True, False, True])
            self.assertEqual(down, across)

    def test_the_svg_draws_one_dot_in_each_dark_module(self):
        dots = [
            (math.floor(float(x) + DOT_RADIUS) - BORDER, math.floor(float(y)) - BORDER)
            for x, y in re.findall(
                r"M([\d.]+) ([\d.]+)a", qr_svg(self.code, border=BORDER)
            )
        ]
        expected = {
            (x, y)
            for y, row in enumerate(self.code.matrix)
            for x, dark in enumerate(row)
            if dark and not in_a_finder_pattern(x, y, self.size)
        }
        self.assertEqual(len(dots), len(expected))
        self.assertEqual(set(dots), expected)

    def test_the_svg_draws_the_three_finder_patterns(self):
        corners = re.findall(
            r'<rect x="(\d+)" y="(\d+)" width="7"', qr_svg(self.code, border=BORDER)
        )
        far = str(self.size - FINDER_SIZE + BORDER)
        near = str(BORDER)
        self.assertEqual(set(corners), {(near, near), (far, near), (near, far)})


class ResponseTests(SimpleTestCase):
    def setUp(self):
        self.code = make_qr(LINK)

    def test_the_two_kinds_a_code_is_served_as(self):
        self.assertEqual(QR_IMAGE_KINDS, ("svg", "png"))

    def test_svg_is_served_as_an_svg_document(self):
        response = qr_response(self.code, "svg", "card")
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertEqual(response.content.decode(), qr_svg(self.code))

    def test_png_is_served_larger_than_the_default_for_print(self):
        response = qr_response(self.code, "png", "card")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(PNG_MAGIC))
        self.assertGreater(
            opened(response.content).width, opened(qr_png(self.code)).width
        )

    def test_shown_in_the_page_and_named_for_a_save(self):
        response = qr_response(self.code, "png", "Alice Martin")
        self.assertEqual(
            response["Content-Disposition"], 'inline; filename="Alice Martin.png"'
        )

    def test_a_name_cannot_close_the_filename_it_sits_in(self):
        response = qr_response(self.code, "svg", 'a"; attachment; x="b')
        self.assertEqual(
            response["Content-Disposition"],
            'inline; filename="a\\"; attachment; x=\\"b.svg"',
        )
