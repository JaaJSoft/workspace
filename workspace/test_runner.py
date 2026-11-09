"""Project test runner.

``MEDIA_ROOT`` defaults to ``BASE_DIR``, so a test that saves file content
writes real blobs into the checkout, under ``files/users/<username>/``, and
nothing removes them afterwards. The runner owns the media root for the whole
session; a test class needs one of its own only when it reads the tree back
(``workspace.common.tests.media``).

It also arms the full-row-write guard over ``File``, the model the app mutates
from the most places at once, and over the models whose lifecycle state is
written by a request and a worker at the same time (``GUARDED_LIFECYCLE_MODELS``).
``workspace.common.tests.row_writes`` explains what it catches and why nothing
else can.

With ``WORKSPACE_TEST_STORAGE=s3`` every blob of the run goes to a bucket of
its own instead (``workspace.common.tests.s3``).

And it opens preview modules to everyone. Fixture users are regular users, and
under the production audience (``staff``) a preview module refuses every
request they make, so its own tests would test nothing but the refusal. The
tests of the audience itself override the setting back.

The vault's test algorithms (``VAULT_TEST_SUITES``) are enabled suite-wide
too, so the dispatch they exist to prove is exercised by every test. Unlike the
settings above they never go through the environment: a subprocess a test
starts (``manage.py migrate``, a gunicorn boot) would inherit the switch without
the runner marker, and vault.E002 would refuse to start it.
"""

import os
import shutil
import tempfile

from django.test.runner import DiscoverRunner, ParallelTestSuite, _init_worker
from django.test.utils import override_settings

VAULT_TEST_SWITCHES = {"VAULT_TEST_SUITES": True, "VAULT_TEST_RUNNER_ACTIVE": True}

# Rows whose status, claim token or trash stamp is written by several actors at
# once - a request, a Celery worker, a dispatcher. A full-row save on one of
# them republishes the state it was loaded in, which is how a cancel came back
# as COMPLETED and a fired one-shot came back active.
GUARDED_LIFECYCLE_MODELS = (
    "ai.AITask",
    "ai.AgentGoal",
    "ai.ScheduledMessage",
    "calendar.Poll",
    "chat.Message",
    "imports.ImportJob",
    "mail.MailAccount",
    "mail.MailFolder",
    "photos.Face",
    "projects.Sprint",
    "projects.Task",
    "vault.VaultEntry",
)


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


# Labels the guard watches in this process. A worker started by fork inherits
# both the receivers and this set, one started by forkserver or spawn neither.
_armed_labels = set()


def armed_row_write_guards():
    """The ``app.Model`` labels the full-row-write guard watches here."""
    return frozenset(_armed_labels)


def arm_row_write_guards():
    """Arm the full-row-write guard in this process, once.

    Called by the parent and again by every ``--parallel`` worker: a signal
    receiver does not survive the forkserver start method, which builds the
    worker from a fresh import, so a guard armed only in the parent is off
    exactly where the tests run.
    """
    if _armed_labels:
        return
    # Imported here rather than at module scope: the runner is built before
    # the app registry is ready, and this reaches for models.
    from django.apps import apps

    from workspace.common.tests.row_writes import guard_full_row_writes
    from workspace.files.models import File

    guard_full_row_writes(
        # File.save() forwards every caller's save to the signal;
        # naming it here keeps the blame on whoever asked for one.
        File,
        forwarded_by=("workspace/files/models.py:save",),
    )
    for label in GUARDED_LIFECYCLE_MODELS:
        guard_full_row_writes(apps.get_model(label))
    _armed_labels.update({File._meta.label, *GUARDED_LIFECYCLE_MODELS})


def _init_worker_with_media_root(counter, *args, **kwargs):
    _init_worker(counter, *args, **kwargs)
    arm_row_write_guards()
    # Under spawn and forkserver ``_init_worker`` has just re-imported the
    # settings from the environment, so the override has to follow it.
    worker_root = make_worker_media_root(os.environ["MEDIA_ROOT"])
    override_settings(MEDIA_ROOT=worker_root, **VAULT_TEST_SWITCHES).enable()
    from workspace.common.tests.s3 import ON_OBJECT_STORAGE, session_storages

    if ON_OBJECT_STORAGE:
        # One prefix of the run's bucket per worker, as one directory per
        # worker on a disk.
        override_settings(STORAGES=session_storages(f"worker-{os.getpid()}")).enable()


class MediaRootParallelTestSuite(ParallelTestSuite):
    init_worker = _init_worker_with_media_root


class MediaRootTestRunner(DiscoverRunner):
    """Points ``MEDIA_ROOT`` at a throwaway directory for the whole run."""

    parallel_test_suite = MediaRootParallelTestSuite

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        arm_row_write_guards()

        self._media_root = tempfile.mkdtemp(prefix="workspace-test-media-")
        # Derived from MEDIA_ROOT when the settings load, so it does not
        # follow the override on its own.
        upload_temp_dir = os.path.join(self._media_root, "tmp", "uploads")
        os.makedirs(upload_temp_dir)
        self._restore_settings = [
            _override_from_env("MEDIA_ROOT", self._media_root),
            _override_from_env("FILE_UPLOAD_TEMP_DIR", upload_temp_dir),
            _override_from_env("PREVIEW_VISIBILITY", "all"),
        ]
        vault_switches = override_settings(**VAULT_TEST_SWITCHES)
        vault_switches.enable()
        self._restore_settings.append(vault_switches.disable)
        from workspace.common.tests.s3 import ON_OBJECT_STORAGE, use_session_bucket

        if ON_OBJECT_STORAGE:
            self._restore_settings.append(use_session_bucket())

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
