"""Loading an index declaration from the command line, for the SQL commands."""

from django.core.management.base import CommandError
from django.utils.module_loading import import_string


def load_declaration(dotted_path, expected):
    """The object at *dotted_path*, refused unless it is an *expected*."""
    try:
        declaration = import_string(dotted_path)
    except ImportError as exc:
        raise CommandError(str(exc)) from exc
    if not isinstance(declaration, expected):
        names = " or ".join(
            kind.__name__
            for kind in (expected if isinstance(expected, tuple) else (expected,))
        )
        raise CommandError(f"{dotted_path} is not a {names}")
    return declaration


def write_blocks(stdout, blocks):
    """Print (title, text) pairs as `-- TITLE` blocks, ready to paste."""
    for title, text in blocks:
        stdout.write(f"-- {title}")
        stdout.write(text)
        stdout.write("")
