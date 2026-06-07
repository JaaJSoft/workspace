"""Helpers for releasing resources defensively."""

import logging

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)


def close_all(handles):
    """Close every handle in *handles*, swallowing per-handle failures.

    Use this in cleanup paths that hold several open file handles: a
    close() that raises must not prevent the remaining handles from being
    closed (one bad handle would otherwise leak all the others), and a
    cleanup path must never mask the exception that triggered it.
    """
    for handle in handles:
        try:
            handle.close()
        except Exception:
            # repr may embed a user-controlled filename, hence scrub().
            logger.warning("Failed to close handle %s", scrub(repr(handle)))
