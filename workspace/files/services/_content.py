"""Internal helpers for reading file content."""

from ..models import File


def read_text_content(file_obj, *, max_bytes=32_768):
    """Read and return the text content of a file."""
    if file_obj.node_type != File.NodeType.FILE:
        return None
    if not file_obj.content or not file_obj.content.name:
        return None
    try:
        with file_obj.content.open("rb") as fh:
            raw = fh.read(max_bytes)
        return raw.decode("utf-8")
    except OSError, UnicodeDecodeError:
        return None


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
