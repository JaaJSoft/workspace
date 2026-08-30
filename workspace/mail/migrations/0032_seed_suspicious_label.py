from django.db import migrations

LABEL = {"name": "Suspicious", "color": "accent", "icon": "shield", "position": 5}


def seed_suspicious_label(apps, schema_editor):
    db = schema_editor.connection.alias
    MailAccount = apps.get_model("mail", "MailAccount")
    MailLabel = apps.get_model("mail", "MailLabel")

    accounts_with_label = set(
        MailLabel.objects.using(db)
        .filter(name__iexact=LABEL["name"])
        .values_list("account_id", flat=True)
    )
    to_create = [
        MailLabel(account_id=pk, **LABEL)
        for pk in MailAccount.objects.using(db)
        .exclude(pk__in=accounts_with_label)
        .values_list("pk", flat=True)
    ]
    if to_create:
        MailLabel.objects.using(db).bulk_create(to_create)


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0031_seed_notify_on_apply"),
    ]

    operations = [
        # No reverse: a label is user-editable and can already carry messages,
        # so a reverse cannot tell a seeded row from one the user kept and
        # would drop its message links with it.
        migrations.RunPython(seed_suspicious_label, migrations.RunPython.noop),
    ]
