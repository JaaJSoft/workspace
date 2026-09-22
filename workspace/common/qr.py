"""QR codes, rendered straight from segno.

Dark modules on a white background, always: a scanner looks for a dark
finder pattern on a light field and most refuse an inverted code outright,
so the theme the page is drawn in never reaches the image.
"""

import io

import segno
from segno import DataOverflowError

# Recovers ~15% of a damaged symbol. Level L would pack the same payload into
# a smaller grid, but a code read off a screen at an angle needs the slack.
DEFAULT_ERROR_LEVEL = "m"

DARK = "#000000"
LIGHT = "#ffffff"


class QRTooLarge(ValueError):
    """The payload is past what the largest QR code can hold."""


def make_qr(text, *, error_level=DEFAULT_ERROR_LEVEL):
    """A ``segno.QRCode`` for the text. Raises ``QRTooLarge`` when it does
    not fit - version 40 is the end of the road, there is no larger symbol."""
    try:
        return segno.make(text, error=error_level, micro=False)
    except DataOverflowError as exc:
        raise QRTooLarge(str(exc)) from exc


def _render(qr, kind, scale, border):
    buffer = io.BytesIO()
    qr.save(buffer, kind=kind, scale=scale, border=border, dark=DARK, light=LIGHT)
    return buffer.getvalue()


def qr_svg(qr, *, scale=8, border=4):
    """The code as an SVG document, as text."""
    return _render(qr, "svg", scale, border).decode("utf-8")


def qr_png(qr, *, scale=8, border=4):
    """The code as PNG bytes, for a download that lands in a gallery or a
    print job rather than a browser."""
    return _render(qr, "png", scale, border)
