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

from django.test.runner import DiscoverRunner, ParallelTestSuite, _init_worker
from django.test.utils import override_settings


def make_worker_media_root(session_root):
    """Return this process's own subdirectory of the session media root.

    Django clones the database per worker but never the media tree, and
    storage paths are built from fixture names that repeat across test classes
    (``files/users/alice/...``, ``files/groups/Team/...``). Two workers would
    otherwise write and delete the same blobs, and unlike a transaction
    nothing rolls a file write back.
    """
    worker_root = os.path.join(session_root, f"worker-{os.getpid()}")
    os.makedirs(worker_root, exist_ok=True)
    return worker_root


def _init_worker_with_media_root(counter, *args, **kwargs):
    _init_worker(counter, *args, **kwargs)
    # Under spawn and forkserver ``_init_worker`` has just re-imported the
    # settings from the environment, so the override has to follow it.
    worker_root = make_worker_media_root(os.environ["MEDIA_ROOT"])
    override_settings(MEDIA_ROOT=worker_root).enable()


class MediaRootParallelTestSuite(ParallelTestSuite):
    init_worker = _init_worker_with_media_root


class MediaRootTestRunner(DiscoverRunner):
    """Points ``MEDIA_ROOT`` at a throwaway directory for the whole run."""

    parallel_test_suite = MediaRootParallelTestSuite

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
