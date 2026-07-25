"""AI tools for the Mail module."""

import json
import uuid as uuid_mod

from pydantic import BaseModel, Field

from workspace.ai.tool_registry import ToolProvider, tool


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


class MailToolProvider(ToolProvider):
    @tool(
        badge_icon="📧",
        badge_label="Read email",
        detail_key="uuid",
        params=ReadEmailParams,
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
            parts.append(f"Date: {msg.date.strftime('%Y-%m-%d %H:%M')}")
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
        detail_key="query",
        params=SearchEmailsParams,
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

        results = []
        for msg in matches:
            results.append(
                {
                    "uuid": str(msg.uuid),
                    "subject": msg.subject or "(no subject)",
                    "from": msg.from_name or msg.from_email,
                    "date": msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "",
                    "folder": msg.folder.display_name if msg.folder else "",
                    "is_read": msg.is_read,
                    "has_attachments": msg.has_attachments,
                }
            )
        return json.dumps(results, ensure_ascii=False)
