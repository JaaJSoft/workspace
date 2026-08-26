"""The test run must not leave uploaded blobs in the checkout.

``MEDIA_ROOT`` defaults to ``BASE_DIR``, so without the runner in
``workspace.test_runner`` every test that saves content writes into the
working tree and nothing cleans it up.
"""

import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase


def _is_inside(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


class MediaRootIsolationTests(SimpleTestCase):
    def test_media_root_is_outside_the_repository(self):
        self.assertFalse(
            _is_inside(settings.MEDIA_ROOT, settings.BASE_DIR),
            f"MEDIA_ROOT points into the checkout ({settings.MEDIA_ROOT})",
        )

    def test_saved_content_lands_outside_the_repository(self):
        name = default_storage.save("media-root-check.txt", ContentFile(b"x"))
        self.addCleanup(default_storage.delete, name)

        self.assertFalse(_is_inside(default_storage.path(name), settings.BASE_DIR))
