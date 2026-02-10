from django.db.models import Q

from workspace.core.module_registry import SearchResult

from .models import MailAccount, MailMessage


def search_mail(query, user, limit):
    account_ids = MailAccount.objects.filter(owner=user).values_list('uuid', flat=True)

    messages = (
        MailMessage.objects
        .filter(
            account_id__in=account_ids,
            deleted_at__isnull=True,
        )
        .filter(
            Q(subject__icontains=query)
            | Q(snippet__icontains=query)
            | Q(from_address__icontains=query)
        )
        .order_by('-date')[:limit]
    )

    return [
        SearchResult(
            uuid=str(m.uuid),
            name=m.subject or '(no subject)',
            url=f'/mail?message={m.uuid}',
            matched_value=m.subject or m.snippet,
            match_type='subject',
            type_icon='mail',
            module_slug='mail',
            module_color='warning',
            date=_format_date(m.date),
        )
        for m in messages
    ]


def _format_date(dt):
    if not dt:
        return None
    from django.utils import timezone
    now = timezone.now()
    diff = now - dt
    if diff.days == 0 and dt.date() == now.date():
        return dt.strftime('%H:%M')
    if diff.days < 7:
        return dt.strftime('%a')
    if dt.year == now.year:
        return dt.strftime('%d %b')
    return dt.strftime('%d %b %Y')
