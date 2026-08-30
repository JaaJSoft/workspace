from django.db import migrations

# Frozen copy of the descriptions in workspace/mail/signals.py at the time this
# migration was written: a migration must not follow later edits to that list.
DESCRIPTIONS = {
    "Urgent": (
        "Needs an answer or a decision today: a deadline, an incident, "
        "someone waiting on you."
    ),
    "Action": (
        "Asks you to do something, but not today: a task, a form to fill, "
        "a reply that can wait."
    ),
    "FYI": (
        "Written to you personally but needs nothing back: an update, "
        "a confirmation, a heads-up."
    ),
    "Newsletter": (
        "Editorial mail you subscribed to: newsletters, digests, blog posts, "
        "marketing campaigns."
    ),
    "Notification": (
        "Automated mail from a service you use: receipts, alerts, "
        "password resets, build results."
    ),
    "Suspicious": (
        "Looks like phishing, a scam or spam: a forged sender, an urgent "
        "payment or credential request, an unsolicited offer."
    ),
}


def seed_label_descriptions(apps, schema_editor):
    db = schema_editor.connection.alias
    MailLabel = apps.get_model("mail", "MailLabel")

    for name, description in DESCRIPTIONS.items():
        # Nothing marks a row as seeded, so the filter is as narrow as the
        # seeders are: they write these names verbatim, and only ever leave the
        # description empty. A case variant or a description already in place
        # means the label is the user's, and their wording is not ours to
        # replace.
        MailLabel.objects.using(db).filter(name=name, description="").update(
            description=description
        )


class Migration(migrations.Migration):
    dependencies = [
        ("mail", "0033_maillabel_description"),
    ]

    operations = [
        # No reverse: the description is user-editable, so a reverse cannot tell
        # a seeded default from a description the user wrote to match it.
        migrations.RunPython(seed_label_descriptions, migrations.RunPython.noop),
    ]
