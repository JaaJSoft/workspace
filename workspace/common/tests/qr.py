"""Reading a QR code back in a test, with a decoder that is not ours.

``workspace/common/qr.py`` draws its codes itself. Comparing what it draws
with what it draws says nothing about whether a phone reads the result, so
the tests hand the image to an independent reader.
"""

import io

import zxingcpp
from PIL import Image


def read_qr(image):
    """The text of the QR code in *image*, the bytes of a PNG, or None when
    the reader finds no code in it."""
    found = zxingcpp.read_barcodes(
        Image.open(io.BytesIO(image)), formats=zxingcpp.BarcodeFormat.QRCode
    )
    return found[0].text if found else None
