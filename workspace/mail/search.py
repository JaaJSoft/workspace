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
        )
        for m in messages
    ]
