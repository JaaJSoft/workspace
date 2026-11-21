"""An IMAP server held in memory, behind the part of IMAPClient the mail
services use.

A test describes mailboxes, lets the services loose on them and reads the
mailboxes back, so it pins what a server would see rather than which calls
were made. `test_imap_live` runs the same services against real servers:
that is where this fake is held to the protocol.

    with fake_imap() as server:
        uid = server.deliver("INBOX", subject="Hello")
        sync_folder_messages(account, inbox)
        assert server.flags("INBOX", uid) == set()

`server.commands` records every command of every connection, for the tests
that are about the wire (batch sizes, which extension was used), and
`server.fail("copy")` makes a command answer NO.
"""

import contextlib
import email
import email.policy
from unittest.mock import patch

from imapclient.exceptions import CapabilityError, IMAPClientError

FULL = frozenset({"IMAP4REV1", "ENABLE", "MOVE", "UIDPLUS", "CONDSTORE", "IDLE"})
# A server with none of the extensions the services look for.
BARE = frozenset({"IMAP4REV1"})


def raw_message(subject="Hello", message_id="", body="Body", **headers):
    lines = [
        "From: Sender <sender@example.com>",
        "To: me@example.com",
        f"Subject: {subject}",
        "Date: Mon, 02 Nov 2026 09:30:00 +0000",
    ]
    if message_id:
        lines.append(f"Message-ID: {message_id}")
    lines += [f"{name.replace('_', '-')}: {value}" for name, value in headers.items()]
    return ("\r\n".join([*lines, "", body, ""])).encode()


class FakeMessage:
    def __init__(self, uid, raw, flags, modseq):
        self.uid = uid
        self.raw = raw
        self.flags = set(flags)
        self.modseq = modseq


class FakeMailbox:
    def __init__(self, name, attributes, uidvalidity):
        self.name = name
        self.attributes = tuple(attributes)
        self.uidvalidity = uidvalidity
        self.uidnext = 1
        self.highestmodseq = 1
        self.messages = {}

    def touch(self):
        self.highestmodseq += 1
        return self.highestmodseq


class FakeImapServer:
    def __init__(self, capabilities=FULL, delimiter="/"):
        self.capabilities = set(capabilities)
        self.delimiter = delimiter
        self.mailboxes = {}
        self.connections = []
        self.commands = []
        self._failures = {}
        self._uidvalidity = 1000
        self.mailbox("INBOX")

    # -- describing and reading the mailboxes -----------------------------

    def mailbox(self, name, attributes=()):
        """Create the mailbox `name` (its wire form), or return it."""
        if name not in self.mailboxes:
            self._uidvalidity += 1
            self.mailboxes[name] = FakeMailbox(name, attributes, self._uidvalidity)
        return self.mailboxes[name]

    def deliver(self, mailbox, raw=None, flags=(), uid=None, **message):
        """Put a message in `mailbox` and return its UID.

        `uid` jumps the mailbox's counter forward, for tests about UIDs of a
        given size or order.
        """
        box = self.mailbox(mailbox)
        if uid is not None:
            box.uidnext = max(box.uidnext, uid)
        uid = box.uidnext
        box.uidnext += 1
        if raw is None:
            raw = raw_message(**message)
        box.messages[uid] = FakeMessage(uid, raw, flags, box.touch())
        return uid

    def remove(self, mailbox, *uids):
        """What another client's expunge does."""
        box = self.mailboxes[mailbox]
        for uid in uids:
            del box.messages[uid]
        box.touch()

    def set_flags(self, mailbox, uid, *flags):
        """What another client's STORE does."""
        box = self.mailboxes[mailbox]
        box.messages[uid].flags = set(flags)
        box.messages[uid].modseq = box.touch()

    def uids(self, mailbox):
        return sorted(self.mailboxes[mailbox].messages)

    def flags(self, mailbox, uid):
        return set(self.mailboxes[mailbox].messages[uid].flags)

    # -- steering -----------------------------------------------------------

    def fail(self, command, error=None, times=None):
        """Make `command` raise: a NO from the server unless `error` is given.

        For good, or for the next `times` calls.
        """
        if error is None:
            error = IMAPClientError(f"{command} failed: NO")
        self._failures[command] = [error, times]

    def recover(self, command):
        self._failures.pop(command, None)

    def named(self, *names):
        """The recorded commands called one of `names`."""
        return [command for command in self.commands if command[0] in names]

    def connect(self, account=None):
        client = FakeImapClient(self)
        self.connections.append(client)
        return client

    def _run(self, command, *args):
        self.commands.append((command, *args))
        failure = self._failures.get(command)
        if failure is None:
            return
        error, times = failure
        if times is not None:
            failure[1] = times - 1
            if failure[1] == 0:
                del self._failures[command]
        raise error


class FakeImapClient:
    folder_encode = False

    def __init__(self, server):
        self.server = server
        self.selected = None
        self.readonly = False
        self.closed = False
        self.literal = None
        self._codes = {}
        # imaplib's connection, as far as the services reach into it.
        self._imap = self

    # -- session ------------------------------------------------------------

    def has_capability(self, name):
        return name.upper() in self.server.capabilities

    def capabilities(self):
        return tuple(name.encode() for name in sorted(self.server.capabilities))

    def enable(self, *capabilities):
        self.server._run("enable", *capabilities)
        return list(capabilities)

    def logout(self):
        self.server._run("logout")
        self.closed = True

    def shutdown(self):
        self.closed = True

    # -- mailboxes ----------------------------------------------------------

    def list_folders(self):
        self.server._run("list")
        return [
            (
                tuple(attribute.encode() for attribute in box.attributes),
                self.server.delimiter.encode(),
                box.name.encode(),
            )
            for box in self.server.mailboxes.values()
        ]

    def _mailbox(self, name):
        try:
            return self.server.mailboxes[name]
        except KeyError:
            raise IMAPClientError(f"Mailbox doesn't exist: {name}") from None

    def select_folder(self, folder, readonly=False):
        self.server._run("select", folder, readonly)
        box = self._mailbox(folder)
        self.selected = box
        self.readonly = readonly
        answer = {
            b"EXISTS": len(box.messages),
            b"UIDNEXT": box.uidnext,
            b"UIDVALIDITY": box.uidvalidity,
        }
        if self.has_capability("CONDSTORE"):
            answer[b"HIGHESTMODSEQ"] = box.highestmodseq
        return answer

    def folder_status(self, folder, what=None):
        self.server._run("status", folder)
        box = self._mailbox(folder)
        return {b"UIDNEXT": box.uidnext, b"MESSAGES": len(box.messages)}

    def create_folder(self, folder):
        self.server._run("create", folder)
        if folder in self.server.mailboxes:
            raise IMAPClientError("create failed: Mailbox already exists")
        self.server.mailbox(folder)

    def delete_folder(self, folder):
        self.server._run("delete", folder)
        self._mailbox(folder)
        del self.server.mailboxes[folder]

    def rename_folder(self, old_name, new_name):
        self.server._run("rename", old_name, new_name)
        self._mailbox(old_name)
        prefix = old_name + self.server.delimiter
        for name in list(self.server.mailboxes):
            if name == old_name or name.startswith(prefix):
                box = self.server.mailboxes.pop(name)
                box.name = new_name + name[len(old_name) :]
                self.server.mailboxes[box.name] = box

    # -- messages -----------------------------------------------------------

    def _selected(self, write=False):
        if self.selected is None:
            raise IMAPClientError("No mailbox selected")
        if write and self.readonly:
            raise IMAPClientError("Mailbox is read-only")
        return self.selected

    def search(self, criteria="ALL"):
        self.server._run("search", criteria)
        box = self._selected()
        if isinstance(criteria, str):
            criteria = [criteria]
        found = sorted(box.messages)
        tokens = iter(criteria)
        for token in tokens:
            if token == "ALL":
                continue
            if token == "UID":
                first, colon, last = next(tokens).partition(":")
                if not colon:
                    last = first
                # "*" is the highest UID there is, and a range holds what is
                # between its ends whichever is the larger: "n:*" always
                # matches that highest UID, even when it is below n.
                highest = max(box.messages, default=0)
                low, high = sorted(
                    highest if end == "*" else int(end) for end in (first, last)
                )
                found = [uid for uid in found if low <= uid <= high]
            elif token == "HEADER":
                name, value = next(tokens), next(tokens)
                found = [
                    uid
                    for uid in found
                    if value.lower() in _header(box.messages[uid].raw, name).lower()
                ]
            else:
                raise NotImplementedError(f"search criterion {token!r}")
        return found

    def fetch(self, messages, data, modifiers=None):
        messages = [int(uid) for uid in messages]
        self.server._run("fetch", messages, list(data), modifiers)
        box = self._selected()
        changed_since = None
        for modifier in modifiers or ():
            name, _space, value = modifier.partition(" ")
            if name != "CHANGEDSINCE":
                raise NotImplementedError(f"fetch modifier {modifier!r}")
            changed_since = int(value)
        answer = {}
        for sequence, uid in enumerate(sorted(box.messages), 1):
            message = box.messages[uid]
            if uid not in messages:
                continue
            if changed_since is not None and message.modseq <= changed_since:
                continue
            item = {b"SEQ": sequence}
            for name in data:
                if name == "FLAGS":
                    item[b"FLAGS"] = tuple(flag.encode() for flag in message.flags)
                elif name == "BODY.PEEK[]":
                    item[b"BODY[]"] = message.raw
                elif name == "RFC822.SIZE":
                    item[b"RFC822.SIZE"] = len(message.raw)
                else:
                    raise NotImplementedError(f"fetch item {name!r}")
            answer[uid] = item
        return answer

    def _store(self, command, messages, flags, change):
        messages = [int(uid) for uid in messages]
        self.server._run(command, messages, list(flags))
        box = self._selected(write=True)
        for uid in messages:
            # A STORE naming a UID that is gone is not an error.
            if uid in box.messages:
                change(box.messages[uid].flags, set(flags))
                box.messages[uid].modseq = box.touch()

    def add_flags(self, messages, flags, silent=False):
        self._store("add_flags", messages, flags, set.update)

    def remove_flags(self, messages, flags, silent=False):
        self._store("remove_flags", messages, flags, set.difference_update)

    def _transfer(self, command, messages, folder, remove):
        messages = [int(uid) for uid in messages]
        self.server._run(command, messages, folder)
        box = self._selected(write=remove)
        target = self._mailbox(folder)
        copied = []
        for uid in messages:
            if uid not in box.messages:
                # Copying a UID that is gone answers OK with nothing copied.
                continue
            source = box.messages[uid]
            new_uid = self.server.deliver(folder, raw=source.raw, flags=source.flags)
            copied.append((uid, new_uid))
            if remove:
                del box.messages[uid]
                box.touch()
        if self.has_capability("UIDPLUS") and copied:
            sources = ",".join(str(old) for old, _new in copied)
            targets = ",".join(str(new) for _old, new in copied)
            self._codes["COPYUID"] = f"{target.uidvalidity} {sources} {targets}"

    def copy(self, messages, folder):
        self._transfer("copy", messages, folder, remove=False)

    def move(self, messages, folder):
        if not self.has_capability("MOVE"):
            raise CapabilityError("Server does not support MOVE capability")
        self._transfer("move", messages, folder, remove=True)

    def expunge(self, messages=None):
        self.server._run("expunge")
        box = self._selected(write=True)
        self._expunge(box, box.messages)

    def uid_expunge(self, messages):
        if not self.has_capability("UIDPLUS"):
            raise CapabilityError("Server does not support UIDPLUS capability")
        messages = [int(uid) for uid in messages]
        self.server._run("uid_expunge", messages)
        self._expunge(self._selected(write=True), messages)

    def _expunge(self, box, candidates):
        gone = [
            uid
            for uid in list(candidates)
            if uid in box.messages and "\\Deleted" in box.messages[uid].flags
        ]
        for uid in gone:
            del box.messages[uid]
        if gone:
            box.touch()

    def append(self, folder, msg, flags=(), msg_time=None):
        self.server._run("append", folder, list(flags))
        self._mailbox(folder)
        self.server.deliver(folder, raw=bytes(msg), flags=flags)

    # -- what the services reach under IMAPClient for -----------------------

    def response(self, name):
        code = self._codes.pop(name, None)
        return name, [code.encode() if code else None]

    def _normalise_folder(self, folder):
        return f'"{folder}"'

    def _simple_command(self, name, *args):
        if name != "APPEND":
            raise NotImplementedError(name)
        mailbox, flags, _date = args
        mailbox = mailbox.strip('"')
        try:
            self.server._run("append", mailbox, flags.strip("()").split())
        except IMAPClientError as error:
            return "NO", [str(error).encode()]
        if mailbox not in self.server.mailboxes:
            return "NO", [b"[TRYCREATE] Mailbox doesn't exist"]
        self.server.deliver(
            mailbox, raw=bytes(self.literal), flags=flags.strip("()").split()
        )
        return "OK", [b"Append completed."]


def _header(raw, name):
    return str(email.message_from_bytes(raw, policy=email.policy.default).get(name, ""))


@contextlib.contextmanager
def fake_imap(server=None, **options):
    """Hand every IMAP connection the services open to a `FakeImapServer`."""
    server = server or FakeImapServer(**options)
    with patch(
        "workspace.mail.services.imap_connection.connect_imap",
        side_effect=server.connect,
    ):
        yield server
