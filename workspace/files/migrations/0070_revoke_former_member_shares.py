"""Delete the public links and shares a former member made on a group's nodes.

Leaving a group now costs a member their rights on its nodes, the ones they
created included, and the links and shares they made there are revoked as they
leave. Those made by members who left before that rule existed are still live;
this removes them.
"""

from itertools import batched

from django.conf import settings
from django.db import migrations


def _delete_former_member_rows(model, maker_field, db, Membership):
    """Delete the *model* rows on group nodes whose *maker_field* user is no
    longer in that group."""
    rows = list(
        model.objects.using(db)
        .filter(file__group__isnull=False)
        .values_list("pk", maker_field, "file__group_id")
    )
    if not rows:
        return
    makers = {maker for _, maker, _ in rows}
    members = set()
    for batch in batched(makers, 500, strict=False):
        members.update(
            Membership.objects.using(db)
            .filter(user_id__in=batch)
            .values_list("user_id", "group_id")
        )
    stale = [pk for pk, maker, group in rows if (maker, group) not in members]
    for batch in batched(stale, 500, strict=False):
        model.objects.using(db).filter(pk__in=batch).delete()


def revoke_former_member_sharing(apps, schema_editor):
    db = schema_editor.connection.alias
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Membership = User.groups.through
    _delete_former_member_rows(
        apps.get_model("files", "FileShareLink"), "created_by_id", db, Membership
    )
    _delete_former_member_rows(
        apps.get_model("files", "FileShare"), "shared_by_id", db, Membership
    )


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0069_file_tag_source"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(revoke_former_member_sharing, migrations.RunPython.noop),
    ]
