#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

# Kept in step with the `requires-python` floor in pyproject.toml by
# workspace/core/tests/test_python_version.py.
MINIMUM_PYTHON = (3, 14, 1)


def check_python_version():
    """Refuse to run on an interpreter older than the declared floor.

    A release candidate satisfies neither `>=3.14` nor `>=3.14.0` under PEP 440,
    yet uv installs one when it is the only 3.14 its index knows about, and the
    venv it then builds runs no command at all: the first pydantic model raises
    a bare `AssertionError` from a `typing._eval_type` keyword the final release
    added. Nothing in that traceback names the interpreter, so say it here.
    """
    if sys.version_info >= (*MINIMUM_PYTHON, "final", 0):
        return

    expected = ".".join(str(part) for part in MINIMUM_PYTHON)
    sys.stderr.write(
        f"This project needs Python {expected} or newer, and a final release -\n"
        "a release candidate breaks every command with an unrelated-looking\n"
        "AssertionError from pydantic.\n\n"
        f"  running:  {sys.version.split()[0]} ({sys.executable})\n"
        f"  expected: >= {expected}\n\n"
        "Rebuild the environment on a final release:\n\n"
        "  uv self update          # so uv's interpreter index carries it\n"
        f"  uv python install {expected}\n"
        "  uv sync --reinstall\n"
    )
    raise SystemExit(1)


def main():
    """Run administrative tasks."""
    check_python_version()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workspace.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
