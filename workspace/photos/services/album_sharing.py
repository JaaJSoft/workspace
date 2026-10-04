"""Write side of album sharing: members, leaving, and public links.

Access is the caller's to check (the album action registry, through the
views); these functions keep the rows consistent and tell the people they
concern (``album_notifications``).
"""

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from ..models import Album, AlbumLink, AlbumShare
from .album_notifications import notify_removed, notify_role_changed, notify_shared
from .albums import remove_items as remove_album_items


def _target_filter(*, user=None, group=None, project=None):
    """The share column naming the one target given; ValueError otherwise."""
    targets = {
        "shared_with": user,
        "shared_with_group": group,
        "shared_with_project": project,
    }
    given = {column: value for column, value in targets.items() if value is not None}
    if len(given) != 1:
        raise ValueError("exactly one share target is required")
    return given


def share_album(album, *, role, acting_user, user=None, group=None, project=None):
    """Make the target a member of *album* with *role*, or change its role.

    Returns ``(share, created)``. The people the share reaches are told,
    on creation as on a role change. Members only hear about photos added
    after their invitation: the first share starts the album's
    notification clock.
    """
    target = _target_filter(user=user, group=group, project=project)
    with transaction.atomic():
        share, created = AlbumShare.objects.get_or_create(
            album=album,
            **target,
            defaults={"role": role, "shared_by": acting_user},
        )
        changed = not created and share.role != role
        if changed:
            share.role = role
            share.save(update_fields=["role"])
        if album.notified_at is None:
            Album.objects.filter(pk=album.pk, notified_at__isnull=True).update(
                notified_at=timezone.now()
            )
    if created:
        notify_shared(share, acting_user=acting_user)
    elif changed:
        notify_role_changed(share, acting_user=acting_user)
    return share, created


def unshare_album(album, *, acting_user, user=None, group=None, project=None):
    """Take the target out of *album*'s members; returns whether it was one.

    What they contributed stays: an item is served while its contributor
    can share the file, member or not (see ``queries.vouched_items_q``).
    """
    target = _target_filter(user=user, group=group, project=project)
    share = (
        AlbumShare.objects.select_related("album", "shared_with_project")
        .filter(album=album, **target)
        .first()
    )
    if share is None:
        return False
    share.delete()
    notify_removed(share, acting_user=acting_user)
    return True


def leave_album(album, user, *, remove_items=False):
    """Give up *user*'s own share of *album*; returns whether they had one.

    With *remove_items* the photos they added leave the album with them.
    """
    with transaction.atomic():
        deleted, _ = AlbumShare.objects.filter(album=album, shared_with=user).delete()
        if deleted and remove_items:
            remove_album_items(
                album,
                album.items.filter(added_by=user).values_list("file_id", flat=True),
            )
    return bool(deleted)


def create_link(
    album, *, acting_user, password="", expires_at=None, allow_download=False
):
    """A new public link to *album*. The password is stored hashed."""
    return AlbumLink.objects.create(
        album=album,
        created_by=acting_user,
        password=make_password(password) if password else "",
        expires_at=expires_at,
        allow_download=allow_download,
    )


def revoke_link(album, link_uuid):
    """Delete *album*'s link *link_uuid*; returns whether there was one."""
    deleted, _ = AlbumLink.objects.filter(album=album, uuid=link_uuid).delete()
    return bool(deleted)
