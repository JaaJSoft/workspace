"""Test helper: run the hourly catch-up for one reader, queued tasks included."""

from unittest.mock import patch

from workspace.files.tasks import catch_up, catch_up_file


def run_catch_up(name):
    """Run reader *name*'s pass and every task it queued; return how many it queued."""
    with patch.object(catch_up_file, "apply_async") as queue:
        stats = catch_up.apply(kwargs={"names": [name]}).get()
    for call in queue.call_args_list:
        catch_up_file.apply(args=call.kwargs["args"], kwargs=call.kwargs.get("kwargs"))
    return stats[name]
