"""Access helpers for the photo library.

The library is a way of reading files the user can already open, so access is
the files module's: every queryset here starts from a ``FileService`` helper
or from ``FileShare.objects.reaching``.

A *scope* picks which of those files the library reads: ``MINE`` (the
default, the user's personal files), ``SHARED`` (files other people shared
with the user, their groups or their projects), ``ALL`` (every file the user
can open), or a ``Group`` instance for that group's folder alone.

Albums are a way in of their own. Their members (the owner, and whoever a
share reaches) see the items each contributor vouches for, the files the
contributor could share in Files anyway, plus any item they can open
themselves. An item whose file went to the trash, was quarantined, or that
nobody in reach may pass on drops out of the album instead of leaking
through it. Album-scoped endpoints serve those files, never ``FileService``.

Faces and their clusters are the user's own and only ever in their personal
photos and videos (see services/face_analysis.py); the helpers at the end
narrow them to the files the library still shows.

What the user hid (``HiddenFile``: photos, videos, and whole folders) is out
of every helper here, albums and faces included, unless one is asked for it
with ``hidden=True``: the Hidden view is the only way back to it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import (
    Count,
    Exists,
    F,
    Max,
    Min,
    OuterRef,
    Q,
    TextField,
    Value,
    Window,
)
from django.db.models.functions import Concat, Lower, RowNumber, StrIndex

from workspace.files.models import File, FileShare, Tag
from workspace.files.services import FileService
from workspace.files.services.scanning.policy import exclude_blocked
from workspace.photos.models import (
    Album,
    AlbumItem,
    AlbumShare,
    Face,
    FaceCluster,
    HiddenFile,
    MediaItem,
)
from workspace.photos.services.analysis import library_candidates
from workspace.photos.services.face_preferences import faces_enabled
from workspace.projects.models import Project
from workspace.projects.queries import project_users

User = get_user_model()

MINE = "mine"
SHARED = "shared"
ALL = "all"

# Album roles, lowest first. The owner of a personal album, and every member
# of a group album's group, hold OWNER; shares grant the three others.
VIEWER = AlbumShare.Role.VIEWER.value
CONTRIBUTOR = AlbumShare.Role.CONTRIBUTOR.value
MANAGER = AlbumShare.Role.MANAGER.value
OWNER = "owner"
ROLE_RANK = {VIEWER: 0, CONTRIBUTOR: 1, MANAGER: 2, OWNER: 3}


def _media(files):
    """The photos and videos of *files*, quarantined ones excluded."""
    return exclude_blocked(library_candidates(files))


def _hidden_q(user):
    """The files *user* hid from their library, as a condition on ``File``:
    those they hid one by one, and everything under a folder they hid.

    Folder paths only name a node within one tree, so a folder hides what
    shares its path prefix *and* its tree: the owner's personal files for a
    personal folder, the group's for a group one. The path test runs in SQL
    (no LIKE, so a ``%`` or ``_`` in a folder name is matched literally),
    which keeps every library queryset lazy.
    """
    hidden = HiddenFile.objects.filter(owner=user)
    folders = (
        hidden.filter(file__node_type=File.NodeType.FOLDER)
        .exclude(file__path="")
        .annotate(
            at=StrIndex(
                OuterRef("path"),
                Concat("file__path", Value("/"), output_field=TextField()),
            )
        )
        .filter(at=1)
    )
    personal_folders = folders.filter(
        file__group__isnull=True, file__owner_id=OuterRef("owner_id")
    )
    group_folders = folders.filter(file__group_id=OuterRef("group_id"))
    return (
        Exists(hidden.filter(file_id=OuterRef("pk")))
        | (Q(group__isnull=True) & Exists(personal_folders))
        | Exists(group_folders)
    )


def _visibility(user, files, hidden):
    """*files* the user hid when *hidden*, else those they did not."""
    if hidden:
        return files.filter(_hidden_q(user))
    return files.exclude(_hidden_q(user))


def _scoped_media(user, scope, hidden=False):
    if scope == MINE:
        files = FileService.user_files_qs(user)
    elif scope == SHARED:
        # Sharing with someone is file-only, so the share rows are the whole
        # answer: no folder to descend into.
        files = File.objects.filter(
            pk__in=FileShare.objects.reaching(user).values("file_id"),
            deleted_at__isnull=True,
        ).exclude(owner=user)
    elif scope == ALL:
        files = File.objects.filter(
            pk__in=FileService.accessible_file_ids(user, include_deleted=False)
        )
    else:
        # Narrowed from the user's own groups, so a group they left, or never
        # joined, reads as empty rather than as someone else's folder.
        files = FileService.user_group_files_qs(user).filter(group=scope)
    return _visibility(user, _media(files), hidden)


def library_files(user, scope=MINE, *, hidden=False):
    """The analyzed photos and videos in *scope*, as live ``File`` rows.

    Trashed files drop out through the files helpers and come back on
    restore: their MediaItem row is left alone the whole time. So do hidden
    ones, unless *hidden* asks for them alone.
    """
    return _scoped_media(user, scope, hidden).filter(media_item__isnull=False)


def unanalyzed_count(user, scope=MINE):
    """How many photos and videos in *scope* are still waiting for a MediaItem row.

    Quarantined files never get one, so counting them would announce an
    analysis that is not coming.
    """
    return (
        _scoped_media(user, scope)
        .filter(media_item__isnull=True)
        .exclude(content="")
        .exclude(content__isnull=True)
        .count()
    )


def library_tags(user, scope=MINE):
    """The user's tags carried by at least one photo in *scope*, with ``photo_count``."""
    return (
        Tag.objects.filter(
            owner=user,
            file_tags__file__in=library_files(user, scope).values("pk"),
        )
        .annotate(photo_count=Count("file_tags"))
        .order_by(Lower("name"))
    )


def library_groups(user):
    """The user's groups whose folder holds at least one photo, by name."""
    group_photos = _visibility(
        user, _media(FileService.user_group_files_qs(user)), hidden=False
    ).filter(media_item__isnull=False)
    return Group.objects.filter(pk__in=group_photos.values("group_id")).order_by(
        Lower("name")
    )


def has_shared_photos(user):
    """True when someone shared at least one photo with the user."""
    return library_files(user, SHARED).exists()


def _folders(user):
    """The live folders of *user*'s own tree and of their groups'."""
    return (
        FileService.user_files_qs(user) | FileService.user_group_files_qs(user)
    ).filter(node_type=File.NodeType.FOLDER)


def hideable_files(user):
    """What *user* may hide: the photos and videos they can open, and their
    own and their groups' folders. Trashed files excluded."""
    reachable = File.objects.filter(
        pk__in=FileService.accessible_file_ids(user, include_deleted=False)
    )
    return File.objects.filter(
        Q(pk__in=library_candidates(reachable).values("pk"))
        | Q(pk__in=_folders(user).values("pk"))
    )


def hidden_folders(user):
    """The folders *user* hid from their library that they can still open,
    by path. A folder in the trash, or in a group they left, hides nothing
    from them and is left out."""
    return (
        _folders(user)
        .filter(pk__in=HiddenFile.objects.filter(owner=user).values("file_id"))
        .select_related("group")
        .order_by(Lower("path"), "pk")
    )


def still_hidden(user, file_uuids):
    """The uuids among *file_uuids* that *user* still hides: on their own,
    or through a folder they hid around them."""
    return set(
        File.objects.filter(pk__in=file_uuids)
        .filter(_hidden_q(user))
        .values_list("pk", flat=True)
    )


def media_type_counts(files):
    """How many photos and how many videos *files* holds, as a dict."""
    return files.aggregate(
        photos=Count("pk", filter=Q(media_item__media_type=MediaItem.MediaType.PHOTO)),
        videos=Count("pk", filter=Q(media_item__media_type=MediaItem.MediaType.VIDEO)),
    )


def _own_albums_q(user):
    """The albums *user* holds without being invited: their personal ones
    and their groups'."""
    return Q(owner=user, group__isnull=True) | Q(group__in=user.groups.values("pk"))


def user_albums(user):
    """The albums *user* can open: their own, their groups', and those
    shared with them, their groups or their projects."""
    return Album.objects.filter(
        _own_albums_q(user)
        | Q(pk__in=AlbumShare.objects.reaching(user).values("album_id"))
    )


def own_albums(user):
    """The albums *user* holds: their personal ones and their groups'."""
    return Album.objects.filter(_own_albums_q(user))


def shared_albums(user):
    """The albums shared with *user* that are not already theirs."""
    return Album.objects.filter(
        pk__in=AlbumShare.objects.reaching(user).values("album_id")
    ).exclude(_own_albums_q(user))


def highest_role(roles):
    """The highest of *roles*, or None when there are none."""
    return max(roles, key=ROLE_RANK.__getitem__, default=None)


def get_album_role(user, album):
    """*user*'s role in *album* (``OWNER``, ``MANAGER``, ``CONTRIBUTOR`` or
    ``VIEWER``), or None when they cannot open it."""
    if album.group_id is None:
        if album.owner_id == user.pk:
            return OWNER
    elif user.groups.filter(pk=album.group_id).exists():
        return OWNER
    return highest_role(
        AlbumShare.objects.reaching(user)
        .filter(album=album)
        .values_list("role", flat=True)
    )


def album_roles(user, albums):
    """*user*'s role in each of *albums*, keyed by album uuid, in a few
    queries whatever their number.

    Albums out of reach are absent rather than mapped to None.
    """
    group_ids = set(user.groups.values_list("pk", flat=True))
    roles = {}
    invited = []
    for album in albums:
        if album.group_id is None and album.owner_id == user.pk:
            roles[album.uuid] = OWNER
        elif album.group_id is not None and album.group_id in group_ids:
            roles[album.uuid] = OWNER
        else:
            invited.append(album.uuid)
    if invited:
        shares = (
            AlbumShare.objects.reaching(user)
            .filter(album_id__in=invited)
            .values_list("album_id", "role")
        )
        for album_id, role in shares:
            roles[album_id] = highest_role([role, roles.get(album_id, role)])
    return roles


def direct_share_album_ids(user, albums):
    """The uuids among *albums* shared with *user* by name: the ones they
    can leave. A share with one of their groups or projects is not theirs
    to give up."""
    return set(
        AlbumShare.objects.filter(album__in=albums, shared_with=user).values_list(
            "album_id", flat=True
        )
    )


def reachable_album(user, album_uuid):
    """The album *user* can open under that uuid, or None.

    A caller getting None answers 404 without saying whether the album does
    not exist or is someone else's.
    """
    return user_albums(user).filter(uuid=album_uuid).first()


def album_members(album):
    """The active users who can open *album*: its owner (every member of its
    group for a group album) and everyone a share reaches."""
    shares = AlbumShare.objects.filter(album=album)
    if album.group_id is None:
        holders = Q(pk=album.owner_id)
    else:
        holders = Q(groups=album.group_id)
    members = User.objects.filter(
        holders
        | Q(pk__in=shares.values("shared_with"))
        | Q(groups__in=shares.values("shared_with_group")),
        is_active=True,
    )
    ids = set(members.values_list("pk", flat=True))
    for project in Project.objects.filter(pk__in=shares.values("shared_with_project")):
        ids.update(user.pk for user in project_users(project))
    return User.objects.filter(pk__in=ids, is_active=True)


def _live_media():
    """Every analyzed photo and video still out of the trash, quarantined
    ones excluded."""
    return _media(File.objects.filter(deleted_at__isnull=True)).filter(
        media_item__isnull=False
    )


def vouched_items_q():
    """Album items whose contributor can still share the file in Files: it
    is theirs, or it sits in a group of theirs.

    Those are the items an album shows every member and every link: adding a
    file one can share is sharing it. A file someone else shared with the
    contributor is not theirs to pass on, and a file that changed hands, or
    whose contributor left its group or lost their account, stops being
    vouched for.
    """
    group_member = User.groups.through.objects.filter(
        user_id=OuterRef("added_by_id"), group_id=OuterRef("file__group_id")
    )
    return Q(added_by__isnull=False) & (
        Q(file__group__isnull=True, file__owner_id=F("added_by_id"))
        | Exists(group_member)
    )


def visible_album_items(user, albums):
    """The items of *albums* whose file *user* can see there.

    A member sees every item its contributor vouches for (``vouched_items_q``),
    plus those they can open through Files anyway. Trashed and quarantined
    files drop out, and what *user* hid from their library stays hidden.
    """
    media = _visibility(user, _live_media(), hidden=False)
    return AlbumItem.objects.filter(
        album__in=albums, file_id__in=media.values("pk")
    ).filter(
        vouched_items_q()
        | Q(file_id__in=FileService.accessible_file_ids(user, include_deleted=False))
    )


def album_files(user, album):
    """The album's photos and videos *user* can see, as live ``File`` rows.

    Empty for an album the user cannot open.
    """
    if get_album_role(user, album) is None:
        return File.objects.none()
    return File.objects.filter(
        pk__in=visible_album_items(user, [album]).values("file_id")
    )


def contributable_files(user):
    """The photos and videos *user* may add to an album as an invited
    member: their own and their groups', the files they can vouch for."""
    files = FileService.user_files_qs(user) | FileService.user_group_files_qs(user)
    return _visibility(user, _media(files), hidden=False).filter(
        media_item__isnull=False
    )


def link_items(album):
    """The items of *album* its public links show: the vouched ones alone,
    whoever opens them."""
    return AlbumItem.objects.filter(
        album=album, file_id__in=_live_media().values("pk")
    ).filter(vouched_items_q())


def link_files(album):
    """``link_items`` as live ``File`` rows."""
    return File.objects.filter(pk__in=link_items(album).values("file_id"))


def _summaries(albums, items):
    """``{"count": int, "cover_id": uuid | None}`` per album of *albums*,
    counting *items* alone. See ``album_summaries``."""
    summaries = {album.uuid: {"count": 0, "cover_id": None} for album in albums}
    if not albums:
        return summaries
    for row in items.order_by().values("album_id").annotate(count=Count("pk")):
        summaries[row["album_id"]]["count"] = row["count"]

    chosen = {album.uuid: album.cover_id for album in albums if album.cover_id}
    if chosen:
        visible_covers = set(
            items.filter(album__cover_id=F("file_id")).values_list(
                "album_id", flat=True
            )
        )
        for album_id in visible_covers:
            summaries[album_id]["cover_id"] = chosen[album_id]

    missing = [uuid for uuid, s in summaries.items() if s["cover_id"] is None]
    if missing:
        latest = (
            items.filter(album_id__in=missing)
            .annotate(
                rank=Window(
                    RowNumber(),
                    partition_by=F("album_id"),
                    order_by=[F("added_at").desc(), F("uuid").desc()],
                )
            )
            .filter(rank=1)
            .values_list("album_id", "file_id")
        )
        for album_id, file_id in latest:
            summaries[album_id]["cover_id"] = file_id
    return summaries


def album_summaries(user, albums):
    """What a listing shows of each album, keyed by album uuid.

    Each value is ``{"count": int, "cover_id": uuid | None}``. The cover is
    the one the album names while it is an item the viewer can see, and the
    most recently added visible item otherwise; None for an album showing
    nothing. Three queries whatever the number of albums.
    """
    albums = list(albums)
    return _summaries(albums, visible_album_items(user, [a.uuid for a in albums]))


def link_summary(album):
    """``album_summaries`` for one album, as its public links show it."""
    return _summaries([album], link_items(album))[album.uuid]


def album_date_range(files):
    """The earliest and latest capture dates of *files*, as ``(first, last)``."""
    bounds = files.aggregate(
        first=Min("media_item__taken_at"), last=Max("media_item__taken_at")
    )
    return bounds["first"], bounds["last"]


def user_face_clusters(user):
    """The user's face clusters, with ``photo_count``: live photos only.

    Empty while face grouping is off for the user, whatever rows a pending
    purge has not reached yet.
    """
    clusters = FaceCluster.objects.filter(owner=user)
    if not faces_enabled(user):
        clusters = clusters.none()
    live = Q(faces__file__in=library_files(user).values("pk"))
    return clusters.annotate(photo_count=Count("faces", filter=live))


def user_faces(user):
    """The faces the user may see and correct: those of their live photos."""
    if not faces_enabled(user):
        return Face.objects.none()
    return Face.objects.filter(owner=user, file__in=library_files(user).values("pk"))


def cluster_photos(user, cluster):
    """The live photos of the user's library holding a face of *cluster*."""
    return library_files(user).filter(faces__cluster=cluster)


def person_photos(user, person):
    """The live photos of the user's library showing *person*, through any
    of the user's clusters named after them. Another user's clusters of the
    same contact are theirs alone."""
    return (
        library_files(user)
        .filter(
            faces__cluster__in=user_face_clusters(user)
            .filter(person=person)
            .values("pk")
        )
        .distinct()
    )


def has_photos_of_person(user, person):
    """Whether *person* is in any photo of the user's library, face grouping on."""
    return faces_enabled(user) and person_photos(user, person).exists()


def face_progress(user):
    """How far the analysis of the user's photos and videos has come, as a dict."""
    from workspace.photos.services.face_analysis import (
        face_candidates,
        pending_faces_qs,
    )

    total = (
        exclude_blocked(face_candidates(FileService.user_files_qs(user)))
        .with_blob()
        .count()
    )
    pending = (
        pending_faces_qs().filter(owner=user).count() if faces_enabled(user) else total
    )
    return {"total": total, "analyzed": total - pending}
