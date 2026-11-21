"""IMAP folder operations: create, delete, rename, move."""

import logging

from django.db import transaction
from django.utils import timezone as dj_timezone

from workspace.mail.services.imap_connection import imap_session
from workspace.mail.services.imap_mailbox import _display_name, encode_name

logger = logging.getLogger(__name__)


def create_folder(account, folder_name, parent_name=""):
    """Create an IMAP folder. Returns the created MailFolder.

    `folder_name` is the name as the user typed it. If `parent_name` (the
    parent's stored name) is provided, the folder is created under it:
    ``parent_name + delimiter + folder_name``.
    """
    from ..models import MailFolder

    full_name = encode_name(folder_name)
    if parent_name:
        full_name = f"{parent_name}{account.imap_delimiter}{full_name}"

    with imap_session(account) as client:
        client.create_folder(full_name)

    folder = MailFolder.objects.create(
        account=account,
        name=full_name,
        display_name=_display_name(full_name),
        folder_type="other",
    )
    return folder


def delete_folder(account, folder):
    """Delete an IMAP folder and its local messages."""
    with imap_session(account) as client:
        client.delete_folder(folder.name)

    folder.delete()


def rename_folder(account, folder, new_display_name):
    """Give an IMAP folder another name, where it is.

    `new_display_name` is the name as the user typed it: a nested folder
    keeps its parent path, so Parent/Old becomes Parent/New and not New at
    the root.
    """
    delimiter = account.imap_delimiter or "/"
    parent_path, separator, _leaf = folder.name.rpartition(delimiter)
    new_name = f"{parent_path}{separator}{encode_name(new_display_name)}"
    return _rename(account, folder, new_name)


def move_folder(account, folder, new_parent_name):
    """Move an IMAP folder under a new parent (or to root)."""
    delimiter = account.imap_delimiter or "/"
    leaf = folder.name.rsplit(delimiter, 1)[-1]
    if new_parent_name:
        new_name = f"{new_parent_name}{delimiter}{leaf}"
    else:
        new_name = leaf
    return _rename(account, folder, new_name)


def _rename(account, folder, new_name):
    """RENAME `folder` to the stored name `new_name`, subfolders included.

    The server renames the whole subtree with the one command, so every row
    whose name starts with the old path follows: left behind, the next
    folder sync would take the new paths for new folders and the old rows
    for deleted ones, and import their messages again as strangers.
    """
    from ..models import MailFolder

    delimiter = account.imap_delimiter or "/"
    old_name = folder.name
    if new_name == old_name:
        return folder

    with imap_session(account) as client:
        client.rename_folder(old_name, new_name)

    with transaction.atomic():
        folder.name = new_name
        folder.display_name = _display_name(new_name)
        folder.save(update_fields=["name", "display_name", "updated_at"])

        # Each child gets a distinct new name (path rewrite), so this cannot
        # be a single QuerySet.update(): load, mutate, then bulk_update.
        old_prefix = old_name + delimiter
        children = list(
            MailFolder.objects.filter(account=account, name__startswith=old_prefix)
        )
        if children:
            now = dj_timezone.now()
            for child in children:
                child.name = new_name + delimiter + child.name[len(old_prefix) :]
                child.display_name = _display_name(child.name)
                child.updated_at = now  # bulk_update bypasses auto_now
            MailFolder.objects.bulk_update(
                children,
                ["name", "display_name", "updated_at"],
            )

    return folder
