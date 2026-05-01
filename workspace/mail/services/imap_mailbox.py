"""IMAP mailbox-name helpers: quoting, decoding, type detection."""

import re

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


def _quote_mailbox(name):
    """Quote an IMAP mailbox name for use in commands.

    Python 3.14 removed automatic quoting from imaplib._command, so
    folder names containing spaces, brackets, etc. must be quoted by
    the caller.
    """
    name = name.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{name}"'


def _decode_mutf7(s):
    """Decode IMAP Modified UTF-7 (RFC 3501 §5.1.3) to Unicode."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == '&':
            j = s.index('-', i + 1)
            if j == i + 1:
                result.append('&')
            else:
                encoded = s[i + 1:j].replace(',', '/')
                result.append(('+' + encoded + '-').encode('ascii').decode('utf-7'))
            i = j + 1
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


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
    # Decode IMAP Modified UTF-7 for display
    if '&' in name and '-' in name:
        try:
            name = _decode_mutf7(name)
        except (ValueError, UnicodeDecodeError):
            # Malformed mUTF-7 shouldn't break display: keep the raw value.
            pass
    return name


def list_folders(conn):
    """List all IMAP folders. Returns [(flags, delimiter, name), ...]."""
    result = []
    status, data = conn.list()
    if status != 'OK':
        return result

    i = 0
    while i < len(data):
        item = data[i]
        if item is None:
            i += 1
            continue

        # imaplib returns tuples for literal folder names:
        #   (b'(\\flags) "/" {N}', b'folder name')
        if isinstance(item, tuple):
            header = item[0].decode('utf-8', errors='replace')
            name = item[1].decode('utf-8', errors='replace')
            match = re.match(r'\(([^)]*)\)\s+"([^"]+)"\s+\{', header)
            if match:
                flags = match.group(1)
                delimiter = match.group(2)
                result.append((flags, delimiter, name))
            i += 1
            continue

        decoded = item.decode('utf-8', errors='replace') if isinstance(item, bytes) else item
        # Parse: (\flags) "delimiter" "name"
        match = re.match(r'\(([^)]*)\)\s+"([^"]+)"\s+"?([^"]*)"?', decoded)
        if match:
            flags = match.group(1)
            delimiter = match.group(2)
            name = match.group(3).strip('"')
            result.append((flags, delimiter, name))
        i += 1
    return result
