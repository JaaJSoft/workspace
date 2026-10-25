"""The raster formats this deployment decodes, by Magika label.

Pillow reads most of them itself. The others are taught to it through its
plugin mechanism once per process (``FilesConfig.ready``), so every
``Image.open`` in the codebase - thumbnails, EXIF, faces, avatars - reads them
without knowing where the decoder came from. A new format is one entry in
``_PLUGINS``.
"""

from .raw_images import RAW_LABELS, register_raw_opener

# What Pillow's own wheels decode.
_BUILTIN_LABELS = frozenset({"jpeg", "png", "webp", "bmp", "tiff", "gif", "avif"})


def _register_heif():
    from pillow_heif import register_heif_opener

    register_heif_opener()


# Label -> the call that registers its Pillow opener.
_PLUGINS = {
    "heif": _register_heif,
    **dict.fromkeys(RAW_LABELS, register_raw_opener),
}

RASTER_LABELS = _BUILTIN_LABELS | frozenset(_PLUGINS)


def register_decoders():
    """Register every plugin opener with Pillow; safe to call more than once.

    In _PLUGINS order: the RAW opener tries whatever no plugin registered
    before it claims, so it comes last.
    """
    for register in dict.fromkeys(_PLUGINS.values()):
        register()
