"""Run batch file operations from a test, the way a client does.

``POST /api/v1/files/operations`` only hands the batch to a worker. These
helpers run the worker inside the request, so the response already carries
the finished report - whatever eager mode the settings happen to be in.
"""

from unittest.mock import patch

from workspace.files.tasks import run_file_operation

URL = "/api/v1/files/operations"


def _run_now(operation_uuid):
    return run_file_operation.apply(args=[operation_uuid])


def run_operation(client, kind, nodes=(), target=None, **item_fields):
    """POST an operation on *nodes* and return the response, run to its end.

    *nodes* are rows or UUIDs. *target* is a folder, a UUID or None for the
    root; it is only sent for the kinds that take one. *item_fields* go on
    every item (``on_conflict="replace"``).
    """
    body = {
        "kind": kind,
        "items": [
            {"uuid": str(getattr(node, "pk", node)), **item_fields} for node in nodes
        ],
    }
    if kind in ("move", "copy", "extract"):
        body["target"] = (
            str(getattr(target, "pk", target)) if target is not None else None
        )
    with patch.object(run_file_operation, "delay", side_effect=_run_now):
        return client.post(URL, body, format="json")


def outcome_of(response):
    """``(counts, [(name, reason), ...])`` of a finished operation."""
    data = response.json()
    return data["counts"], [
        (error["name"], error["reason"]) for error in data["errors"]
    ]
