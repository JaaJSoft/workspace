"""Telling album members what happened to them and to their albums.

Additions are announced once a burst is over, never photo by photo. The
first add of a burst elects one task ``PHOTOS_ALBUM_NOTIFY_WINDOW_SECONDS``
later (``cache.add``, the way share-link uploads are reported); the task
announces what was added since ``Album.notified_at``: one notification per
member naming whoever else added photos, merged into the member's unread one
about that album's additions.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from workspace.common.logging import scrub
from workspace.notifications.services.notifications import notify_many, notify_stream

from ..models import Album, AlbumShare

logger = logging.getLogger(__name__)

User = get_user_model()

# The stream of the additions, so their merge never rewrites the
# notification that announced the invitation.
ADDITIONS_STREAM = "additions"


def album_url(album):
    return reverse("photos_ui:album", args=[album.uuid])


def _count_label(count):
    return "1 photo" if count == 1 else f"{count} photos"


def share_recipients(share, *, exclude):
    """The active users *share* reaches, *exclude* left out."""
    from workspace.projects.queries import project_users

    if share.shared_with_id is not None:
        users = User.objects.filter(pk=share.shared_with_id)
    elif share.shared_with_group_id is not None:
        users = User.objects.filter(groups=share.shared_with_group_id)
    else:
        users = User.objects.filter(
            pk__in=[u.pk for u in project_users(share.shared_with_project)]
        )
    return list(users.filter(is_active=True).exclude(pk=exclude.pk))


def notify_shared(share, *, acting_user):
    """Tell everyone *share* reaches that they were added to the album."""
    album = share.album
    notify_many(
        recipients=share_recipients(share, exclude=acting_user),
        origin="photos",
        icon="book-image",
        title=f'{acting_user.username} shared the album "{album.title}" with you',
        body=f"You can {_ROLE_VERBS[share.role]}.",
        url=album_url(album),
        actor=acting_user,
        source=album,
    )


def notify_role_changed(share, *, acting_user):
    """Tell everyone *share* reaches what they may do now."""
    album = share.album
    notify_many(
        recipients=share_recipients(share, exclude=acting_user),
        origin="photos",
        icon="book-image",
        title=f'Your role in the album "{album.title}" is now {share.get_role_display().lower()}',
        body=f"You can {_ROLE_VERBS[share.role]}.",
        url=album_url(album),
        actor=acting_user,
        source=album,
    )


def notify_removed(share, *, acting_user):
    """Tell everyone *share* reached that it was taken away from them."""
    notify_many(
        recipients=share_recipients(share, exclude=acting_user),
        origin="photos",
        icon="book-image",
        title=f'{acting_user.username} removed you from the album "{share.album.title}"',
        actor=acting_user,
    )


_ROLE_VERBS = {
    AlbumShare.Role.VIEWER: "browse its photos",
    AlbumShare.Role.CONTRIBUTOR: "browse it and add your own photos",
    AlbumShare.Role.MANAGER: "browse it, add photos and manage it",
}


def additions_cache_key(album_uuid):
    return f"photos:album-additions:{album_uuid}"


def schedule_additions_notification(album):
    """Elect the task announcing the burst of additions *album* is in.

    Only the first add of a burst wins the election; the others find the
    key and leave it to the task already scheduled. An album nobody else
    can open has nobody to tell.
    """
    from ..tasks import notify_album_additions

    if album.group_id is None and not AlbumShare.objects.filter(album=album).exists():
        return
    window = settings.PHOTOS_ALBUM_NOTIFY_WINDOW_SECONDS
    # Five windows only bound a key the task never got to delete.
    if not cache.add(additions_cache_key(album.uuid), 1, timeout=window * 5):
        return
    try:
        notify_album_additions.apply_async(args=[str(album.uuid)], countdown=window)
    except Exception:
        cache.delete(additions_cache_key(album.uuid))
        logger.exception(
            "Could not schedule the additions notification of album %s",
            scrub(album.uuid),
        )


def _additions_title(album, people, count):
    """ "bob added 3 photos to "Trip"", naming at most two people."""
    names = [person.username for person in people]
    if len(names) == 1:
        who = names[0]
    elif len(names) == 2:
        who = f"{names[0]} and {names[1]}"
    else:
        who = f"{names[0]} and {len(names) - 1} others"
    return f'{who} added {_count_label(count)} to "{album.title}"'


def announce_additions(album_uuid):
    """Tell the members of the album what was added since the last time.

    Counts what every member sees (``queries.link_items``, the items their
    contributor vouches for), so nobody hears about a photo they cannot
    open.
    """
    from ..queries import album_members, link_items

    # Under the album's row lock, the one adds take: two runs of this task
    # (the election key expired or was evicted) never both read the same
    # notified_at and announce the same photos twice.
    with transaction.atomic():
        album = Album.objects.select_for_update().filter(uuid=album_uuid).first()
        if album is None:
            cache.delete(additions_cache_key(album_uuid))
            return
        now = timezone.now()
        items = link_items(album).filter(added_at__lte=now)
        if album.notified_at is not None:
            items = items.filter(added_at__gt=album.notified_at)
        counts = dict(
            items.order_by().values_list("added_by").annotate(count=Count("pk"))
        )
        Album.objects.filter(pk=album.pk).update(notified_at=now)

    if counts:
        adders = {u.pk: u for u in User.objects.filter(pk__in=counts)}
        # A member hears about the others' photos, never their own: the
        # members who added nothing share one title, each adder gets theirs.
        audiences = {}
        for member in album_members(album).values_list("pk", flat=True):
            others = tuple(pk for pk in sorted(counts) if pk != member and pk in adders)
            if others:
                audiences.setdefault(others, []).append(member)
        for others, recipients in audiences.items():
            people = sorted((adders[pk] for pk in others), key=lambda u: u.username)
            notify_stream(
                recipient_ids=recipients,
                source=album,
                origin="photos",
                icon="image-plus",
                title=_additions_title(album, people, sum(counts[pk] for pk in others)),
                url=album_url(album),
                actor=people[0],
                stream=ADDITIONS_STREAM,
            )

    # Last, so the next add starts a fresh window.
    cache.delete(additions_cache_key(album_uuid))
    # An add that landed while this ran is past `now`: it elects a task of
    # its own only if another add follows, so elect it here.
    if album.items.filter(added_at__gt=now).exists():
        schedule_additions_notification(album)
