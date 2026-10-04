"""Media-root isolation for tests that read the storage tree back.

The test runner already points ``MEDIA_ROOT`` at a throwaway directory for the
whole session (``workspace.test_runner``), so no test has to think about where
its uploads land. This mixin covers the narrower case: a test that *walks* the
tree - filesystem sync reconciliation, orphan purges - and would otherwise see
blobs left behind by earlier tests, because a rolled-back transaction takes the
rows away but not the files.

The suite can also run with every blob on object storage
(``WORKSPACE_TEST_STORAGE=s3``, see ``workspace.common.tests.s3``). The mixin
then gives the test an empty prefix of the run's bucket instead, and a test
that can only hold on a disk says so with ``local_storage_only``.
"""

import tempfile
import uuid
from unittest import skipIf

from django.test import override_settings

from .s3 import ON_OBJECT_STORAGE, session_storages

local_storage_only = skipIf(
    ON_OBJECT_STORAGE,
    "reads the blobs as files on a disk, which object storage has not",
)


class IsolatedMediaRootMixin:
    """Give each test method an empty ``MEDIA_ROOT`` of its own.

    Mix in before the ``TestCase`` base and call ``super().setUp()`` first, so
    the override is live while the fixtures are built.
    """

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory(prefix="workspace-test-media-")
        self.addCleanup(tmpdir.cleanup)
        self.media_root = tmpdir.name
        settings = {"MEDIA_ROOT": self.media_root}
        if ON_OBJECT_STORAGE:
            settings["STORAGES"] = session_storages(f"isolated-{uuid.uuid4().hex}")
        override = override_settings(**settings)
        override.enable()
        self.addCleanup(override.disable)
        super().setUp()
