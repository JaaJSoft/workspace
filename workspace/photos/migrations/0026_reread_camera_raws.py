"""Forget what was read off the camera RAW photos while they passed for TIFFs.

``files.0074`` retypes them; before it, a RAW was decoded as the 160x120
thumbnail of its first TIFF directory, or not at all. Dropping the readings
makes the files pending again, and the catch-ups read the real picture: the
faces and people found again keep what the user decided about the old ones.
Embeddings stay: the model reads a picture smaller than that thumbnail.
"""

from django.db import migrations

_RAW_LABELS = ["dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "pef", "raf"]


def forget_thumbnail_readings(apps, schema_editor):
    db = schema_editor.connection.alias
    for name in ("MediaItem", "FaceAnalysis", "AppearanceAnalysis"):
        model = apps.get_model("photos", name)
        model.objects.using(db).filter(file__type__in=_RAW_LABELS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0074_retype_camera_raws"),
        ("photos", "0025_hide_people_set_apart_in_their_photo"),
    ]

    operations = [
        migrations.RunPython(forget_thumbnail_readings, migrations.RunPython.noop),
    ]
