"""Refuse a full-row ``save()`` on a row that already exists.

``Model.save()`` writes every column from the instance in memory, so a
mutation meaning to change one field republishes the whole snapshot it was
loaded from - reverting anything another writer landed on the row in between.
The instance is loaded when a request begins and saved when it ends, so the
window is the request, and a lock or a content write that arrived inside it is
simply undone.

The mistake is invisible to the rest of a suite: under a single writer the row
ends up identical either way, and only a race tells the two apart. Nothing
short of asking the ORM directly can catch it, which is what this does - it
fails the test at the call site the moment a save carries no ``update_fields``.

Test code is exempt. A fixture assigning ``deleted_at`` and saving is building
state, not racing anybody, and holding it to the rule would cost churn and buy
nothing.
"""

import traceback
from pathlib import Path

from django.db.models.signals import pre_save

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Every frame of this module is the guard walking its own stack.
_SELF = str(Path(__file__).resolve().relative_to(_PROJECT_ROOT))


class FullRowWrite(AssertionError):
    """A save wrote every column of an existing row."""


def written_by_test_code(relative_path):
    """Whether *relative_path* is a test rather than application code."""
    return "tests" in Path(relative_path).parts


def _calling_source(forwarded_by):
    """Where the save came from: ``(path, line)``, or ``None`` outside the app.

    The innermost project frame is the answer, minus the frames *forwarded_by*
    names as machinery that only passes a save along. Those are matched as
    ``<project-relative path>:<function>`` rather than by file, so a model's
    own ``save()`` override is stepped over while a bare save *elsewhere* in
    the same module still gets the blame it deserves.
    """
    for frame in reversed(traceback.extract_stack()):
        try:
            relative = Path(frame.filename).resolve().relative_to(_PROJECT_ROOT)
        except ValueError:
            continue  # the standard library, site-packages, a test harness
        if relative.parts[0] != "workspace" or str(relative) == _SELF:
            continue
        if f"{relative}:{frame.name}" in forwarded_by:
            continue
        return str(relative), frame.lineno
    return None


def guard_full_row_writes(model, *, forwarded_by=(), exempt=written_by_test_code):
    """Fail whenever application code saves every column of an existing *model*.

    *forwarded_by* lists ``path:function`` frames that merely pass a save
    along - a model's own ``save()`` override sits between every caller and
    the signal, and blaming it would name one line for every mistake.

    Returns the receiver, so a caller can disconnect it again.
    """
    forwarded_by = frozenset(forwarded_by)

    def refuse_full_row_write(sender, instance, **kwargs):
        if kwargs.get("raw") or instance._state.adding:
            return  # an insert writes every column by definition
        if kwargs.get("update_fields") is not None:
            return
        source = _calling_source(forwarded_by)
        if source is None or exempt(source[0]):
            return
        path, line = source
        raise FullRowWrite(
            f"{path}:{line} saved every column of an existing "
            f"{sender.__name__} row. Pass update_fields=[...] naming the "
            f"columns this change writes: a bare save() republishes the "
            f"instance as it was loaded, undoing whatever landed on the row "
            f"while the request ran."
        )

    pre_save.connect(refuse_full_row_write, sender=model, weak=False)
    return refuse_full_row_write
