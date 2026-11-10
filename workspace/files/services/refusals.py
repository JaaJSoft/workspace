"""Why an action on one node was turned down, as a code a client can show.

Every service that acts on a single node for a user - copy, move, trash,
restore, purge, extract - raises :class:`Refused` with one of these codes, so
that a batch report, an endpoint and a test all speak the same language. The
exception the refusal came from stays reachable as ``__cause__``.
"""

from contextlib import contextmanager
from enum import StrEnum

from ._names import NameTaken
from .files import FileService, GroupRootFolder
from .locking import LockConflict, Relocated, StaleContent, conflicting_lock
from .quota import QuotaExceeded
from .scanning.policy import QuarantinedSource


class Refusal(StrEnum):
    NOT_ALLOWED = "not_allowed"
    TARGET = "target"
    INTO_ITSELF = "into_itself"
    NAME_TAKEN = "name_taken"
    NO_CONTENT = "no_content"
    NOT_REPLACEABLE = "not_replaceable"
    LOCKED = "locked"
    STALE = "stale"
    QUARANTINED = "quarantined"
    QUOTA = "quota"
    GROUP_ROOT = "group_root"
    ARCHIVE_TOO_LARGE = "archive_too_large"
    ARCHIVE_TOO_MANY_ENTRIES = "archive_too_many_entries"
    ARCHIVE_INVALID = "archive_invalid"


class Refused(Exception):
    """An action the rules turned down, for a reason with a name."""

    def __init__(self, reason, *, holder=None, current_hash=""):
        self.reason = reason
        self.holder = holder
        self.current_hash = current_hash
        super().__init__(str(reason))


@contextmanager
def refusals():
    """Turn the exceptions the file services raise into :class:`Refused`."""
    try:
        yield
    except QuarantinedSource as error:
        raise Refused(Refusal.QUARANTINED) from error
    except QuotaExceeded as error:
        raise Refused(Refusal.QUOTA) from error
    except LockConflict as error:
        raise Refused(Refusal.LOCKED, holder=error.holder) from error
    except StaleContent as error:
        raise Refused(Refusal.STALE, current_hash=error.current_hash) from error
    except Relocated as error:
        # The file a write was aimed at moved under it.
        raise Refused(Refusal.STALE) from error
    except GroupRootFolder as error:
        raise Refused(Refusal.GROUP_ROOT) from error
    except NameTaken as error:
        raise Refused(Refusal.NAME_TAKEN) from error


def require_action(action_id, user, node, *, in_trash=False):
    """Refuse unless the action registry offers *action_id* on *node*.

    *in_trash* judges the permission the way the trash does, where every
    member of a group may restore or purge what another member trashed.
    """
    # The registry's action modules import the services package.
    from workspace.files.actions import ActionRegistry

    permission = (
        FileService.trash_permission(user, node)
        if in_trash
        else FileService.get_permission(user, node)
    )
    if not ActionRegistry.is_action_available(
        action_id, user, node, permission=permission
    ):
        raise Refused(Refusal.NOT_ALLOWED)


def require_destination(user, parent):
    """Refuse unless *user* may add to *parent* (None is their own root)."""
    if parent is not None and not FileService.can_create_in(user, parent):
        raise Refused(Refusal.TARGET)


def require_unlocked(user, node):
    """Refuse when someone else holds a lock on *node*."""
    holder = conflicting_lock(node, user)
    if holder is not None:
        raise Refused(Refusal.LOCKED, holder=holder)
