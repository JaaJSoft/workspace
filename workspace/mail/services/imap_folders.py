"""IMAP folder operations: create, delete, rename, move."""

import logging

from django.db import transaction
from django.utils import timezone as dj_timezone

from workspace.mail.services.imap_connection import connect_imap
from workspace.mail.services.imap_mailbox import _display_name, _quote_mailbox

logger = logging.getLogger(__name__)


def create_folder(account, folder_name, parent_name=''):
    """Create an IMAP folder. Returns the created MailFolder.

    If parent_name is provided, the folder is created as a subfolder:
    ``parent_name + delimiter + folder_name``.
    """
    from ..models import MailFolder

    if parent_name:
        full_name = f'{parent_name}{account.imap_delimiter}{folder_name}'
    else:
        full_name = folder_name

    conn = connect_imap(account)
    try:
        status, data = conn.create(_quote_mailbox(full_name))
        if status != 'OK':
            raise Exception(f'IMAP CREATE failed: {data}')
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    folder = MailFolder.objects.create(
        account=account,
        name=full_name,
        display_name=_display_name(full_name),
        folder_type='other',
    )
    return folder


def delete_folder(account, folder):
    """Delete an IMAP folder and its local messages."""
    conn = connect_imap(account)
    try:
        # Close the folder first if selected, then delete
        status, data = conn.delete(_quote_mailbox(folder.name))
        if status != 'OK':
            raise Exception(f'IMAP DELETE failed: {data}')
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    folder.delete()


def rename_folder(account, folder, new_name):
    """Rename an IMAP folder."""
    conn = connect_imap(account)
    try:
        status, data = conn.rename(_quote_mailbox(folder.name), _quote_mailbox(new_name))
        if status != 'OK':
            raise Exception(f'IMAP RENAME failed: {data}')
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    folder.name = new_name
    folder.display_name = _display_name(new_name)
    folder.save(update_fields=['name', 'display_name', 'updated_at'])
    return folder


def move_folder(account, folder, new_parent_name):
    """Move an IMAP folder under a new parent (or to root).

    Uses IMAP RENAME to change the folder's full path. Also updates all
    child folders in the DB whose ``name`` starts with the old prefix.
    """
    from ..models import MailFolder

    delimiter = account.imap_delimiter or '/'
    old_name = folder.name

    # Use the wire-encoded leaf from folder.name, not folder.display_name:
    # display_name is Unicode-decoded (mUTF-7 -> Unicode) for the UI, while
    # IMAP RENAME must receive the original mUTF-7 wire form.
    leaf = old_name.rsplit(delimiter, 1)[-1]
    if new_parent_name:
        new_name = f'{new_parent_name}{delimiter}{leaf}'
    else:
        new_name = leaf

    if new_name == old_name:
        return folder

    conn = connect_imap(account)
    try:
        st, data = conn.rename(_quote_mailbox(old_name), _quote_mailbox(new_name))
        if st != 'OK':
            raise Exception(f'IMAP RENAME failed: {data}')
    finally:
        try:
            conn.logout()
        except Exception:
            # Best-effort cleanup: a logout failure on an already-broken
            # connection isn't actionable.
            pass

    # Update this folder and children in DB
    with transaction.atomic():
        folder.name = new_name
        folder.display_name = _display_name(new_name)
        folder.save(update_fields=['name', 'display_name', 'updated_at'])

        # Update child folders: any folder whose name starts with old_name + delimiter.
        # One UPDATE via bulk_update instead of N saves. Since each child gets a
        # distinct new name (path rewrite), we can't collapse to a single
        # QuerySet.update() - load, mutate in Python, then bulk_update.
        old_prefix = old_name + delimiter
        children = list(
            MailFolder.objects.filter(account=account, name__startswith=old_prefix)
        )
        if children:
            now = dj_timezone.now()
            for child in children:
                child.name = new_name + delimiter + child.name[len(old_prefix):]
                child.display_name = _display_name(child.name)
                child.updated_at = now  # bulk_update bypasses auto_now
            MailFolder.objects.bulk_update(
                children, ['name', 'display_name', 'updated_at'],
            )

    return folder
