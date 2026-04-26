"""End-to-end WebDAV tests via a real ``davfs2`` filesystem mount.

These tests boot a Django ``LiveServer`` (which runs the full WSGI
dispatcher, including the ``/dav`` WebDAV app), mount it through the
kernel with ``mount.davfs``, then exercise the server through plain
filesystem operations: ``touch``, ``echo``, ``cat``, ``mkdir``, ``rm``,
``mv``, ``cp``.

The point is to catch interop issues that pure HTTP-level tests miss:
LOCK-before-PUT loops, two-phase write-then-rename patterns, frequent
``stat`` calls translated into ``PROPFIND Depth: 0``, URL-encoding
edge cases, trailing-slash handling, and the uploads triggered on
file ``close()`` rather than on every ``write()``.

These tests require:
    * Linux (davfs2 is FUSE/Linux-only)
    * ``mount.davfs`` available in PATH
    * either running as root, or ``mount.davfs`` being SUID-root, or
      passwordless ``sudo -n`` available

When any prerequisite is missing the tests are skipped — except in CI,
where setting ``WORKSPACE_DAVFS2_TESTS=1`` turns missing prerequisites
into a hard failure so a regression in the CI image is loud.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


# --------------------------------------------------------------------------- #
# Prerequisite detection
# --------------------------------------------------------------------------- #

REQUIRE = os.environ.get("WORKSPACE_DAVFS2_TESTS", "").lower() in {"1", "true", "yes", "on"}


def _mount_davfs_path() -> str | None:
    return (
        shutil.which("mount.davfs")
        or ("/sbin/mount.davfs" if Path("/sbin/mount.davfs").exists() else None)
        or ("/usr/sbin/mount.davfs" if Path("/usr/sbin/mount.davfs").exists() else None)
    )


def _can_mount_directly(path: str) -> bool:
    """True iff the current process can invoke ``mount.davfs`` without sudo."""
    if os.geteuid() == 0:
        return True
    try:
        st = os.stat(path)
    except OSError:
        return False
    return bool(st.st_mode & 0o4000)  # SUID bit


def _has_passwordless_sudo() -> bool:
    if not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"], capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _check_prereqs() -> str | None:
    """Return ``None`` when everything is in place, else a human reason."""
    if sys.platform != "linux":
        return "davfs2 mount tests require Linux"
    bin_path = _mount_davfs_path()
    if not bin_path:
        return "mount.davfs binary not found (install the davfs2 package)"
    if _can_mount_directly(bin_path):
        return None
    if _has_passwordless_sudo():
        return None
    return (
        "mount.davfs needs root: either run tests as root, "
        "make the binary SUID-root, or enable passwordless sudo"
    )


_PREREQ_REASON = _check_prereqs()


def _sudo_prefix() -> list[str]:
    bin_path = _mount_davfs_path()
    if bin_path and _can_mount_directly(bin_path):
        return []
    return ["sudo", "-n"]


# When the env var is set we *want* failures to surface (CI), so don't
# decorate the class with ``skipUnless``.  Otherwise — local dev — skip
# silently when the binary or perms are missing.
_SKIP_SILENTLY = _PREREQ_REASON is not None and not REQUIRE


# --------------------------------------------------------------------------- #
# Test base class
# --------------------------------------------------------------------------- #


@unittest.skipIf(_SKIP_SILENTLY, _PREREQ_REASON or "")
@override_settings(DEFAULT_FILE_STORAGE="django.core.files.storage.InMemoryStorage")
class WebDAVDavfs2MountTests(LiveServerTestCase):
    """Boot a live server, mount its ``/dav`` endpoint via davfs2, exercise it.

    Each test gets a fresh mount point and a fresh user, so cache state
    inside ``mount.davfs`` cannot leak across tests.
    """

    USERNAME = "davfsuser"
    PASSWORD = "DavFsTestPass-1!"

    # Bind the live server to all interfaces so 127.0.0.1 is reachable
    # even when Django picked ``localhost`` (which can resolve to ::1).
    host = "127.0.0.1"

    # Polling budget for davfs2's deferred upload on close().  CI runners
    # are slow enough that 15s is a comfortable upper bound; the
    # ``delay_upload=0`` mount option keeps the typical wait under 1s.
    DB_POLL_TIMEOUT = 15.0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Final hard check: when REQUIRE is set, fail loudly here so the
        # class-skip decorator doesn't silently swallow a CI misconfig.
        if REQUIRE and _PREREQ_REASON is not None:
            raise RuntimeError(
                f"WORKSPACE_DAVFS2_TESTS=1 but prerequisites missing: {_PREREQ_REASON}"
            )
        cls._tmp_root = Path(tempfile.mkdtemp(prefix="dav-mount-tests-"))

    @classmethod
    def tearDownClass(cls):
        try:
            shutil.rmtree(cls._tmp_root, ignore_errors=True)
        finally:
            super().tearDownClass()

    # -- per-test setup/teardown ------------------------------------------- #

    def setUp(self):
        super().setUp()
        # Per-test username avoids any chance of credential collision
        # with cached basic-auth state inside neon/davfs2.
        self.username = f"{self.USERNAME}-{uuid.uuid4().hex[:8]}"
        self.user = User.objects.create_user(
            username=self.username,
            email=f"{self.username}@test.local",
            password=self.PASSWORD,
        )
        self.mp = self._tmp_root / f"mp-{uuid.uuid4().hex[:8]}"
        self.mp.mkdir()
        self._mounted = False
        self._mount()

    def tearDown(self):
        try:
            self._umount()
        finally:
            super().tearDown()

    # -- mount/umount ------------------------------------------------------ #

    def _dav_url(self) -> str:
        parsed = urllib.parse.urlparse(self.live_server_url)
        # Force IPv4 host: ``localhost`` resolves to ::1 first on some
        # boxes and davfs2/neon don't always retry on AF_INET6 failure.
        return f"http://127.0.0.1:{parsed.port}/dav/"

    def _mount(self):
        url = self._dav_url()
        # Tuned mount options:
        #   delay_upload=0   — push to server immediately on close()
        #   cache_size=1     — minimum (MiB), less stale data to chase
        #   gui_optimize=0   — disable directory pre-fetch
        #   use_locks=0      — skip LOCK/UNLOCK round-trips (test isolation)
        #   uid/gid          — remap ownership so the unprivileged test user
        #                      can read/write files even when mount runs as root
        opts = ",".join([
            f"username={self.username}",
            f"uid={os.geteuid()}",
            f"gid={os.getegid()}",
            "dir_mode=755",
            "file_mode=644",
            "delay_upload=0",
            "cache_size=1",
            "gui_optimize=0",
            "use_locks=0",
        ])
        cmd = _sudo_prefix() + [
            _mount_davfs_path(), url, str(self.mp), "-o", opts,
        ]
        proc = subprocess.run(
            cmd,
            input=f"{self.PASSWORD}\n",  # stdin: password (username via -o)
            text=True,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip() or "unknown error"
            raise RuntimeError(
                f"mount.davfs failed (code {proc.returncode}): {msg}\n"
                f"command: {' '.join(cmd)}"
            )
        self._mounted = True
        # Wait until the kernel reports the mount as live before any
        # filesystem call lands on it.
        if not self._wait_until(self.mp.is_mount, timeout=10):
            raise RuntimeError(f"mount.davfs returned 0 but {self.mp} is not a mountpoint")

    def _umount(self):
        if not self._mounted:
            return
        # First try a clean umount; if the davfs2 daemon is still flushing,
        # fall back to lazy umount so the cleanup never wedges the test.
        for args in (["umount"], ["umount", "-l"]):
            r = subprocess.run(
                _sudo_prefix() + args + [str(self.mp)],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0:
                self._mounted = False
                return
        # Last-resort log to make a stuck mount obvious in CI output.
        sys.stderr.write(f"[davfs2] failed to umount {self.mp}\n")

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _wait_until(pred, timeout=5.0, interval=0.1):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if pred():
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return bool(pred())

    def _wait_for_file(self, **filters) -> File:
        """Block until a ``File`` row matching ``filters`` exists (or fail)."""
        ok = self._wait_until(
            lambda: File.objects.filter(**filters).exists(),
            timeout=self.DB_POLL_TIMEOUT,
        )
        self.assertTrue(
            ok,
            f"timed out waiting for File row matching {filters} "
            f"(maybe davfs2 didn't flush, or the server rejected the upload)",
        )
        return File.objects.filter(**filters).first()

    def _wait_for_no_file(self, **filters):
        ok = self._wait_until(
            lambda: not File.objects.filter(**filters).exists(),
            timeout=self.DB_POLL_TIMEOUT,
        )
        self.assertTrue(
            ok,
            f"timed out waiting for File row {filters} to disappear",
        )

    @staticmethod
    def _fsync_close(path: Path):
        """Open + fsync + close to push pending davfs2 writes to the server."""
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # =====================================================================
    # Tests
    # =====================================================================

    # --- mount sanity ----------------------------------------------------- #

    def test_mount_lists_empty(self):
        """Fresh user → ls on the mountpoint returns nothing."""
        entries = list(self.mp.iterdir())
        self.assertEqual(entries, [])

    def test_mount_sees_preexisting_file(self):
        """File created server-side appears under the mountpoint."""
        FileService.create_file(self.user, "preexisting.txt", mime_type="text/plain")
        # davfs2 caches directory listings; force a refresh by re-stat'ing
        # the mountpoint after a brief beat.
        self._wait_until(
            lambda: "preexisting.txt" in (p.name for p in self.mp.iterdir()),
            timeout=10,
        )
        names = sorted(p.name for p in self.mp.iterdir())
        self.assertIn("preexisting.txt", names)

    # --- create / write --------------------------------------------------- #

    def test_touch_creates_empty_file(self):
        """``touch foo`` → server stores an empty File row."""
        target = self.mp / "empty.txt"
        target.touch()
        self._fsync_close(target)
        f = self._wait_for_file(
            owner=self.user, name="empty.txt", deleted_at__isnull=True,
        )
        self.assertEqual(f.size, 0)
        self.assertEqual(f.node_type, File.NodeType.FILE)

    def test_write_then_read_roundtrip(self):
        """``echo data > foo`` then ``cat foo`` returns the same bytes."""
        target = self.mp / "hello.txt"
        target.write_bytes(b"hello davfs\n")
        f = self._wait_for_file(
            owner=self.user, name="hello.txt", deleted_at__isnull=True,
        )
        self.assertEqual(f.size, len(b"hello davfs\n"))
        # Read back through the mount — exercises GET on the same connection
        # davfs2 just used for PUT, after its local cache settles.
        self.assertEqual(target.read_bytes(), b"hello davfs\n")

    def test_overwrite_existing_file(self):
        """Re-writing an existing path replaces its contents on the server."""
        target = self.mp / "rewrite.txt"
        target.write_bytes(b"v1")
        self._wait_for_file(owner=self.user, name="rewrite.txt", size=2)

        target.write_bytes(b"version-two")
        f = self._wait_for_file(
            owner=self.user, name="rewrite.txt", deleted_at__isnull=True, size=11,
        )
        f.content.open("rb")
        try:
            self.assertEqual(f.content.read(), b"version-two")
        finally:
            f.content.close()

    def test_16mib_file_streams_through(self):
        """A 16 MiB upload streams through ``_StreamingWriteBuffer`` intact.

        The buffer flushes every ``DjangoFile.DEFAULT_CHUNK_SIZE`` (64 KiB),
        so 16 MiB triggers ~256 flushes — that's the path real photo/
        video uploads take, and the one TCP-backpressure was designed for.
        Content is verified by SHA-256 to avoid keeping two 16 MiB buffers
        in memory at once.
        """
        import hashlib

        size = 16 * 1024 * 1024  # 16 MiB
        pattern = bytes(range(256))  # all byte values, catches alignment bugs
        payload = (pattern * (size // 256))[:size]
        self.assertEqual(len(payload), size)
        expected_sha = hashlib.sha256(payload).hexdigest()

        target = self.mp / "big.bin"
        target.write_bytes(payload)

        # 16 MiB on a slow CI runner can take longer than the default
        # 15 s budget, so wait on the size landing in DB explicitly.
        ok = self._wait_until(
            lambda: File.objects.filter(
                owner=self.user, name="big.bin", size=size,
                deleted_at__isnull=True,
            ).exists(),
            timeout=60.0,
        )
        self.assertTrue(ok, "16 MiB upload never reached the DB with full size")

        f = File.objects.get(
            owner=self.user, name="big.bin", deleted_at__isnull=True,
        )
        h = hashlib.sha256()
        f.content.open("rb")
        try:
            for chunk in iter(lambda: f.content.read(1024 * 1024), b""):
                h.update(chunk)
        finally:
            f.content.close()
        self.assertEqual(
            h.hexdigest(), expected_sha,
            "server-side bytes differ from what the client uploaded",
        )

    # --- folders ---------------------------------------------------------- #

    def test_mkdir_creates_folder(self):
        """``mkdir foo`` → MKCOL → folder row in DB."""
        (self.mp / "newdir").mkdir()
        f = self._wait_for_file(
            owner=self.user, name="newdir", node_type=File.NodeType.FOLDER,
            deleted_at__isnull=True,
        )
        self.assertIsNone(f.parent)

    def test_write_inside_subfolder(self):
        """Folder created via mkdir then a file created inside it."""
        sub = self.mp / "sub"
        sub.mkdir()
        self._wait_for_file(owner=self.user, name="sub", node_type=File.NodeType.FOLDER)

        (sub / "inside.txt").write_bytes(b"x" * 32)
        parent = File.objects.get(owner=self.user, name="sub", deleted_at__isnull=True)
        self._wait_for_file(
            owner=self.user, name="inside.txt", parent=parent, size=32,
        )

    def test_listing_reflects_server_state(self):
        """``ls`` after server-side mutations sees the new entries.

        davfs2 caches directory listings; with ``gui_optimize=0`` and a
        small cache they expire quickly enough for tests.
        """
        FileService.create_folder(self.user, "DirA")
        FileService.create_file(self.user, "fileA.txt", mime_type="text/plain")
        self._wait_until(
            lambda: {p.name for p in self.mp.iterdir()} >= {"DirA", "fileA.txt"},
            timeout=10,
        )
        names = {p.name for p in self.mp.iterdir()}
        self.assertIn("DirA", names)
        self.assertIn("fileA.txt", names)

    # --- delete ----------------------------------------------------------- #

    def test_rm_soft_deletes_file(self):
        """``rm foo`` → DELETE → File.deleted_at is set."""
        FileService.create_file(self.user, "to_remove.txt", mime_type="text/plain")
        self._wait_until(
            lambda: (self.mp / "to_remove.txt").exists(), timeout=10,
        )
        (self.mp / "to_remove.txt").unlink()
        self._wait_until(
            lambda: File.objects.get(
                owner=self.user, name="to_remove.txt"
            ).deleted_at is not None,
            timeout=self.DB_POLL_TIMEOUT,
        )
        f = File.objects.get(owner=self.user, name="to_remove.txt")
        self.assertIsNotNone(f.deleted_at)

    def test_rmdir_removes_empty_folder(self):
        FileService.create_folder(self.user, "tobedel")
        self._wait_until(lambda: (self.mp / "tobedel").exists(), timeout=10)
        (self.mp / "tobedel").rmdir()
        self._wait_until(
            lambda: File.objects.get(
                owner=self.user, name="tobedel"
            ).deleted_at is not None,
            timeout=self.DB_POLL_TIMEOUT,
        )

    # --- move / copy ------------------------------------------------------ #

    def test_mv_renames_in_same_dir(self):
        """``mv a b`` → MOVE same-folder → name changes, content preserved."""
        src = self.mp / "src.txt"
        src.write_bytes(b"move me")
        self._wait_for_file(owner=self.user, name="src.txt")

        src.rename(self.mp / "dst.txt")
        self._wait_for_file(
            owner=self.user, name="dst.txt", deleted_at__isnull=True, size=7,
        )
        self._wait_for_no_file(
            owner=self.user, name="src.txt", parent__isnull=True,
            deleted_at__isnull=True,
        )

    def test_mv_into_subfolder(self):
        """``mv file folder/`` → MOVE across folders → parent updated."""
        FileService.create_folder(self.user, "Inbox")
        src = self.mp / "letter.txt"
        src.write_bytes(b"hi")
        self._wait_for_file(owner=self.user, name="letter.txt")
        self._wait_until(lambda: (self.mp / "Inbox").exists(), timeout=10)

        src.rename(self.mp / "Inbox" / "letter.txt")

        inbox = File.objects.get(
            owner=self.user, name="Inbox", deleted_at__isnull=True,
        )
        self._wait_for_file(
            owner=self.user, name="letter.txt", parent=inbox,
            deleted_at__isnull=True,
        )

    def test_cp_duplicates_file(self):
        """``cp a b`` → COPY → both rows live, content matches."""
        src = self.mp / "orig.txt"
        src.write_bytes(b"copy me")
        self._wait_for_file(owner=self.user, name="orig.txt")

        # cp shells out by default; subprocess keeps cp's exact behavior
        # (some clients implement copy as GET + PUT, others as COPY —
        # GNU cp on a davfs2 mount goes through the COPY method).
        r = subprocess.run(
            ["cp", str(src), str(self.mp / "dup.txt")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

        dup = self._wait_for_file(
            owner=self.user, name="dup.txt", deleted_at__isnull=True,
        )
        # Original is still there:
        self.assertTrue(
            File.objects.filter(
                owner=self.user, name="orig.txt", deleted_at__isnull=True,
            ).exists()
        )
        dup.content.open("rb")
        try:
            self.assertEqual(dup.content.read(), b"copy me")
        finally:
            dup.content.close()

    # --- name encoding edge cases ---------------------------------------- #

    def test_filename_with_spaces(self):
        """Spaces in filenames must round-trip through URL encoding."""
        target = self.mp / "with space.txt"
        target.write_bytes(b"spaced")
        self._wait_for_file(
            owner=self.user, name="with space.txt", size=6,
        )

    def test_filename_with_utf8(self):
        """Non-ASCII filenames must round-trip (RFC 3986 percent-encoding)."""
        name = "café-éàü.txt"
        target = self.mp / name
        target.write_bytes(b"unicode")
        self._wait_for_file(owner=self.user, name=name, size=7)


# --------------------------------------------------------------------------- #
# Smoke check helpers — useful when debugging the suite locally
# --------------------------------------------------------------------------- #


class PrereqSelfCheck(unittest.TestCase):
    """One always-runnable test that documents the env and explains skips."""

    def test_explain_environment(self):
        # Always passes; the assertion only fires when something would
        # silently swallow a misconfigured WORKSPACE_DAVFS2_TESTS=1 run.
        if REQUIRE and _PREREQ_REASON is not None:
            self.fail(
                f"WORKSPACE_DAVFS2_TESTS=1 set but: {_PREREQ_REASON}"
            )
        # Otherwise just make the reason discoverable in -v output.
        if _PREREQ_REASON:
            sys.stderr.write(
                f"[davfs2] mount tests will be skipped: {_PREREQ_REASON}\n"
            )


