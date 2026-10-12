from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("files", "0066_resolve_caseless_names"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="file",
            name="file_unique_live_group_name",
        ),
        migrations.RemoveConstraint(
            model_name="file",
            name="file_unique_live_personal_name",
        ),
        migrations.RemoveConstraint(
            model_name="file",
            name="file_unique_live_root_name",
        ),
        migrations.AddConstraint(
            model_name="file",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("group__isnull", False),
                    ("parent__isnull", False),
                ),
                fields=("parent", "group", "name_key"),
                name="file_unique_live_group_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="file",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("group__isnull", True),
                    ("parent__isnull", False),
                ),
                fields=("parent", "owner", "name_key"),
                name="file_unique_live_personal_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="file",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("group__isnull", True),
                    ("parent__isnull", True),
                ),
                fields=("owner", "name_key"),
                name="file_unique_live_root_name",
            ),
        ),
    ]
