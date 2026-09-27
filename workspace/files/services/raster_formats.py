"""The raster formats this deployment decodes, by Magika label.

Pillow reads most of them itself. The others are taught to it through its
plugin mechanism once per process (``FilesConfig.ready``), so every
``Image.open`` in the codebase - thumbnails, EXIF, faces, avatars - reads them
without knowing where the decoder came from. A new format is one entry in
``_PLUGINS``.
"""

# What Pillow's own wheels decode.
_BUILTIN_LABELS = frozenset({"jpeg", "png", "webp", "bmp", "tiff", "gif", "avif"})


def _register_heif():
    from pillow_heif import register_heif_opener

    register_heif_opener()


# Label -> the call that registers its Pillow opener.
_PLUGINS = {
    "heif": _register_heif,
}

RASTER_LABELS = _BUILTIN_LABELS | frozenset(_PLUGINS)


def register_decoders():
    """Register every plugin opener with Pillow; safe to call more than once."""
    for register in _PLUGINS.values():
        register()
