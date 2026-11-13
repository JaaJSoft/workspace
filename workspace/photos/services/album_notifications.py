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
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from workspace.common.logging import scrub
from workspace.notifications.services.notifications import notify_many, notify_stream
from workspace.users.services.settings import get_user_languages, in_recipient_languages

from ..models import Album, AlbumShare

logger = logging.getLogger(__name__)

User = get_user_model()

# The stream of the additions, so their merge never rewrites the
# notification that announced the invitation.
ADDITIONS_STREAM = "additions"


def album_url(album):
    return reverse("photos_ui:album", args=[album.uuid])


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
    for users in in_recipient_languages(
        share_recipients(share, exclude=acting_user)
    ):
        notify_many(
            recipients=users,
            origin="photos",
            icon="book-image",
            title=_('%(user)s shared the album "%(title)s" with you')
            % {"user": acting_user.username, "title": album.title},
            body=_role_abilities(share.role),
            url=album_url(album),
            actor=acting_user,
            source=album,
        )


def notify_role_changed(share, *, acting_user):
    """Tell everyone *share* reaches what they may do now."""
    album = share.album
    for users in in_recipient_languages(
        share_recipients(share, exclude=acting_user)
    ):
        notify_many(
            recipients=users,
            origin="photos",
            icon="book-image",
            title=_new_role_title(share.role, album.title),
            body=_role_abilities(share.role),
            url=album_url(album),
            actor=acting_user,
            source=album,
        )


def notify_removed(share, *, acting_user):
    """Tell everyone *share* reached that it was taken away from them."""
    for users in in_recipient_languages(
        share_recipients(share, exclude=acting_user)
    ):
        notify_many(
            recipients=users,
            origin="photos",
            icon="book-image",
            title=_('%(user)s removed you from the album "%(title)s"')
            % {"user": acting_user.username, "title": share.album.title},
            actor=acting_user,
        )


def notify_revoked(share):
    """Tell everyone *share* reached that it went with its maker's right to
    share the album (``album_revocation``)."""
    for users in in_recipient_languages(
        share_recipients(share, exclude=share.shared_by)
    ):
        notify_many(
            recipients=users,
            origin="photos",
            icon="book-image",
            title=_('The album "%(title)s" is no longer shared with you')
            % {"title": share.album.title},
            actor=share.shared_by,
        )


def _role_abilities(role):
    """What *role* lets its holder do, as a sentence of its own."""
    if role == AlbumShare.Role.MANAGER:
        return _("You can browse it, add photos and manage it.")
    if role == AlbumShare.Role.CONTRIBUTOR:
        return _("You can browse it and add your own photos.")
    return _("You can browse its photos.")


def _new_role_title(role, title):
    """One whole sentence per role: the role's name agrees with the rest of
    the sentence differently in each language."""
    if role == AlbumShare.Role.MANAGER:
        text = _('You are now a manager of the album "%(title)s"')
    elif role == AlbumShare.Role.CONTRIBUTOR:
        text = _('You are now a contributor to the album "%(title)s"')
    else:
        text = _('You are now a viewer of the album "%(title)s"')
    return text % {"title": title}


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
    """ "bob added 3 photos to "Trip"", naming at most two people.

    One sentence per shape of the subject: the verb agrees with it.
    """
    values = {"count": count, "title": album.title}
    if len(people) == 1:
        text = ngettext(
            '%(name)s added %(count)s photo to "%(title)s"',
            '%(name)s added %(count)s photos to "%(title)s"',
            count,
        )
        values["name"] = people[0].username
    elif len(people) == 2:
        text = ngettext(
            '%(first)s and %(second)s added %(count)s photo to "%(title)s"',
            '%(first)s and %(second)s added %(count)s photos to "%(title)s"',
            count,
        )
        values["first"], values["second"] = people[0].username, people[1].username
    else:
        # Three people at least: "others" is always two or more.
        text = ngettext(
            '%(name)s and %(others)s others added %(count)s photo to "%(title)s"',
            '%(name)s and %(others)s others added %(count)s photos to "%(title)s"',
            count,
        )
        values["name"], values["others"] = people[0].username, len(people) - 1
    return text % values


def _by_language(user_ids):
    """*user_ids* grouped by the language each reads."""
    grouped = {}
    for user_id, language in get_user_languages(user_ids).items():
        grouped.setdefault(language, []).append(user_id)
    return grouped.items()


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
            count = sum(counts[pk] for pk in others)
            for language, user_ids in _by_language(recipients):
                with translation.override(language):
                    title = _additions_title(album, people, count)
                notify_stream(
                    recipient_ids=user_ids,
                    source=album,
                    origin="photos",
                    icon="image-plus",
                    title=title,
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
