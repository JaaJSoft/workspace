"""QR codes: segno encodes the text, this module draws the symbol.

segno only draws square modules. Here a module is a dot and the three
finder patterns are rounded squares, the same shapes in the SVG a page shows
and in the PNG a download hands over.

Dark on white, always: a scanner looks for a dark finder pattern on a light
field and most refuse an inverted code outright, so the theme the page is
drawn in never reaches the image.
"""

import io

import segno
from django.http import HttpResponse
from PIL import Image, ImageColor, ImageDraw
from segno import DataOverflowError

from .http_ranges import safe_filename

# Recovers ~15% of a damaged symbol. Level L would pack the same payload into
# a smaller grid, but a code read off a screen at an angle needs the slack.
DEFAULT_ERROR_LEVEL = "m"

DARK = "#000000"
LIGHT = "#ffffff"

# Every length below is in modules, the unit a QR code is laid out in.

# Narrower than its cell: touching dots read as a bar, and a scanner samples
# the middle of a cell, which the dot still covers.
DOT_RADIUS = 0.44

FINDER_SIZE = 7
# The squares a finder pattern is made of, outermost first: how far each sits
# inside the pattern, its corner radius and its colour. A scanner finds the
# pattern by the 1:1:3:1:1 run of dark and light through its middle, which
# rounding the corners leaves alone.
FINDER_SQUARES = ((0, 2.2, DARK), (1, 1.4, LIGHT), (2, 1.0, DARK))

# A dot has no straight edge to land on the pixel grid, so the PNG is drawn
# this many times larger and averaged down. The canvas stays under
# PNG_CANVAS_LIMIT pixels a side: the largest code is drawn at its final
# size rather than on a canvas of tens of megabytes.
PNG_SUPERSAMPLING = 4
PNG_CANVAS_LIMIT = 4096


class QRTooLarge(ValueError):
    """The payload is past what the largest QR code can hold."""


def make_qr(text, *, error_level=DEFAULT_ERROR_LEVEL):
    """A ``segno.QRCode`` for the text. Raises ``QRTooLarge`` when it does
    not fit - version 40 is the end of the road, there is no larger symbol."""
    try:
        return segno.make(text, error=error_level, micro=False)
    except DataOverflowError as exc:
        raise QRTooLarge(str(exc)) from exc


def _finder_origins(size):
    """The top-left module of each finder pattern: every corner of the symbol
    but the bottom right, whatever its version."""
    far = size - FINDER_SIZE
    return ((0, 0), (far, 0), (0, far))


def _dots(qr):
    """``(x, y)`` of every dark module outside the finder patterns."""
    size = len(qr.matrix)
    origins = _finder_origins(size)
    for y, row in enumerate(qr.matrix):
        for x, dark in enumerate(row):
            if dark and not any(
                ox <= x < ox + FINDER_SIZE and oy <= y < oy + FINDER_SIZE
                for ox, oy in origins
            ):
                yield x, y


def _finder_squares(qr):
    """``(x, y, side, radius, colour)`` of every square of the finder
    patterns, in the order to paint them."""
    for ox, oy in _finder_origins(len(qr.matrix)):
        for inset, radius, colour in FINDER_SQUARES:
            yield ox + inset, oy + inset, FINDER_SIZE - 2 * inset, radius, colour


def qr_svg(qr, *, scale=8, border=4):
    """The code as an SVG document, as text: *scale* pixels a module, inside
    a quiet zone of *border* modules."""
    side = len(qr.matrix) + 2 * border
    diameter = 2 * DOT_RADIUS
    # One path for every dot: a page showing a large code gets a single
    # element rather than thousands.
    dots = "".join(
        f"M{x + border + 0.5 - DOT_RADIUS:g} {y + border + 0.5:g}"
        f"a{DOT_RADIUS} {DOT_RADIUS} 0 1 0 {diameter} 0"
        f"a{DOT_RADIUS} {DOT_RADIUS} 0 1 0 -{diameter} 0"
        for x, y in _dots(qr)
    )
    squares = "".join(
        f'<rect x="{x + border}" y="{y + border}" width="{square}" '
        f'height="{square}" rx="{radius}" fill="{colour}"/>'
        for x, y, square, radius, colour in _finder_squares(qr)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side * scale}" '
        f'height="{side * scale}" viewBox="0 0 {side} {side}">'
        f'<rect width="{side}" height="{side}" fill="{LIGHT}"/>'
        f'<path d="{dots}" fill="{DARK}"/>'
        f"{squares}</svg>"
    )


def qr_png(qr, *, scale=8, border=4):
    """The code as PNG bytes, for a download that lands in a gallery or a
    print job rather than a browser."""
    pixels = (len(qr.matrix) + 2 * border) * scale
    oversampling = max(1, min(PNG_SUPERSAMPLING, PNG_CANVAS_LIMIT // pixels))
    unit = scale * oversampling
    dark, light = ImageColor.getcolor(DARK, "L"), ImageColor.getcolor(LIGHT, "L")
    canvas = Image.new("L", (pixels * oversampling,) * 2, light)
    draw = ImageDraw.Draw(canvas)

    def box(x, y, width):
        """The pixels a square of modules covers, both ends included."""
        left, top = (x + border) * unit, (y + border) * unit
        return (left, top, left + width * unit - 1, top + width * unit - 1)

    inset = 0.5 - DOT_RADIUS
    for x, y in _dots(qr):
        draw.ellipse(box(x + inset, y + inset, 2 * DOT_RADIUS), fill=dark)
    for x, y, square, radius, colour in _finder_squares(qr):
        draw.rounded_rectangle(
            box(x, y, square),
            radius=radius * unit,
            fill=ImageColor.getcolor(colour, "L"),
        )

    buffer = io.BytesIO()
    canvas.reduce(oversampling).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# The SVG is what a page shows - it stays sharp at whatever size the page
# gives it - and the PNG is what a download hands to a gallery or a print
# job, so it carries its resolution in the file.
_IMAGE_KINDS = {
    "svg": (qr_svg, "image/svg+xml", 8),
    "png": (qr_png, "image/png", 12),
}
QR_IMAGE_KINDS = tuple(_IMAGE_KINDS)


def qr_response(qr, kind, name):
    """The code served as an image of *kind*, one of ``QR_IMAGE_KINDS``: shown
    in the page, and called ``<name>.<kind>`` once saved."""
    render, content_type, scale = _IMAGE_KINDS[kind]
    response = HttpResponse(render(qr, scale=scale), content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{safe_filename(name)}.{kind}"'
    return response
