from django.db import migrations

# Frozen copy of the descriptions in workspace/mail/signals.py at the time this
# migration was written: a migration must not follow later edits to that list.
DESCRIPTIONS = {
    "urgent": (
        "Needs an answer or a decision today: a deadline, an incident, "
        "someone waiting on you."
    ),
    "action": (
        "Asks you to do something, but not today: a task, a form to fill, "
        "a reply that can wait."
    ),
    "fyi": (
        "Written to you personally but needs nothing back: an update, "
        "a confirmation, a heads-up."
    ),
    "newsletter": (
        "Editorial mail you subscribed to: newsletters, digests, blog posts, "
        "marketing campaigns."
    ),
    "notification": (
        "Automated mail from a service you use: receipts, alerts, "
        "password resets, build results."
    ),
    "suspicious": (
        "Looks like phishing, a scam or spam: a forged sender, an urgent "
        "payment or credential request, an unsolicited offer."
    ),
}


def seed_label_descriptions(apps, schema_editor):
    db = schema_editor.connection.alias
    MailLabel = apps.get_model("mail", "MailLabel")

    for name, description in DESCRIPTIONS.items():
        # description="" only: a row already carrying one was written by the
        # user, and the seeded text is not an improvement on it.
        MailLabel.objects.using(db).filter(name__iexact=name, description="").update(
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
