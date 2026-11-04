"""Filters other modules put between a file's bytes and someone else.

``files`` serves the bytes and owns the registry; a module registers from its
own ``AppConfig.ready()``, so the dependency points at ``files`` and never
back. A filter is called with ``(file_obj, reader, handle)``: *reader* is the
user the bytes go to, None for a visitor through a public link. It answers
a file-like object to read instead of *handle*, or None to leave it.

What a filter answers must keep the size: the endpoints announce it up
front, serve Range requests from it and stream zips that declared it.
"""

_filters = []


def register_content_filter(content_filter):
    _filters.append(content_filter)


def served_handle(file_obj, reader, handle):
    """*handle*, *file_obj*'s open content, as *reader* gets to read it."""
    for content_filter in _filters:
        handle = content_filter(file_obj, reader, handle) or handle
    return handle


def open_served(file_obj, reader):
    """*file_obj*'s content opened for *reader*: see :func:`served_handle`."""
    return served_handle(file_obj, reader, file_obj.content.open("rb"))
