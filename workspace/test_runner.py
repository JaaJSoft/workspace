"""Project test runner.

``MEDIA_ROOT`` defaults to ``BASE_DIR``, so a test that saves file content
writes real blobs into the checkout, under ``files/users/<username>/``, and
nothing removes them afterwards. The runner owns the media root for the whole
session; a test class needs one of its own only when it reads the tree back
(``workspace.common.tests.media``).
"""

import os
import shutil
import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class MediaRootTestRunner(DiscoverRunner):
    """Points ``MEDIA_ROOT`` at a throwaway directory for the whole run."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media_root = tempfile.mkdtemp(prefix="workspace-test-media-")
        # The settings module reads MEDIA_ROOT from the environment, and
        # --parallel workers re-import it from scratch (the default start
        # method is no longer fork), so the override alone would not reach
        # them. Both halves are needed: the env var for processes that import
        # settings after this point, override_settings for the current one.
        self._previous_env = os.environ.get("MEDIA_ROOT")
        os.environ["MEDIA_ROOT"] = self._media_root
        self._media_override = override_settings(MEDIA_ROOT=self._media_root)
        self._media_override.enable()

    def teardown_test_environment(self, **kwargs):
        self._media_override.disable()
        if self._previous_env is None:
            os.environ.pop("MEDIA_ROOT", None)
        else:
            os.environ["MEDIA_ROOT"] = self._previous_env
        shutil.rmtree(self._media_root, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
