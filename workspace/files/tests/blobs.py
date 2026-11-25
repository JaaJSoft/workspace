from contextlib import contextmanager

from django.db.models.signals import post_save

from workspace.files.models import File


@contextmanager
def written_blobs():
    """Yield the list of blob names of the files created inside the block."""
    blobs = []

    def record(sender, instance, created, **kwargs):
        if created and instance.content:
            blobs.append(instance.content.name)

    post_save.connect(record, sender=File, weak=False)
    try:
        yield blobs
    finally:
        post_save.disconnect(record, sender=File)
