from django.test import SimpleTestCase

from workspace.common.qr import DARK, LIGHT, QRTooLarge, make_qr, qr_png, qr_svg

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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


class RenderTests(SimpleTestCase):
    def setUp(self):
        self.code = make_qr("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alice\r\nEND:VCARD\r\n")

    def test_svg_is_an_svg_document(self):
        svg = qr_svg(self.code)
        self.assertIn("<svg", svg)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)

    def test_png_carries_its_magic_number(self):
        self.assertTrue(qr_png(self.code).startswith(PNG_MAGIC))

    def test_scale_drives_the_svg_size(self):
        small = qr_svg(self.code, scale=4, border=4)
        large = qr_svg(self.code, scale=8, border=4)
        self.assertNotEqual(small, large)
        self.assertIn('transform="scale(4)"', small)
        self.assertIn('transform="scale(8)"', large)

    def test_modules_are_dark_on_a_light_field(self):
        """An inverted code is refused by most scanners, whatever the page
        theme around it. segno shortens the hex it is handed, so the colours
        land in the document as #000 and #fff."""
        self.assertEqual((DARK, LIGHT), ("#000000", "#ffffff"))
        svg = qr_svg(self.code)
        self.assertIn('fill="#fff"', svg)
        self.assertIn('stroke="#000"', svg)
