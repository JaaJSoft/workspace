"""Every directory the app stores blobs under is one ``copy_blobs`` copies.

``BLOB_ROOTS`` is an allowlist, because the media directory also holds what
is not a blob. A module that starts storing under a new top-level directory
without adding it there loses those blobs the day the instance moves to
another backend, and nothing fails until then: the rows are copied with the
database and point at keys the destination never received.

The storage names the app builds are f-strings that open on a literal
directory (``f"thumbnails/{uuid}.webp"``), so those are what this reads. A
name built another way - a constant joined later - is out of its sight.
"""

import ast
import re
from pathlib import Path

from django.test import SimpleTestCase

import workspace
from workspace.core.management.commands.copy_blobs import BLOB_ROOTS

ROOT = Path(workspace.__file__).parent

# Literal directories, then the first interpolation: "avatars/groups/{...}".
_DIRECTORY_PREFIX = re.compile(r"([a-z][a-z_-]*)(/[a-z_-]+)*/")

# f-strings of that shape that name no blob, by file.
NOT_STORAGE_NAMES = {
    "photos/services/details.py": {"f"},  # an aperture, "f/2.8"
}


def _source_files():
    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        # A migration names the directories of its time, a test its fixtures'.
        if "tests" in parts or "migrations" in parts:
            continue
        yield path


def _roots(tree):
    """``(line, top-level directory)`` of every path-shaped f-string."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or len(node.values) < 2:
            continue
        head = node.values[0]
        if not isinstance(head, ast.Constant) or not isinstance(head.value, str):
            continue
        match = _DIRECTORY_PREFIX.fullmatch(head.value)
        if match:
            yield node.lineno, match.group(1)


class BlobRootsTests(SimpleTestCase):
    def test_every_storage_name_is_built_under_a_copied_root(self):
        missing = []
        for path in _source_files():
            relative = path.relative_to(ROOT).as_posix()
            ignored = NOT_STORAGE_NAMES.get(relative, set())
            for line, root in _roots(ast.parse(path.read_text(encoding="utf-8"))):
                if root not in BLOB_ROOTS and root not in ignored:
                    missing.append(f"workspace/{relative}:{line}: {root}/")
        self.assertEqual(
            missing,
            [],
            "Add the directory to BLOB_ROOTS (core/management/commands/"
            "copy_blobs.py), or to NOT_STORAGE_NAMES here when the string is "
            "not a storage name.",
        )

    def test_the_check_reads_the_top_level_directory(self):
        source = "\n".join(
            [
                'f"thumbnails/{uuid}.webp"',
                'f"avatars/groups/{conversation}.webp"',
                'f"video-frames/{file_id}/{row_id}.webp"',
            ]
        )

        found = [root for _line, root in sorted(_roots(ast.parse(source)))]

        self.assertEqual(found, ["thumbnails", "avatars", "video-frames"])

    def test_a_string_that_opens_on_no_directory_is_not_a_storage_name(self):
        source = "\n".join(
            [
                'f"{prefix}/{name}"',
                'f"/api/v1/files/{uuid}"',
                'f"Moved {source} to trash/{name}"',
                '"files/users"',
            ]
        )

        self.assertEqual(list(_roots(ast.parse(source))), [])
