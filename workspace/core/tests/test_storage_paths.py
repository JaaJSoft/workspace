"""Nothing outside ``workspace/common/storage`` reaches a blob through the
filesystem.

A blob may live where it has no filesystem path at all - an object store - so
code that asks the storage for a path, reads ``MEDIA_ROOT`` or builds its own
``FileSystemStorage`` works against a disk and breaks anywhere else, without a
single test noticing on the disk the suite runs on. ``BlobStorage`` has a verb
for everything that code needed: directories, moves, staged writes, a local
copy for an external tool.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

import workspace

ROOT = Path(workspace.__file__).parent

# The backends themselves, the settings that define MEDIA_ROOT and STORAGES,
# and the test runner pointing MEDIA_ROOT at a throwaway directory.
ALLOWED = (ROOT / "common" / "storage", ROOT / "settings", ROOT / "test_runner.py")

# Attribute names the app gives its FileFields: ``<row>.<field>.path`` is the
# blob's filesystem path, the same as ``storage.path(name)``.
FIELD_FILE_NAMES = {"content", "file", "voice_ref", "field_file"}


def _source_files():
    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        # Migrations are a historical record, written against a disk and
        # guarded where they touch it; tests build fixtures on the disk the
        # suite runs on.
        if "tests" in parts or "migrations" in parts:
            continue
        if any(path == allowed or allowed in path.parents for allowed in ALLOWED):
            continue
        yield path


def _owner(node):
    """The last name in the expression an attribute is read from."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _offences(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            owner = _owner(node.value) or ""
            if node.attr == "path" and (
                "storage" in owner or owner in FIELD_FILE_NAMES
            ):
                yield node.lineno, f"{owner}.path"
            elif node.attr in ("location", "base_location") and "storage" in owner:
                yield node.lineno, f"{owner}.{node.attr}"
            elif node.attr == "MEDIA_ROOT":
                yield node.lineno, "MEDIA_ROOT"
        elif isinstance(node, ast.Name) and node.id == "FileSystemStorage":
            yield node.lineno, "FileSystemStorage"
        elif isinstance(node, ast.alias) and node.name == "FileSystemStorage":
            yield node.lineno, "FileSystemStorage import"


class StoragePathTests(SimpleTestCase):
    def test_no_code_reaches_a_blob_through_the_filesystem(self):
        offences = [
            f"{path.relative_to(ROOT.parent)}:{line}: {what}"
            for path in _source_files()
            for line, what in _offences(ast.parse(path.read_text(encoding="utf-8")))
        ]
        self.assertEqual(
            offences,
            [],
            "Go through the BlobStorage verbs (workspace/common/storage) instead "
            "of a filesystem path; only LocalBackend turns a name into one.",
        )

    def test_the_check_sees_each_way_in(self):
        source = "\n".join(
            [
                "default_storage.path(name)",
                "File._meta.get_field('content').storage.path(name)",
                "file_obj.content.path",
                "attachment.file.path",
                "default_storage.location",
                "settings.MEDIA_ROOT",
                "from django.core.files.storage import FileSystemStorage",
                "FileSystemStorage(location=root)",
            ]
        )

        found = [what for _line, what in sorted(_offences(ast.parse(source)))]

        self.assertEqual(
            found,
            [
                "default_storage.path",
                "storage.path",
                "content.path",
                "file.path",
                "default_storage.location",
                "MEDIA_ROOT",
                "FileSystemStorage import",
                "FileSystemStorage",
            ],
        )

    def test_the_tree_path_of_a_node_is_not_a_filesystem_path(self):
        source = "folder.path\nnode.path or node.get_path()\nrequest.path"

        self.assertEqual(list(_offences(ast.parse(source))), [])
