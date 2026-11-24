"""Public album links: resolving a token and its password.

The password flow is the file links' one: a correct password earns a signed
access token, valid an hour, that the visitor's later requests carry in a
cookie of that link. It is stateless, so no session is needed.
"""

from django.core import signing
from django.db.models import F
from django.utils import timezone

from workspace.common.link_access import access_cookie_name, set_access_cookie

from ..models import AlbumLink
from .album_revocation import can_share

SIGNER = signing.TimestampSigner(salt="photo-album-link")
ACCESS_TOKEN_MAX_AGE = 3600


def find_link(token):
    """The link *token* names, expired or not, or None.

    None too for a link whose maker may no longer share the album. Losing
    that right revokes their links (``album_revocation``), but only on the
    paths a hook watches: a share row deleted in bulk runs none. Asking here
    keeps such a link from handing the album out all the same.
    """
    link = (
        AlbumLink.objects.select_related("album", "album__owner", "created_by")
        .filter(token=token)
        .first()
    )
    if link is None or not can_share(link.created_by, link.album):
        return None
    return link


def _access_cookie(link):
    return access_cookie_name("album_access", link.token)


def grant_access(response, request, link):
    """Remember on *response* that the visitor gave *link*'s password."""
    set_access_cookie(
        response,
        request,
        _access_cookie(link),
        SIGNER.sign(link.token),
        ACCESS_TOKEN_MAX_AGE,
    )


def has_access(link, request):
    """Whether *request* may open *link*: always for a link without a
    password, else only with a token signed for this very link, not yet
    stale."""
    if not link.has_password:
        return True
    access_token = request.COOKIES.get(_access_cookie(link), "")
    if not access_token:
        return False
    try:
        value = SIGNER.unsign(access_token, max_age=ACCESS_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return False
    return value == link.token


def record_view(link):
    """Count one visit of the link's page."""
    AlbumLink.objects.filter(pk=link.pk).update(
        view_count=F("view_count") + 1, last_accessed_at=timezone.now()
    )
