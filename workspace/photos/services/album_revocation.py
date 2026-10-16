"""What a member handed out of an album stops when they lose the right to.

A public link or a share someone made on an album keeps handing the album
out only while its maker may still share it: the owner of a personal album,
any member of a group album's group, a manager. Whoever stops being one -
unshared, leaving, downgraded, out of the group or the project that let them
in - loses the links and the shares they made there (the files rule, see
``files.services.sharing.revoke_group_share_links``).
"""

from functools import reduce
from operator import or_

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from ..actions import AlbumActionRegistry
from ..models import Album, AlbumLink, AlbumShare
from ..queries import album_roles, get_album_role
from .album_notifications import notify_revoked

User = get_user_model()


def can_share(user, album, *, role=None):
    """Whether *user* may hand *album* out: the album's ``share`` action.

    *role* is the user's role in the album when the caller resolved it
    already; it is looked up otherwise.
    """
    if role is None:
        role = get_album_role(user, album)
    return AlbumActionRegistry.is_action_available("share", user, album, role=role)


def _lapsed_makers(pairs):
    """The ``(album_id, user_id)`` pairs of *pairs* whose user may no longer
    share that album, in a few queries per user."""
    albums = Album.objects.in_bulk({album_id for album_id, _ in pairs})
    users = User.objects.in_bulk({user_id for _, user_id in pairs})
    by_user = {}
    for album_id, user_id in pairs:
        by_user.setdefault(user_id, []).append(albums[album_id])
    lapsed = set()
    for user_id, their_albums in by_user.items():
        user = users[user_id]
        roles = album_roles(user, their_albums)
        lapsed.update(
            (album.uuid, user_id)
            for album in their_albums
            if not can_share(user, album, role=roles.get(album.uuid))
        )
    return lapsed


def _made_by(lapsed, maker_field):
    return reduce(
        or_,
        (
            Q(album_id=album_id, **{maker_field: user_id})
            for album_id, user_id in lapsed
        ),
    )


def revoke_lapsed_sharing(*, album_ids=None, user_ids=None):
    """Delete the links and the shares made on *album_ids* or by *user_ids*
    (or both) by people who may no longer share the album they made them on.

    A revoked share may cost the people it reached their own right to share,
    so the albums it was on are checked again until nothing more lapses. The
    people a revoked share reached are told. Returns ``(links, shares)``,
    the numbers of rows deleted.
    """
    if album_ids is None and user_ids is None:
        raise ValueError("albums or users are required")
    revoked_links = revoked_shares = 0
    while True:
        links = AlbumLink.objects.all()
        shares = AlbumShare.objects.filter(shared_by__isnull=False)
        if album_ids is not None:
            links = links.filter(album_id__in=album_ids)
            shares = shares.filter(album_id__in=album_ids)
        if user_ids is not None:
            links = links.filter(created_by_id__in=user_ids)
            shares = shares.filter(shared_by_id__in=user_ids)
        pairs = set(links.values_list("album_id", "created_by_id")) | set(
            shares.values_list("album_id", "shared_by_id")
        )
        lapsed = _lapsed_makers(pairs) if pairs else set()
        if not lapsed:
            return revoked_links, revoked_shares
        with transaction.atomic():
            deleted, _ = AlbumLink.objects.filter(
                _made_by(lapsed, "created_by_id")
            ).delete()
            gone = list(
                AlbumShare.objects.filter(
                    _made_by(lapsed, "shared_by_id")
                ).select_related("album", "shared_by", "shared_with_project")
            )
            AlbumShare.objects.filter(pk__in=[share.pk for share in gone]).delete()
        revoked_links += deleted
        revoked_shares += len(gone)
        for share in gone:
            notify_revoked(share)
        if not gone:
            return revoked_links, revoked_shares
        album_ids, user_ids = {share.album_id for share in gone}, None
