"""Disable the local password of accounts already linked to an OIDC identity.

Linking now calls set_unusable_password() (see WorkspaceOIDCBackend._link_identity),
but accounts linked before that rule still carry their pre-SSO password hash,
which keeps working on /login, HTTP Basic and WebDAV while being impossible to
change. Align them with newly linked accounts.
"""

from django.conf import settings
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX, make_password
from django.db import migrations


def disable_linked_passwords(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    OIDCIdentity = apps.get_model("users", "OIDCIdentity")
    db = schema_editor.connection.alias

    linked_ids = OIDCIdentity.objects.using(db).values_list("user_id", flat=True)
    users = list(
        User.objects.using(db).filter(pk__in=linked_ids).exclude(
            password__startswith=UNUSABLE_PASSWORD_PREFIX
        )
    )
    for user in users:
        user.password = make_password(None)
    User.objects.using(db).bulk_update(users, ["password"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0008_oidcidentity"),
    ]

    operations = [
        migrations.RunPython(disable_linked_passwords, migrations.RunPython.noop),
    ]
