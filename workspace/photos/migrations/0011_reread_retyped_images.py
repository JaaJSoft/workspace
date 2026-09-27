"""Forget what was read off the HEIF and AVIF photos while they passed for videos.

``files.0060`` retypes them; before it, no heif or avif file was ever read as
a photo, so every row found here was written by the video readers. Dropping
the rows makes the files pending again, and the catch-up reads them as photos.
"""

from django.db import migrations


def forget_video_readings(apps, schema_editor):
    MediaItem = apps.get_model("photos", "MediaItem")
    FaceAnalysis = apps.get_model("photos", "FaceAnalysis")
    db = schema_editor.connection.alias

    for model in (MediaItem, FaceAnalysis):
        model.objects.using(db).filter(file__type__in=["heif", "avif"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0060_retype_isobmff_images"),
        ("photos", "0010_face_timestamp"),
    ]

    operations = [
        migrations.RunPython(forget_video_readings, migrations.RunPython.noop),
    ]
