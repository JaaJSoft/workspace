"""Give UserMemory a UUID primary key, keeping every stored memory.

The rows are copied into a new table rather than altered in place: dropping
the auto ``id`` would leave the historical model without a primary key, which
the SQLite table rebuild and the PostgreSQL constraint swap handle
differently. A table built with its final key, filled, then renamed into
place takes the same path on both. Its timestamps are plain while the rows
are copied, so the copy keeps them instead of stamping the migration's time.
"""

from django.conf import settings
from django.db import migrations, models

import workspace.common.uuids

_COPIED = ("user_id", "bot_id", "key", "content", "created_at", "updated_at")


def copy_to_uuid_table(apps, schema_editor):
    db = schema_editor.connection.alias
    old = apps.get_model("ai", "UserMemory")
    new = apps.get_model("ai", "UserMemoryUuid")
    rows = old.objects.using(db).order_by("created_at", "pk").values(*_COPIED)
    new.objects.using(db).bulk_create(
        (new(**row) for row in rows.iterator()), batch_size=500
    )


def copy_back_to_integer_table(apps, schema_editor):
    db = schema_editor.connection.alias
    old = apps.get_model("ai", "UserMemory")
    new = apps.get_model("ai", "UserMemoryUuid")
    for row in new.objects.using(db).order_by("created_at", "uuid").values(*_COPIED):
        # The integer table stamps its own timestamps on insert.
        memory = old.objects.using(db).create(**row)
        old.objects.using(db).filter(pk=memory.pk).update(
            created_at=row["created_at"], updated_at=row["updated_at"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0025_aitask_answer_figures"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserMemoryUuid",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=workspace.common.uuids.uuid_v7_or_v4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("key", models.CharField(max_length=100)),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "bot",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.RunPython(copy_to_uuid_table, copy_back_to_integer_table),
        migrations.DeleteModel(name="UserMemory"),
        migrations.RenameModel(old_name="UserMemoryUuid", new_name="UserMemory"),
        migrations.AlterField(
            model_name="usermemory",
            name="user",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="ai_memories",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="usermemory",
            name="bot",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="bot_memories",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="usermemory",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name="usermemory",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterModelOptions(name="usermemory", options={"ordering": ["key"]}),
        migrations.AlterUniqueTogether(
            name="usermemory", unique_together={("user", "bot", "key")}
        ),
    ]
