"""Hide the people found by their body who were set apart from someone their
photo already shows.

Naming someone found by their body after a person their photo already shows
sets them apart from that person, and now hides them as well: the user
recognized somebody already there, boxed a second time or with their face
given to the person beside them. Those set apart before were left waiting,
and the review asked about them once more; this hides them as the answer
now does.
"""

from django.db import migrations
from django.db.models import Exists, OuterRef


def hide_people_set_apart_in_their_photo(apps, schema_editor):
    db = schema_editor.connection.alias
    Appearance = apps.get_model("photos", "Appearance")
    Face = apps.get_model("photos", "Face")
    shown_by_a_face = Face.objects.using(db).filter(
        file_id=OuterRef("file_id"),
        cluster__person_id=OuterRef("rejected_person_id"),
        cluster__hidden=False,
    )
    said_of_another = Appearance.objects.using(db).filter(
        file_id=OuterRef("file_id"),
        person_id=OuterRef("rejected_person_id"),
        hidden=False,
    )
    Appearance.objects.using(db).filter(
        person__isnull=True,
        suggested_person__isnull=True,
        rejected_person__isnull=False,
        hidden=False,
    ).filter(Exists(shown_by_a_face) | Exists(said_of_another)).update(hidden=True)


class Migration(migrations.Migration):
    dependencies = [
        ("photos", "0024_appearances"),
    ]

    operations = [
        migrations.RunPython(
            hide_people_set_apart_in_their_photo, migrations.RunPython.noop
        ),
    ]
