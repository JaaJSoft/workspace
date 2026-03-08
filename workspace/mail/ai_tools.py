"""AI tools for the Mail module."""
import json

from workspace.ai.tool_registry import Param, ToolProvider, tool


class MailToolProvider(ToolProvider):

    @tool(badge_icon='📧', badge_label='Read email', detail_key='uuid', params={
        'uuid': Param('The UUID of the email message to read.'),
    })
    def read_email(self, args, user, bot, conversation_id, context):
        """Read the full content of an email by its UUID: subject, sender, recipients, date, and body text. \
Call this after finding an email via search_emails to get its complete content, \
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

    @tool(badge_icon='🔍', badge_label='Searched emails', detail_key='query', params={
        'query': Param('The search term to look for in email subject, content, or sender.'),
        'unread_only': Param('If true, only return unread emails.', type='boolean', required=False),
        'starred_only': Param('If true, only return starred emails.', type='boolean', required=False),
        'has_attachments': Param('If true, only return emails with attachments.', type='boolean', required=False),
    })
    def search_emails(self, args, user, bot, conversation_id, context):
        """Search through your emails by subject, content, or sender. \
Returns up to 20 matches with subject, sender, date, and folder. \
Call this when the user asks to find, look up, or locate an email. \
Use read_email with the returned UUID to get the full content."""
        query = args.get('query', '').strip()
        if not query:
            return 'Error: query is required'

        from django.db.models import Q
        from workspace.mail.models import MailMessage
        from workspace.mail.queries import user_account_ids

        account_ids = user_account_ids(user)
        qs = (
            MailMessage.objects
            .filter(account_id__in=account_ids, deleted_at__isnull=True)
            .exclude(folder__is_hidden=True)
            .filter(
                Q(subject__icontains=query)
                | Q(snippet__icontains=query)
                | Q(from_address__icontains=query)
            )
            .select_related('folder')
        )

        if args.get('unread_only'):
            qs = qs.filter(is_read=False)
        if args.get('starred_only'):
            qs = qs.filter(is_starred=True)
        if args.get('has_attachments'):
            qs = qs.filter(has_attachments=True)

        matches = qs.order_by('-date')[:20]
        if not matches:
            return f'No emails found matching "{query}".'

        def _sender(addr):
            if isinstance(addr, dict):
                return addr.get('name') or addr.get('email', '')
            return str(addr)

        results = []
        for msg in matches:
            results.append({
                'uuid': str(msg.uuid),
                'subject': msg.subject or '(no subject)',
                'from': _sender(msg.from_address),
                'date': msg.date.strftime('%Y-%m-%d %H:%M') if msg.date else '',
                'folder': msg.folder.display_name if msg.folder else '',
                'is_read': msg.is_read,
                'has_attachments': msg.has_attachments,
            })
        return json.dumps(results, ensure_ascii=False)
