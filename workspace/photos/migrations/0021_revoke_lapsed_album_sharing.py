"""Delete the album links and shares whose maker may no longer share the album.

Losing the right to share an album - owner of a personal album, member of a
group album's group, manager - now revokes the public links and the shares one
made on it. Those left behind before that rule existed are still live; this
removes them, following the chain a revoked share starts.
"""

from collections import defaultdict
from itertools import batched

from django.conf import settings
from django.db import migrations

MANAGER = "manager"


def _rows_by_batch(manager, field, values, *columns):
    rows = []
    for batch in batched(values, 500, strict=False):
        rows += manager.filter(**{f"{field}__in": batch}).values_list(*columns)
    return rows


def revoke_lapsed_album_sharing(apps, schema_editor):
    db = schema_editor.connection.alias
    Album = apps.get_model("photos", "Album")
    AlbumLink = apps.get_model("photos", "AlbumLink")
    AlbumShare = apps.get_model("photos", "AlbumShare")
    Project = apps.get_model("projects", "Project")
    ProjectMember = apps.get_model("projects", "ProjectMember")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    links = list(
        AlbumLink.objects.using(db).values_list("pk", "album_id", "created_by_id")
    )
    shares = {
        pk: rest
        for pk, *rest in AlbumShare.objects.using(db).values_list(
            "pk",
            "album_id",
            "shared_by_id",
            "shared_with_id",
            "shared_with_group_id",
            "shared_with_project_id",
            "role",
        )
    }
    if not links and not shares:
        return

    album_ids = {album_id for _, album_id, _ in links} | {
        row[0] for row in shares.values()
    }
    albums = {
        pk: (owner_id, group_id)
        for pk, owner_id, group_id in _rows_by_batch(
            Album.objects.using(db), "pk", album_ids, "pk", "owner_id", "group_id"
        )
    }
    makers = {maker for _, _, maker in links} | {
        row[1] for row in shares.values() if row[1] is not None
    }
    groups = defaultdict(set)
    for user_id, group_id in _rows_by_batch(
        User.groups.through.objects.using(db),
        "user_id",
        makers,
        "user_id",
        "group_id",
    ):
        groups[user_id].add(group_id)
    projects = defaultdict(set)
    for user_id, project_id in _rows_by_batch(
        ProjectMember.objects.using(db).filter(left_at__isnull=True),
        "user_id",
        makers,
        "user_id",
        "project_id",
    ):
        projects[user_id].add(project_id)
    projects_of_group = defaultdict(set)
    for project_id, group_id in _rows_by_batch(
        Project.groups.through.objects.using(db),
        "group_id",
        set().union(*groups.values()),
        "project_id",
        "group_id",
    ):
        projects_of_group[group_id].add(project_id)
    for user_id, their_groups in groups.items():
        for group_id in their_groups:
            projects[user_id] |= projects_of_group[group_id]

    manager_shares = defaultdict(dict)
    for pk, (album_id, _, shared_with, group_id, project_id, role) in shares.items():
        if role == MANAGER:
            manager_shares[album_id][pk] = (shared_with, group_id, project_id)

    def manages(user_id, album_id):
        return any(
            shared_with == user_id
            or group_id in groups[user_id]
            or project_id in projects[user_id]
            for shared_with, group_id, project_id in manager_shares[album_id].values()
        )

    def can_share(user_id, album_id):
        owner_id, group_id = albums[album_id]
        if group_id is None and owner_id == user_id:
            return True
        if group_id is not None and group_id in groups[user_id]:
            return True
        return manages(user_id, album_id)

    lapsed_shares = []
    while True:
        lapsed = [
            pk
            for pk, (album_id, maker, *_) in shares.items()
            if maker is not None and not can_share(maker, album_id)
        ]
        if not lapsed:
            break
        for pk in lapsed:
            album_id = shares.pop(pk)[0]
            manager_shares[album_id].pop(pk, None)
        lapsed_shares += lapsed
    lapsed_links = [
        pk for pk, album_id, maker in links if not can_share(maker, album_id)
    ]
    for batch in batched(lapsed_links, 500, strict=False):
        AlbumLink.objects.using(db).filter(pk__in=batch).delete()
    for batch in batched(lapsed_shares, 500, strict=False):
        AlbumShare.objects.using(db).filter(pk__in=batch).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("photos", "0020_photo_auto_tagged_at"),
        ("projects", "0029_task_file_link"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(revoke_lapsed_album_sharing, migrations.RunPython.noop),
    ]
