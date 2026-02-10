"""IMAP service for syncing mail folders and messages."""

import email
import email.header
import email.utils
import imaplib
import logging
import re
from datetime import datetime, timezone

import nh3
from django.core.files.base import ContentFile
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

# Maximum messages to fetch on initial sync per folder
INITIAL_SYNC_LIMIT = 200
# Batch size for FETCH commands
FETCH_BATCH_SIZE = 50

# HTML sanitisation whitelist
NH3_ALLOWED_TAGS = {
    'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'dd', 'del', 'div', 'dl',
    'dt', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img',
    'li', 'ol', 'p', 'pre', 'q', 's', 'span', 'strong', 'sub', 'sup',
    'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'u', 'ul',
}

NH3_ALLOWED_ATTRIBUTES = {
    '*': {'class', 'style', 'dir', 'lang'},
    'a': {'href', 'target', 'title'},
    'img': {'src', 'alt', 'width', 'height', 'title'},
    'td': {'colspan', 'rowspan', 'align', 'valign'},
    'th': {'colspan', 'rowspan', 'align', 'valign'},
    'table': {'border', 'cellpadding', 'cellspacing', 'width'},
}

# Special-use folder attributes (RFC 6154)
SPECIAL_USE_MAP = {
    '\\Sent': 'sent',
    '\\Drafts': 'drafts',
    '\\Trash': 'trash',
    '\\Jstrash': 'trash',
    '\\Junk': 'spam',
    '\\Spam': 'spam',
    '\\Archive': 'archive',
    '\\All': 'archive',
}

# Fallback name-based detection
NAME_TYPE_MAP = {
    'inbox': 'inbox',
    'sent': 'sent',
    'sent mail': 'sent',
    'sent items': 'sent',
    'drafts': 'drafts',
    'draft': 'drafts',
    'trash': 'trash',
    'deleted': 'trash',
    'deleted items': 'trash',
    'bin': 'trash',
    'junk': 'spam',
    'spam': 'spam',
    'archive': 'archive',
    'all mail': 'archive',
}


def connect_imap(account):
    """Open and authenticate an IMAP connection for the given account."""
    if account.imap_use_ssl:
        conn = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
    else:
        conn = imaplib.IMAP4(account.imap_host, account.imap_port)
    conn.login(account.username, account.get_password())
    return conn


def test_imap_connection(account):
    """Test IMAP connectivity. Returns (success, error_message)."""
    try:
        conn = connect_imap(account)
        conn.logout()
        return True, None
    except Exception as e:
        return False, str(e)


def list_folders(conn):
    """List all IMAP folders. Returns [(flags, delimiter, name), ...]."""
    result = []
    status, data = conn.list()
    if status != 'OK':
        return result

    for item in data:
        if item is None:
            continue
        decoded = item.decode('utf-8', errors='replace') if isinstance(item, bytes) else item
        # Parse: (\flags) "delimiter" "name"
        match = re.match(r'\(([^)]*)\)\s+"([^"]+)"\s+"?([^"]*)"?', decoded)
        if match:
            flags = match.group(1)
            delimiter = match.group(2)
            name = match.group(3).strip('"')
            result.append((flags, delimiter, name))
    return result


def _detect_folder_type(name, flags):
    """Detect folder type from special-use attributes or name."""
    # Check RFC 6154 special-use attributes
    for attr, ftype in SPECIAL_USE_MAP.items():
        if attr.lower() in flags.lower():
            return ftype

    # Name-based detection
    lower_name = name.lower().split('/')[-1].split('.')[-1]
    if lower_name in NAME_TYPE_MAP:
        return NAME_TYPE_MAP[lower_name]

    # INBOX is always inbox
    if name.upper() == 'INBOX':
        return 'inbox'

    return 'other'


def _display_name(name):
    """Derive a human-readable display name from an IMAP folder name."""
    # Take last segment after / or .
    for sep in ('/', '.'):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    return name


def sync_folders(account):
    """Sync the list of IMAP folders for the given account to the database."""
    from workspace.mail.models import MailFolder

    conn = connect_imap(account)
    try:
        remote_folders = list_folders(conn)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    existing = {f.name: f for f in MailFolder.objects.filter(account=account)}
    remote_names = set()

    for flags, _delim, name in remote_folders:
        remote_names.add(name)
        folder_type = _detect_folder_type(name, flags)
        display = _display_name(name)

        if name in existing:
            folder = existing[name]
            changed = False
            if folder.folder_type != folder_type:
                folder.folder_type = folder_type
                changed = True
            if folder.display_name != display:
                folder.display_name = display
                changed = True
            if changed:
                folder.save(update_fields=['folder_type', 'display_name', 'updated_at'])
        else:
            MailFolder.objects.create(
                account=account,
                name=name,
                display_name=display,
                folder_type=folder_type,
            )

    # Remove folders that no longer exist remotely
    gone = set(existing.keys()) - remote_names
    if gone:
        MailFolder.objects.filter(account=account, name__in=gone).delete()


def sync_folder_messages(account, folder):
    """Incrementally sync messages for one folder."""
    from workspace.mail.models import MailMessage

    conn = connect_imap(account)
    try:
        status, data = conn.select(folder.name, readonly=True)
        if status != 'OK':
            logger.warning("Could not SELECT folder %s: %s", folder.name, data)
            return

        # Check UIDVALIDITY
        uid_validity = int(data[0])
        if folder.uid_validity and folder.uid_validity != uid_validity:
            # UIDVALIDITY changed — purge and re-sync
            logger.info("UIDVALIDITY changed for %s, resetting", folder.name)
            MailMessage.objects.filter(folder=folder).delete()
            folder.last_sync_uid = 0

        folder.uid_validity = uid_validity
        folder.save(update_fields=['uid_validity', 'updated_at'])

        # Search for new UIDs (always use UID SEARCH to get real UIDs)
        if folder.last_sync_uid > 0:
            status, search_data = conn.uid('SEARCH', None, f'UID {folder.last_sync_uid + 1}:*')
            if status != 'OK':
                _update_folder_counts(folder)
                return
            uid_list = search_data[0].split()
            uid_list = [u.decode() if isinstance(u, bytes) else u for u in uid_list if u]
            # Filter out the already-synced UID (server may include it)
            uid_list = [u for u in uid_list if int(u) > folder.last_sync_uid]
        else:
            # Initial sync: get all UIDs then take last N
            status, search_data = conn.uid('SEARCH', None, 'ALL')
            if status != 'OK':
                _update_folder_counts(folder)
                return
            all_uids = search_data[0].split()
            all_uids = [u.decode() if isinstance(u, bytes) else u for u in all_uids if u]
            if not all_uids:
                _update_folder_counts(folder)
                return
            # Limit initial sync
            if len(all_uids) > INITIAL_SYNC_LIMIT:
                all_uids = all_uids[-INITIAL_SYNC_LIMIT:]
            uid_list = all_uids

        if not uid_list:
            _update_folder_counts(folder)
            return

        max_uid = folder.last_sync_uid
        # Fetch in batches
        for i in range(0, len(uid_list), FETCH_BATCH_SIZE):
            batch = uid_list[i:i + FETCH_BATCH_SIZE]
            uid_set = ','.join(batch)
            status, msg_data = conn.uid('FETCH', uid_set, '(UID FLAGS RFC822)')
            if status != 'OK':
                continue

            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue
                # Parse UID from response
                uid_match = re.search(rb'UID (\d+)', response_part[0])
                if not uid_match:
                    continue
                uid = int(uid_match.group(1))

                # Parse flags
                flags_match = re.search(rb'FLAGS \(([^)]*)\)', response_part[0])
                flags_str = flags_match.group(1).decode() if flags_match else ''

                raw_email = response_part[1]
                try:
                    msg = _parse_message(raw_email, account, folder, uid, flags_str)
                    if msg:
                        max_uid = max(max_uid, uid)
                except Exception:
                    logger.exception("Failed to parse message UID %d in %s", uid, folder.name)

        # Update sync position
        if max_uid > folder.last_sync_uid:
            folder.last_sync_uid = max_uid
        _update_folder_counts(folder)

    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _update_folder_counts(folder):
    """Update message_count and unread_count from database."""
    from workspace.mail.models import MailMessage

    qs = MailMessage.objects.filter(folder=folder, deleted_at__isnull=True)
    folder.message_count = qs.count()
    folder.unread_count = qs.filter(is_read=False).count()
    folder.save(update_fields=['message_count', 'unread_count', 'last_sync_uid', 'updated_at'])


def _parse_message(raw_email, account, folder, uid, flags_str):
    """Parse a raw email and save it as a MailMessage."""
    from workspace.mail.models import MailAttachment, MailMessage

    # Check if already exists
    if MailMessage.objects.filter(folder=folder, imap_uid=uid).exists():
        return None

    msg = email.message_from_bytes(raw_email)

    # Headers
    subject = _decode_header(msg.get('Subject', ''))
    from_addr = _parse_address(msg.get('From', ''))
    to_addrs = [_parse_address(a) for a in (msg.get_all('To') or [])]
    cc_addrs = [_parse_address(a) for a in (msg.get_all('Cc') or [])]
    bcc_addrs = [_parse_address(a) for a in (msg.get_all('Bcc') or [])]
    reply_to = msg.get('Reply-To', '')
    message_id = msg.get('Message-ID', '')

    # Date
    date_str = msg.get('Date')
    date = None
    if date_str:
        try:
            parsed = email.utils.parsedate_to_datetime(date_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            date = parsed
        except Exception:
            pass

    # Body
    body_text = ''
    body_html = ''
    attachments_data = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get('Content-Disposition', ''))

            if 'attachment' in content_disposition:
                _collect_attachment(part, attachments_data)
            elif content_type == 'text/plain' and not body_text:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    body_text = payload.decode(charset, errors='replace')
            elif content_type == 'text/html' and not body_html:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    body_html = payload.decode(charset, errors='replace')
            elif part.get('Content-ID'):
                # Inline attachment
                _collect_attachment(part, attachments_data, is_inline=True)
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            decoded = payload.decode(charset, errors='replace')
            if content_type == 'text/html':
                body_html = decoded
            else:
                body_text = decoded

    # Sanitise HTML
    if body_html:
        body_html = nh3.clean(
            body_html,
            tags=NH3_ALLOWED_TAGS,
            attributes=NH3_ALLOWED_ATTRIBUTES,
        )

    # Snippet from text body
    snippet = ''
    if body_text:
        snippet = body_text[:300].replace('\n', ' ').strip()

    # Flags
    is_read = '\\Seen' in flags_str
    is_starred = '\\Flagged' in flags_str
    is_draft = '\\Draft' in flags_str

    # Flatten to_addrs (each element may contain multiple addresses)
    to_flat = _flatten_addresses(to_addrs)
    cc_flat = _flatten_addresses(cc_addrs)
    bcc_flat = _flatten_addresses(bcc_addrs)

    mail_msg = MailMessage.objects.create(
        account=account,
        folder=folder,
        message_id=message_id[:512],
        imap_uid=uid,
        subject=subject[:1000],
        from_address=from_addr,
        to_addresses=to_flat,
        cc_addresses=cc_flat,
        bcc_addresses=bcc_flat,
        reply_to=reply_to[:255],
        date=date,
        snippet=snippet,
        body_text=body_text,
        body_html=body_html,
        is_read=is_read,
        is_starred=is_starred,
        is_draft=is_draft,
        has_attachments=bool(attachments_data),
    )

    # Save attachments
    for att_data in attachments_data:
        MailAttachment.objects.create(
            message=mail_msg,
            filename=att_data['filename'][:255],
            content_type=att_data['content_type'][:255],
            size=len(att_data['data']),
            content=ContentFile(att_data['data'], name=att_data['filename']),
            content_id=att_data.get('content_id', '')[:255],
            is_inline=att_data.get('is_inline', False),
        )

    return mail_msg


def _collect_attachment(part, attachments_data, is_inline=False):
    """Extract attachment data from an email part."""
    filename = part.get_filename()
    if filename:
        filename = _decode_header(filename)
    else:
        ext = part.get_content_type().split('/')[-1]
        filename = f'attachment.{ext}'

    payload = part.get_payload(decode=True)
    if payload:
        attachments_data.append({
            'filename': filename,
            'content_type': part.get_content_type(),
            'data': payload,
            'content_id': (part.get('Content-ID') or '').strip('<>'),
            'is_inline': is_inline,
        })


def _decode_header(value):
    """Decode an RFC 2047 encoded header value."""
    if not value:
        return ''
    decoded_parts = email.header.decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ''.join(result)


def _parse_address(addr_string):
    """Parse an email address string into {name, email}."""
    if not addr_string:
        return {'name': '', 'email': ''}
    decoded = _decode_header(addr_string)
    name, email_addr = email.utils.parseaddr(decoded)
    return {'name': name, 'email': email_addr}


def _flatten_addresses(addr_list):
    """Flatten a list of parsed addresses (some may have been parsed from combined strings)."""
    result = []
    for addr in addr_list:
        if isinstance(addr, dict):
            if addr.get('email'):
                result.append(addr)
        elif isinstance(addr, list):
            result.extend(addr)
    return result


def mark_read(account, message):
    """Mark a message as read on the IMAP server."""
    _set_flag(account, message, '\\Seen', True)


def mark_unread(account, message):
    """Mark a message as unread on the IMAP server."""
    _set_flag(account, message, '\\Seen', False)


def star_message(account, message):
    """Star a message on the IMAP server."""
    _set_flag(account, message, '\\Flagged', True)


def unstar_message(account, message):
    """Unstar a message on the IMAP server."""
    _set_flag(account, message, '\\Flagged', False)


def delete_message(account, message):
    """Mark a message as deleted on the IMAP server."""
    _set_flag(account, message, '\\Deleted', True)
    conn = connect_imap(account)
    try:
        conn.select(message.folder.name)
        conn.uid('STORE', str(message.imap_uid), '+FLAGS', '(\\Deleted)')
        conn.expunge()
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _set_flag(account, message, flag, add):
    """Add or remove an IMAP flag on a message."""
    conn = connect_imap(account)
    try:
        conn.select(message.folder.name)
        op = '+FLAGS' if add else '-FLAGS'
        conn.uid('STORE', str(message.imap_uid), op, f'({flag})')
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def sync_account(account):
    """Full sync: folders then messages for each folder."""
    from workspace.mail.models import MailFolder

    sync_folders(account)
    for folder in MailFolder.objects.filter(account=account):
        try:
            sync_folder_messages(account, folder)
        except Exception:
            logger.exception("Failed to sync folder %s for %s", folder.name, account.email)

    account.last_sync_at = dj_timezone.now()
    account.last_sync_error = ''
    account.save(update_fields=['last_sync_at', 'last_sync_error', 'updated_at'])
