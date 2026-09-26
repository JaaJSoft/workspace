"""Project test runner.

``MEDIA_ROOT`` defaults to ``BASE_DIR``, so a test that saves file content
writes real blobs into the checkout, under ``files/users/<username>/``, and
nothing removes them afterwards. The runner owns the media root for the whole
session; a test class needs one of its own only when it reads the tree back
(``workspace.common.tests.media``).

It also arms the full-row-write guard over ``File``, the model the app mutates
from the most places at once (``workspace.common.tests.row_writes`` explains
what it catches and why nothing else can).

And it opens preview modules to everyone. Fixture users are regular users, and
under the production audience (``staff``) a preview module refuses every
request they make, so its own tests would test nothing but the refusal. The
tests of the audience itself override the setting back.
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
        # Imported here rather than at module scope: the runner is built before
        # the app registry is ready, and this reaches for a model.
        from workspace.common.tests.row_writes import guard_full_row_writes
        from workspace.files.models import File

        guard_full_row_writes(
            # File.save() forwards every caller's save to the signal;
            # naming it here keeps the blame on whoever asked for one.
            File,
            forwarded_by=("workspace/files/models.py:save",),
        )

        self._media_root = tempfile.mkdtemp(prefix="workspace-test-media-")
        self._restore_settings = [
            _override_from_env("MEDIA_ROOT", self._media_root),
            _override_from_env("PREVIEW_VISIBILITY", "all"),
        ]

    def teardown_test_environment(self, **kwargs):
        for restore in reversed(self._restore_settings):
            restore()
        shutil.rmtree(self._media_root, ignore_errors=True)
        super().teardown_test_environment(**kwargs)


def _override_from_env(name, value):
    """Set a setting the settings module reads from the environment.

    --parallel workers re-import settings from scratch (the default start
    method is no longer fork), so override_settings alone would not reach
    them. Both halves are needed: the env var for processes that import
    settings after this point, override_settings for the current one.
    Returns the function that undoes both.
    """
    previous = os.environ.get(name)
    os.environ[name] = value
    override = override_settings(**{name: value})
    override.enable()

    def restore():
        override.disable()
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    return restore
