"""AI tools for the Mail module."""
from workspace.ai.tool_registry import Param, ToolProvider, tool


class MailToolProvider(ToolProvider):

    @tool(badge_icon='📧', badge_label='Read email', detail_key='uuid', params={
        'uuid': Param('The UUID of the email message to read.'),
    })
    def read_email(self, args, user, bot, conversation_id, context):
        """Read the full content of an email by its UUID: subject, sender, recipients, date, and body text. \
Call this after finding an email via search_workspace to get its complete content, \
or when the user asks to read, open, or see the details of a specific email."""
        email_uuid = args.get('uuid', '').strip()
        if not email_uuid:
            return 'Error: uuid is required'
        from workspace.mail.models import MailMessage
        msg = (
            MailMessage.objects
            .filter(uuid=email_uuid, account__owner=user, deleted_at__isnull=True)
            .select_related('folder', 'account')
            .first()
        )
        if not msg:
            return 'Email not found or access denied.'

        def _fmt_addr(addr):
            if isinstance(addr, dict):
                name = addr.get('name', '')
                email = addr.get('email', addr.get('address', ''))
                return f'{name} <{email}>' if name else email
            return str(addr)

        parts = [
            f'Subject: {msg.subject or "(no subject)"}',
            f'From: {_fmt_addr(msg.from_address)}',
            f'To: {", ".join(_fmt_addr(a) for a in msg.to_addresses)}',
        ]
        if msg.cc_addresses:
            parts.append(f'Cc: {", ".join(_fmt_addr(a) for a in msg.cc_addresses)}')
        if msg.date:
            parts.append(f'Date: {msg.date.strftime("%Y-%m-%d %H:%M")}')
        parts.append(f'Folder: {msg.folder.display_name}')
        if msg.has_attachments:
            parts.append('Attachments: yes')
        parts.append('')
        body = msg.body_text or msg.snippet or '(no content)'
        parts.append(body[:3000])
        return '\n'.join(parts)
