"""Shared fixture for the public share page e2e tests.

A personal tree the visitor browses without an account:

    Docs/                (the share root)
      Sub/
        readme.md        (a markdown file the viewer renders)
      data.csv           (a typed icon the registry overrides)
"""

from __future__ import annotations

from django.core.files.base import ContentFile

from workspace.files.models import File, FileShareLink

README_TEXT = "Shared body text that only a rendered editor shows."


def build_shared_tree(test, *, username="share-owner", **link_fields):
    """Create the tree above on *test* and return the share link."""
    test.owner = test.create_user(username=username)
    test.root = File.objects.create(
        owner=test.owner, name="Docs", node_type=File.NodeType.FOLDER
    )
    test.sub = File.objects.create(
        owner=test.owner, name="Sub", node_type=File.NodeType.FOLDER, parent=test.root
    )
    test.readme = File.objects.create(
        owner=test.owner,
        name="readme.md",
        node_type=File.NodeType.FILE,
        parent=test.sub,
        type="markdown",
        mime_type="text/markdown",
    )
    body = f"# Hello\n\n{README_TEXT}\n".encode()
    test.readme.content = ContentFile(body, name="readme.md")
    test.readme.size = len(body)
    test.readme.save()
    # A second file in the same folder, so there is a neighbour to walk to.
    test.notes = File.objects.create(
        owner=test.owner,
        name="zz-notes.md",
        node_type=File.NodeType.FILE,
        parent=test.sub,
        type="markdown",
        mime_type="text/markdown",
    )
    notes_body = b"# Notes\n\nSecond file in the folder.\n"
    test.notes.content = ContentFile(notes_body, name="zz-notes.md")
    test.notes.size = len(notes_body)
    test.notes.save()
    test.csv = File.objects.create(
        owner=test.owner,
        name="data.csv",
        node_type=File.NodeType.FILE,
        parent=test.root,
        type="csv",
        mime_type="text/csv",
        size=12,
    )
    link_fields.setdefault("mode", FileShareLink.Mode.READ)
    test.link = FileShareLink.objects.create(
        file=test.root, created_by=test.owner, **link_fields
    )
    return test.link
