"""Internal helpers for reading file content."""

from ..models import File
from .text_extraction import file_text


def read_text_content(file_obj, *, max_bytes=32_768):
    """Read and return the text content of a file.

    A reader gets its own ceiling and the same parser as the index, so the two
    never disagree about which files hold words. The ceiling counts characters
    of prose rather than bytes of the blob, since a compressed document's size
    says nothing about how much there is to read.
    """
    return file_text(file_obj, max_chars=max_bytes)


def read_image_content(file_obj, *, max_bytes=10_485_760):
    """Read and return the raw bytes of an image file."""
    if file_obj.node_type != File.NodeType.FILE:
        return None, None
    if not file_obj.content or not file_obj.content.name:
        return None, None
    mime = file_obj.mime_type or ""
    if not mime.startswith("image/"):
        return None, None
    try:
        with file_obj.content.open("rb") as fh:
            raw = fh.read(max_bytes)
        return raw, mime
    except OSError:
        return None, None
