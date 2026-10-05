"""Public album links: resolving a token and its password.

The password flow is the file links' one: a correct password earns a signed
access token, valid an hour, that the visitor's later requests carry in the
query string. It is stateless, so no session is needed.
"""

from django.core import signing
from django.db.models import F
from django.utils import timezone

from ..models import AlbumLink

SIGNER = signing.TimestampSigner(salt="photo-album-link")
ACCESS_TOKEN_MAX_AGE = 3600


def find_link(token):
    """The link *token* names, expired or not, or None."""
    return (
        AlbumLink.objects.select_related("album", "album__owner", "created_by")
        .filter(token=token)
        .first()
    )


def access_token_for(link):
    """The access token a visitor gets for the right password."""
    return SIGNER.sign(link.token)


def has_access(link, access_token):
    """Whether *access_token* opens *link*: always for a link without a
    password, else only a token signed for this very link, not yet stale."""
    if not link.has_password:
        return True
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
