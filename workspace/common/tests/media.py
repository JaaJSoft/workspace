"""Media-root isolation for tests that read the storage tree back.

The test runner already points ``MEDIA_ROOT`` at a throwaway directory for the
whole session (``workspace.test_runner``), so no test has to think about where
its uploads land. This mixin covers the narrower case: a test that *walks* the
tree - filesystem sync reconciliation, orphan purges - and would otherwise see
blobs left behind by earlier tests, because a rolled-back transaction takes the
rows away but not the files.
"""

import tempfile

from django.test import override_settings


class IsolatedMediaRootMixin:
    """Give each test method an empty ``MEDIA_ROOT`` of its own.

    Mix in before the ``TestCase`` base and call ``super().setUp()`` first, so
    the override is live while the fixtures are built.
    """

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory(prefix="workspace-test-media-")
        self.addCleanup(tmpdir.cleanup)
        self.media_root = tmpdir.name
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        super().setUp()
