"""AI tools for the Mail module.

Reading tools (`read_email`, `search_emails`) touch the local mirror only.
Everything below them reaches the account's IMAP or SMTP server, inline: the
tool loop streams to a waiting user, so each call is bounded by the socket
timeouts of the connection layer (`IMAP_TIMEOUT`, `SMTP_TIMEOUT`) and any
failure comes back as a sentence the model can relay rather than an
exception that would break the stream.

Sending is the one action no later turn can undo, so it is gated twice: the
bot needs `BotProfile.can_send_email`, and the message has to survive a
confirmation round in which the user sees exactly what will go out.
Drafting needs neither - the draft lands in the Drafts folder and the user
is the one who sends it.
"""

import json
import logging
import uuid as uuid_mod
from typing import Literal

from pydantic import BaseModel, Field

from workspace.ai.services.confirmation import (
    consume_bound_confirmation,
    request_bound_confirmation,
)
from workspace.ai.tool_registry import ToolProvider, tool
from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Names the kind of write a confirmation token may redeem, so one minted
# here can never be spent on another tool's pending action.
SEND_ACTION = "mail.send"

# The whole draft body the model is allowed to hand us in one call. A model
# looping on its own output would otherwise APPEND megabytes to Drafts.
BODY_MAX_CHARS = 20000


def _resolve_account(user, hint):
    """Return ``(account, error)`` for the identity a tool should act as.

    A user with one active account never has to name it. With several,
    guessing which identity to write as is the one mistake that cannot be
    walked back, so the choice goes to the model with the list in hand.
    """
    from workspace.mail.models import MailAccount

    accounts = list(
        MailAccount.objects.filter(owner=user, is_active=True).order_by("email")
    )
    if not accounts:
        return None, "No active mail account is configured on this workspace."

    hint = (hint or "").strip()
    if hint:
        for account in accounts:
            if str(account.uuid) == hint or account.email.casefold() == hint.casefold():
                return account, None
        return None, (
            f'No active mail account matches "{hint}". '
            f"Available: {', '.join(account.email for account in accounts)}."
        )

    if len(accounts) > 1:
        return None, (
            "Several mail accounts are configured, so the account argument is "
            "required: " + ", ".join(account.email for account in accounts) + "."
        )
    return accounts[0], None


def _resolve_message(user, uuid):
    """The user's non-deleted message with that UUID, or None."""
    from workspace.mail.models import MailMessage
    from workspace.mail.queries import user_account_ids

    return (
        MailMessage.objects.filter(
            uuid=uuid,
            account_id__in=user_account_ids(user),
            deleted_at__isnull=True,
        )
        .select_related("account", "folder")
        .first()
    )


def _resolve_folder(account, hint):
    """Return ``(folder, error)`` for a folder named by UUID or by name."""
    from workspace.mail.models import MailFolder

    hint = (hint or "").strip()
    folders = list(MailFolder.objects.filter(account=account).order_by("display_name"))
    if not hint:
        return None, "A folder is required."
    for folder in folders:
        if str(folder.uuid) == hint:
            return folder, None
    for folder in folders:
        if hint.casefold() in (folder.display_name.casefold(), folder.name.casefold()):
            return folder, None
    return None, (
        f'No folder named "{hint}" on {account.email}. '
        f"Call list_folders to see what exists."
    )


def _clean_addresses(values):
    """Strip, drop blanks, dedupe case-insensitively, reject non-addresses.

    Returns ``(addresses, error)``. The check is deliberately shallow - a
    local IMAP APPEND is not the place to re-litigate RFC 5322 - but it
    catches the model handing over a display name or a sentence.
    """
    cleaned = {}
    for value in values or []:
        address = str(value or "").strip().strip("<>")
        if not address:
            continue
        if "@" not in address or " " in address:
            return None, f'"{address}" is not a usable email address.'
        cleaned.setdefault(address.casefold(), address)
    return list(cleaned.values()), None


def _run_remote(action, fn, *args, **kwargs):
    """Run one IMAP/SMTP call, turning a failure into a readable message.

    Returns ``(result, error)``. Nothing raises out of here: the tool loop
    is streaming, and an exception would end the response instead of
    telling the user what did not happen.
    """
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        logger.warning("Mail tool could not %s: %s", scrub(action), scrub(str(exc)))
        return None, (
            f"The mail server did not complete the {action} — it may be "
            "unreachable or too slow to answer. Nothing was changed."
        )


class ReadEmailParams(BaseModel):
    # Typed as ``uuid.UUID`` so Pydantic rejects malformed values at the
    # tool-call boundary, before reaching ``filter(uuid=...)`` which would
    # otherwise raise ValidationError -> 500.
    uuid: uuid_mod.UUID = Field(description="The UUID of the email message to read.")


class SearchEmailsParams(BaseModel):
    query: str = Field(
        description="The search term to look for in email subject, content, or sender."
    )
    unread_only: bool = Field(
        default=False, description="If true, only return unread emails."
    )
    starred_only: bool = Field(
        default=False, description="If true, only return starred emails."
    )
    has_attachments: bool = Field(
        default=False, description="If true, only return emails with attachments."
    )


class DraftEmailParams(BaseModel):
    to: list[str] = Field(
        min_length=1, description="Recipient email addresses, at least one."
    )
    subject: str = Field(description="Subject line of the message.")
    body: str = Field(description="Plain-text body of the message.")
    cc: list[str] = Field(default_factory=list, description="Carbon-copy recipients.")
    bcc: list[str] = Field(
        default_factory=list, description="Blind carbon-copy recipients."
    )
    account: str = Field(
        default="",
        description=(
            "Which account to write as, given as its email address. Required "
            "only when several accounts are configured."
        ),
    )


class ReplyToEmailParams(BaseModel):
    uuid: uuid_mod.UUID = Field(
        description="UUID of the message being answered, as returned by search_emails."
    )
    body: str = Field(
        description=(
            "What to say, in plain text. The quoted original is appended "
            "automatically — do not repeat it."
        )
    )
    reply_all: bool = Field(
        default=False,
        description="If true, answer every recipient of the original, not only its sender.",
    )


class SendEmailParams(BaseModel):
    to: list[str] = Field(
        default_factory=list, description="Recipient email addresses, at least one."
    )
    subject: str = Field(default="", description="Subject line of the message.")
    body: str = Field(default="", description="Plain-text body of the message.")
    cc: list[str] = Field(default_factory=list, description="Carbon-copy recipients.")
    bcc: list[str] = Field(
        default_factory=list, description="Blind carbon-copy recipients."
    )
    account: str = Field(
        default="",
        description=(
            "Which account to send as, given as its email address. Required "
            "only when several accounts are configured."
        ),
    )
    confirmation_token: str = Field(
        default="",
        description=(
            "Leave empty on the first call. The first call shows the user the "
            "message and returns a token; pass that exact token back — and "
            "nothing else — once they have agreed to send it."
        ),
    )


class ListMailAccountScopedParams(BaseModel):
    account: str = Field(
        default="",
        description=(
            "Which account to look at, given as its email address. Required "
            "only when several accounts are configured."
        ),
    )


class MarkEmailParams(BaseModel):
    uuid: uuid_mod.UUID = Field(description="UUID of the message to flag.")
    action: Literal["read", "unread", "starred", "unstarred"] = Field(
        description="The flag to apply."
    )


class MoveEmailParams(BaseModel):
    uuid: uuid_mod.UUID = Field(description="UUID of the message to move.")
    folder: str = Field(
        description="Destination folder, named exactly as list_folders returned it."
    )


class DeleteEmailParams(BaseModel):
    uuid: uuid_mod.UUID = Field(description="UUID of the message to move to the trash.")


class LabelEmailParams(BaseModel):
    uuid: uuid_mod.UUID = Field(description="UUID of the message to label.")
    label: str = Field(description="Label name, exactly as list_labels returned it.")
    action: Literal["add", "remove"] = Field(
        default="add", description="Whether to attach or detach the label."
    )


class MailToolProvider(ToolProvider):
    @tool(
        badge_icon="📧",
        badge_label="Read email",
        badge_running_label="Reading email",
        detail_key="uuid",
        params=ReadEmailParams,
        concurrent=True,
    )
    def read_email(self, args, user, bot, conversation_id, context):
        """Read the full content of an email by its UUID: subject, sender, recipients, date, and body text. \
Call this after finding an email via search_emails to get its complete content, \
or when the user asks to read, open, or see the details of a specific email."""
        from workspace.mail.models import MailMessage
        from workspace.mail.queries import user_account_ids

        msg = (
            MailMessage.objects.filter(
                uuid=args.uuid,
                account_id__in=user_account_ids(user),
                deleted_at__isnull=True,
            )
            .select_related("folder", "account")
            .first()
        )
        if not msg:
            return "Email not found or access denied."

        def _fmt_addr(addr):
            if isinstance(addr, dict):
                name = addr.get("name", "")
                email = addr.get("email", addr.get("address", ""))
                return f"{name} <{email}>" if name else email
            return str(addr)

        parts = [
            f"Subject: {msg.subject or '(no subject)'}",
            f"From: {_fmt_addr({'name': msg.from_name, 'email': msg.from_email})}",
            f"To: {', '.join(_fmt_addr(a) for a in msg.to_addresses)}",
        ]
        if msg.cc_addresses:
            parts.append(f"Cc: {', '.join(_fmt_addr(a) for a in msg.cc_addresses)}")
        if msg.date:
            from workspace.users.services.settings import get_user_timezone

            local_date = msg.date.astimezone(get_user_timezone(user))
            parts.append(f"Date: {local_date.strftime('%Y-%m-%d %H:%M')}")
        parts.append(f"Folder: {msg.folder.display_name}")
        if msg.has_attachments:
            parts.append("Attachments: yes")
        parts.append("")
        body = msg.body_text or msg.snippet or "(no content)"
        parts.append(body[:3000])
        return "\n".join(parts)

    @tool(
        badge_icon="🔍",
        badge_label="Searched emails",
        badge_running_label="Searching emails",
        detail_key="query",
        params=SearchEmailsParams,
        concurrent=True,
    )
    def search_emails(self, args, user, bot, conversation_id, context):
        """Search through your emails by subject, content, or sender. \
Returns up to 20 matches with subject, sender, date, and folder. \
Call this when the user asks to find, look up, or locate an email. \
Use read_email with the returned UUID to get the full content."""
        query = args.query.strip()
        if not query:
            return "Error: query is required"

        from workspace.mail.models import MailMessage
        from workspace.mail.queries import user_account_ids
        from workspace.mail.search import fts_messages

        account_ids = user_account_ids(user)
        base = (
            MailMessage.objects.filter(
                account_id__in=account_ids, deleted_at__isnull=True
            )
            .exclude(folder__is_hidden=True)
            .select_related("folder")
        )
        if args.unread_only:
            base = base.filter(is_read=False)
        if args.starred_only:
            base = base.filter(is_starred=True)
        if args.has_attachments:
            base = base.filter(has_attachments=True)

        matches = fts_messages(base, query).order_by("-search_rank", "-date")[:20]
        if not matches:
            return f'No emails found matching "{query}".'

        from workspace.users.services.settings import get_user_timezone

        user_tz = get_user_timezone(user)
        results = []
        for msg in matches:
            results.append(
                {
                    "uuid": str(msg.uuid),
                    "subject": msg.subject or "(no subject)",
                    "from": msg.from_name or msg.from_email,
                    "date": msg.date.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
                    if msg.date
                    else "",
                    "folder": msg.folder.display_name if msg.folder else "",
                    "is_read": msg.is_read,
                    "has_attachments": msg.has_attachments,
                }
            )
        return json.dumps(results, ensure_ascii=False)

    # -- compose ------------------------------------------------------------

    def _save_draft(self, account, **fields):
        """Park a composed message in Drafts and describe what landed there."""
        from workspace.mail.models import MailFolder
        from workspace.mail.queries import special_folder
        from workspace.mail.services.drafts import save_composed_draft

        if special_folder(account, MailFolder.FolderType.DRAFTS) is None:
            return (
                f"{account.email} has no Drafts folder, so there is nowhere to "
                "put this message. Nothing was saved."
            )

        draft, error = _run_remote("draft", save_composed_draft, account, **fields)
        if error:
            return error
        if draft is None:
            return "The mail server refused the draft. Nothing was saved."
        return json.dumps(
            {
                "status": "draft saved",
                "uuid": str(draft.uuid),
                "account": account.email,
                "folder": draft.folder.display_name,
                "subject": draft.subject or "(no subject)",
                "to": [
                    entry.get("email", "") if isinstance(entry, dict) else str(entry)
                    for entry in draft.to_addresses
                ],
                "note": "Waiting in Drafts for the user to review and send.",
            },
            ensure_ascii=False,
        )

    @tool(
        badge_icon="✍️",
        badge_label="Drafted an email",
        badge_running_label="Drafting an email",
        detail_key="subject",
        params=DraftEmailParams,
    )
    def draft_email(self, args, user, bot, conversation_id, context):
        """Write an email and leave it in the user's Drafts folder for them to review and send. \
Nothing is sent: the user opens the draft in the mail app, edits it if needed, and sends it themselves. \
Prefer this over send_email whenever the user asks you to write, prepare or draft a message. \
Use reply_to_email instead when answering an existing message, so the reply threads properly."""
        account, error = _resolve_account(user, args.account)
        if error:
            return error

        to, error = _clean_addresses(args.to)
        if error:
            return error
        if not to:
            return "At least one recipient is required."
        cc, error = _clean_addresses(args.cc)
        if error:
            return error
        bcc, error = _clean_addresses(args.bcc)
        if error:
            return error

        return self._save_draft(
            account,
            to=to,
            subject=args.subject.strip(),
            body_text=args.body[:BODY_MAX_CHARS],
            cc=cc,
            bcc=bcc,
        )

    @tool(
        badge_icon="↩️",
        badge_label="Drafted a reply",
        badge_running_label="Drafting a reply",
        params=ReplyToEmailParams,
    )
    def reply_to_email(self, args, user, bot, conversation_id, context):
        """Draft a reply to an email and leave it in the user's Drafts folder for them to send. \
Recipients, subject, the quoted original and the threading headers are filled in from the \
message being answered — you only write what to say. Nothing is sent. \
Call read_email first when you need the content you are answering."""
        from workspace.mail.services.compose import build_reply
        from workspace.users.services.settings import get_user_timezone

        message = _resolve_message(user, args.uuid)
        if not message:
            return "Email not found or access denied."

        account = message.account
        reply = build_reply(
            message,
            account,
            args.body[:BODY_MAX_CHARS],
            get_user_timezone(user),
            reply_all=args.reply_all,
        )
        if not reply.to:
            return (
                "That message has no one to answer — it was sent from this "
                "account itself. Use draft_email to write a new message."
            )

        return self._save_draft(
            account,
            to=reply.to,
            subject=reply.subject,
            body_text=reply.body_text,
            cc=reply.cc,
            reply_message_id=message.uuid,
        )

    @tool(
        badge_icon="📤",
        badge_label="Sent an email",
        badge_running_label="Sending an email",
        detail_key="subject",
        params=SendEmailParams,
    )
    def send_email(self, args, user, bot, conversation_id, context):
        """Send an email immediately, in two steps. Call it first with the message and no \
confirmation_token: the user is shown exactly what would go out and asked to approve it. \
Once they agree, call it again passing back the token you were given — the message that gets \
sent is the one they saw, so the other arguments are ignored on that second call. \
Only use this when the user explicitly asks to send; draft_email is the right tool otherwise, \
and a sent email cannot be recalled."""
        profile = getattr(bot, "bot_profile", None)
        if not (profile and profile.can_send_email):
            return (
                "This assistant is not allowed to send email. Use draft_email "
                "or reply_to_email instead: the message lands in the user's "
                "Drafts folder and they send it themselves."
            )

        token = args.confirmation_token.strip()
        if token:
            return self._send_confirmed(user, conversation_id, token)

        account, error = _resolve_account(user, args.account)
        if error:
            return error

        to, error = _clean_addresses(args.to)
        if error:
            return error
        if not to:
            return "At least one recipient is required."
        cc, error = _clean_addresses(args.cc)
        if error:
            return error
        bcc, error = _clean_addresses(args.bcc)
        if error:
            return error

        subject = args.subject.strip()
        body = args.body[:BODY_MAX_CHARS]
        token, blocked = request_bound_confirmation(
            context,
            (
                f"Send this email to {', '.join(to)}?\n"
                f"Subject: {subject or '(no subject)'}"
            ),
            action=SEND_ACTION,
            user=user,
            conversation_id=conversation_id,
            payload={
                "account_id": str(account.uuid),
                "to": to,
                "cc": cc,
                "bcc": bcc,
                "subject": subject,
                "body": body,
            },
            options=["Yes, send it", "No, keep it as a draft", "No, cancel"],
        )
        if blocked:
            return blocked
        return json.dumps(
            {
                "status": "awaiting confirmation",
                "confirmation_token": token,
                "from": account.email,
                "to": to,
                "cc": cc,
                "bcc": bcc,
                "subject": subject or "(no subject)",
                "body": body,
                "note": (
                    "Nothing has been sent. The user has been shown this "
                    "message; call send_email again with confirmation_token "
                    "only if they agree to send it."
                ),
            },
            ensure_ascii=False,
        )

    def _send_confirmed(self, user, conversation_id, token):
        """Send the message a confirmation round pinned to this token.

        The payload comes from the pin rather than from the model's
        arguments, so the second call can only put on the wire what the user
        was actually shown.
        """
        from workspace.mail.services.sending import deliver_email

        pending = consume_bound_confirmation(SEND_ACTION, user, conversation_id, token)
        if pending is None:
            return (
                "That confirmation is unknown or has expired. Call send_email "
                "again without a token to show the user the message afresh."
            )

        account, error = _resolve_account(user, pending["account_id"])
        if error:
            return error

        delivery, error = _run_remote(
            "send",
            deliver_email,
            account=account,
            to=pending["to"],
            subject=pending["subject"],
            body_text=pending["body"],
            cc=pending["cc"],
            # Bcc stays in the SMTP envelope: deliver_email is the one that
            # knows which of the two serializations carries the header.
            bcc=pending["bcc"],
        )
        if error:
            return error

        return json.dumps(
            {
                "status": "sent",
                "from": account.email,
                "to": pending["to"],
                "subject": pending["subject"] or "(no subject)",
                **(
                    {}
                    if delivery.archived
                    else {
                        "warning": (
                            "The email was sent but could not be copied to "
                            "the Sent folder."
                        )
                    }
                ),
            },
            ensure_ascii=False,
        )

    # -- triage -------------------------------------------------------------

    @tool(
        badge_icon="📁",
        badge_label="Listed folders",
        badge_running_label="Listing folders",
        params=ListMailAccountScopedParams,
        concurrent=True,
    )
    def list_folders(self, args, user, bot, conversation_id, context):
        """List the folders of a mail account, with their unread counts. \
Call this before move_email: folders belong to one account and their names vary \
between providers, so a folder name you guessed is a call that fails."""
        from workspace.mail.models import MailFolder

        account, error = _resolve_account(user, args.account)
        if error:
            return error

        folders = MailFolder.objects.filter(account=account).order_by("display_name")
        if not folders:
            return f"{account.email} has no folders synced yet."
        return json.dumps(
            [
                {
                    "name": folder.display_name,
                    "type": folder.folder_type,
                    "messages": folder.message_count,
                    "unread": folder.unread_count,
                }
                for folder in folders
            ],
            ensure_ascii=False,
        )

    @tool(
        badge_icon="🏷️",
        badge_label="Listed labels",
        badge_running_label="Listing labels",
        params=ListMailAccountScopedParams,
        concurrent=True,
    )
    def list_labels(self, args, user, bot, conversation_id, context):
        """List the labels defined on a mail account. \
Call this before label_email: labels are per-account and user-defined, so only a name \
returned here can be applied."""
        from workspace.mail.models import MailLabel

        account, error = _resolve_account(user, args.account)
        if error:
            return error

        labels = MailLabel.objects.filter(account=account).order_by("position", "name")
        if not labels:
            return f"{account.email} has no labels defined."
        return json.dumps(
            [{"name": label.name, "unread": label.unread_count} for label in labels],
            ensure_ascii=False,
        )

    @tool(
        badge_icon="📌",
        badge_label="Flagged an email",
        badge_running_label="Flagging an email",
        detail_key="action",
        params=MarkEmailParams,
    )
    def mark_email(self, args, user, bot, conversation_id, context):
        """Mark an email as read, unread, starred or unstarred. \
Use it to help the user work through a backlog: star what needs an answer, \
mark read what they have decided to ignore."""
        from workspace.mail.services.triage import set_flag

        message = _resolve_message(user, args.uuid)
        if not message:
            return "Email not found or access denied."

        synced = set_flag(message, args.action)
        subject = message.subject or "(no subject)"
        if not synced:
            return (
                f'"{subject}" is now {args.action} here, but the mail server '
                "did not take the change — it may be unreachable. The next "
                "sync will settle it."
            )
        return f'"{subject}" is now {args.action}.'

    @tool(
        badge_icon="📂",
        badge_label="Moved an email",
        badge_running_label="Moving an email",
        detail_key="folder",
        params=MoveEmailParams,
    )
    def move_email(self, args, user, bot, conversation_id, context):
        """File an email into another folder of the same account. \
Call list_folders first to learn which folders exist and how they are named. \
To throw a message away, use delete_email instead."""
        message = _resolve_message(user, args.uuid)
        if not message:
            return "Email not found or access denied."

        target, error = _resolve_folder(message.account, args.folder)
        if error:
            return error
        return self._move(message, target)

    @tool(
        badge_icon="🗑️",
        badge_label="Trashed an email",
        badge_running_label="Trashing an email",
        params=DeleteEmailParams,
    )
    def delete_email(self, args, user, bot, conversation_id, context):
        """Move an email to the trash. It leaves the inbox but stays recoverable, \
so the user can undo it from their mail app. Use this rather than move_email when \
the user wants a message gone."""
        from workspace.mail.models import MailFolder
        from workspace.mail.queries import special_folder

        message = _resolve_message(user, args.uuid)
        if not message:
            return "Email not found or access denied."

        trash = special_folder(message.account, MailFolder.FolderType.TRASH)
        if not trash:
            # Never fall back to an IMAP delete: that expunges the message for
            # good, and nothing in this loop is worth an irreversible one.
            return (
                f"{message.account.email} has no trash folder, so there is "
                "nowhere to put this message. Nothing was deleted."
            )
        return self._move(message, trash)

    def _move(self, message, target):
        """Move `message` to `target` and describe the outcome."""
        from workspace.mail.services.triage import move_to_folder

        subject = message.subject or "(no subject)"
        if message.folder_id == target.pk:
            return f'"{subject}" is already in {target.display_name}.'

        _, error = _run_remote("move", move_to_folder, message, target)
        if error:
            return error
        return f'"{subject}" moved to {target.display_name}.'

    @tool(
        badge_icon="🏷️",
        badge_label="Labelled an email",
        badge_running_label="Labelling an email",
        detail_key="label",
        params=LabelEmailParams,
    )
    def label_email(self, args, user, bot, conversation_id, context):
        """Attach a label to an email, or take one off. Labels live in this workspace only, \
so this touches nothing on the mail server. Call list_labels first — only an existing \
label can be applied."""
        from workspace.mail.models import MailLabel
        from workspace.mail.services.triage import set_label

        message = _resolve_message(user, args.uuid)
        if not message:
            return "Email not found or access denied."

        name = args.label.strip()
        label = MailLabel.objects.filter(
            account=message.account, name__iexact=name
        ).first()
        if not label:
            return (
                f'No label named "{name}" on {message.account.email}. '
                "Call list_labels to see what exists."
            )

        subject = message.subject or "(no subject)"
        attaching = args.action == "add"
        changed = set_label(message, label, attaching)
        if attaching:
            return (
                f'Labelled "{subject}" with {label.name}.'
                if changed
                else f'"{subject}" already carried the {label.name} label.'
            )
        return (
            f'Removed the {label.name} label from "{subject}".'
            if changed
            else f'"{subject}" did not carry the {label.name} label.'
        )
