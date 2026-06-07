"""HTTP Range request helpers (RFC 7233).

Django's `FileResponse` does not honor `Range:` headers, which makes
`<video>`/`<audio>` playback fall back to a slow progressive download
with no seeking. These helpers let any view that serves binary content
opt into proper Range handling.

Single byte-range only (`bytes=start-end`, `bytes=start-`, `bytes=-N`).
Multi-range (comma-separated) is intentionally not supported - browsers
don't use it for media playback and the multipart/byteranges format
costs more code than it saves.

Typical usage:

    from workspace.common.http_ranges import serve_with_ranges

    def get(self, request, ...):
        ...
        return serve_with_ranges(
            request,
            file_handle=storage.open(path, 'rb'),
            file_size=meta.size,
            content_type=meta.mime,
            inline_filename=meta.name,
        )
"""

import re

# bytes=start-end, bytes=start-, bytes=-N. Multi-range is rejected.
# No internal `\s*` matches: outer whitespace is handled by .strip()
# at the call site, internal whitespace inside the directive is not
# allowed by RFC 7233 and the redundant `\s*` opens a polynomial
# backtracking surface (CodeQL py/polynomial-redos).
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_byte_range(range_header, file_size):
    """Parse a single 'bytes=start-end' Range header.

    Returns (start, end) inclusive, or None if the header is absent,
    malformed, or asks for a range that cannot be satisfied. Callers
    that receive None when the request did carry a Range header should
    reply 416 Range Not Satisfiable.
    """
    if not range_header or file_size <= 0:
        return None
    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if not start_s and not end_s:
        return None
    if not start_s:
        # Suffix form 'bytes=-N' -> last N bytes
        suffix = int(end_s)
        if suffix == 0:
            return None
        start = max(0, file_size - suffix)
        end = file_size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
    if start >= file_size or start > end:
        return None
    return start, min(end, file_size - 1)


def stream_range(file_handle, start, end, block_size=65536):
    """Yield chunks of file_handle from start to end inclusive, then close it.

    The handle is always closed when the generator is exhausted or
    garbage-collected, even if the client disconnects mid-stream.
    """
    try:
        file_handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = file_handle.read(min(block_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file_handle.close()


def safe_filename(name):
    """Sanitize a filename for inclusion in a Content-Disposition header.

    Strips CR/LF (header-injection vector) and backslash-escapes double
    quotes so the value cannot terminate the quoted-string parameter.
    """
    return (
        name.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "")
        .replace("\n", "")
    )


def serve_with_ranges(
    request,
    file_handle,
    file_size,
    content_type,
    inline_filename=None,
    attachment_filename=None,
    cache_control=None,
    etag=None,
    download_metric=None,
):
    """Serve a file with HTTP Range support.

    Returns the appropriate Django response:
    - 206 Partial Content with the requested slice when Range is honored
    - 416 Range Not Satisfiable when Range is malformed / out of bounds
    - 200 OK streaming the whole file otherwise (with Accept-Ranges set)

    The 206 path intentionally omits ETag: Django's
    ConditionalGetMiddleware would otherwise rewrite the partial into
    a 304 whenever the client sends both Range and If-None-Match
    (common from Chrome), starving the media element.

    `file_handle` must be an open binary file-like object. It will be
    closed exactly once - either when the response stream finishes or
    when the function exits early with a 416.

    Pass `inline_filename` for in-browser viewing (video/audio/images),
    `attachment_filename` to force a download. Pass one, not both.

    `download_metric` is an optional Prometheus counter (anything with
    an `inc(n)` method) that gets incremented with the number of bytes
    written to the response.
    """
    from django.http import FileResponse, HttpResponse, StreamingHttpResponse

    range_header = request.META.get("HTTP_RANGE")
    if range_header:
        parsed = parse_byte_range(range_header, file_size)
        if parsed is None:
            file_handle.close()
            resp = HttpResponse(status=416)
            resp["Content-Range"] = f"bytes */{file_size}"
            resp["Accept-Ranges"] = "bytes"
            return resp
        start, end = parsed
        response = StreamingHttpResponse(
            stream_range(file_handle, start, end),
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(end - start + 1)
        response["Accept-Ranges"] = "bytes"
        if inline_filename:
            response["Content-Disposition"] = (
                f'inline; filename="{safe_filename(inline_filename)}"'
            )
        elif attachment_filename:
            response["Content-Disposition"] = (
                f'attachment; filename="{safe_filename(attachment_filename)}"'
            )
        if cache_control:
            response["Cache-Control"] = cache_control
        if download_metric is not None:
            download_metric.inc(end - start + 1)
        return response

    response = FileResponse(
        file_handle,
        content_type=content_type,
        as_attachment=bool(attachment_filename),
        filename=attachment_filename or inline_filename,
    )
    if inline_filename and not attachment_filename:
        response["Content-Disposition"] = (
            f'inline; filename="{safe_filename(inline_filename)}"'
        )
    response["Accept-Ranges"] = "bytes"
    if cache_control:
        response["Cache-Control"] = cache_control
    if etag:
        response["ETag"] = etag
    if download_metric is not None and file_size:
        download_metric.inc(file_size)
    return response
