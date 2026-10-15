"""Delete the public links a former member made on a group's nodes.

Leaving a group now costs a member their rights on its nodes, the ones they
created included, and the links they made there are revoked as they leave.
Links made by members who left before that rule existed are still live; this
removes them.
"""

from itertools import batched

from django.conf import settings
from django.db import migrations


def revoke_former_member_links(apps, schema_editor):
    db = schema_editor.connection.alias
    FileShareLink = apps.get_model("files", "FileShareLink")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Membership = User.groups.through

    links = list(
        FileShareLink.objects.using(db)
        .filter(file__group__isnull=False)
        .values_list("pk", "created_by_id", "file__group_id")
    )
    if not links:
        return
    creators = {created_by for _, created_by, _ in links}
    members = set()
    for batch in batched(creators, 500, strict=False):
        members.update(
            Membership.objects.using(db)
            .filter(user_id__in=batch)
            .values_list("user_id", "group_id")
        )
    stale = [
        pk for pk, created_by, group in links if (created_by, group) not in members
    ]
    for batch in batched(stale, 500, strict=False):
        FileShareLink.objects.using(db).filter(pk__in=batch).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0069_file_tag_source"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(revoke_former_member_links, migrations.RunPython.noop),
    ]
