"""Downloads served straight from the store, when it signs URLs."""

from django.http import HttpResponseRedirect

from ..metrics import FILES_DOWNLOAD_BYTES


def signed_redirect(request, file_obj, *, attachment):
    """A redirect to *file_obj*'s blob on its store, or None to serve it here.

    The URL carries the content type and the disposition the app would have
    sent. Whoever holds it can fetch the blob until it expires, so only call
    this once access and quarantine have been checked.
    """
    url = file_obj.content.storage.signed_url(
        file_obj.content.name,
        filename=file_obj.name,
        attachment=attachment,
        content_type=file_obj.mime_type,
    )
    if url is None:
        return None
    response = HttpResponseRedirect(url)
    # The URL expires: a cache must never hand out a stale one.
    response["Cache-Control"] = "private, no-store"
    if file_obj.size and "HTTP_RANGE" not in request.META:
        FILES_DOWNLOAD_BYTES.inc(file_obj.size)
    return response
