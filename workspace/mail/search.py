import hashlib
from collections import Counter, defaultdict

from django.db.models import Q

from workspace.core.module_registry import SearchResult

from .models import MailAccount, MailMessage


def search_mail(query, user, limit):
    account_ids = MailAccount.objects.filter(owner=user).values_list('uuid', flat=True)

    messages = (
        MailMessage.objects
        .select_related('folder')
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
            tags=(m.folder.display_name,) if m.folder else (),
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


def search_contacts(query, user, limit):
    account_ids = MailAccount.objects.filter(owner=user).values_list('uuid', flat=True)

    messages = (
        MailMessage.objects
        .filter(account_id__in=account_ids, deleted_at__isnull=True)
        .filter(
            Q(from_address__icontains=query)
            | Q(to_addresses__icontains=query)
            | Q(cc_addresses__icontains=query)
        )
        .order_by('-date')
        .only('from_address', 'to_addresses', 'cc_addresses')[:500]
    )

    q_lower = query.lower()
    email_count = Counter()
    email_names = defaultdict(Counter)

    for msg in messages:
        addresses = []
        fa = msg.from_address
        if isinstance(fa, dict) and fa.get('email'):
            addresses.append(fa)
        for field in (msg.to_addresses, msg.cc_addresses):
            if isinstance(field, list):
                addresses.extend(
                    a for a in field if isinstance(a, dict) and a.get('email')
                )

        for addr in addresses:
            email = addr['email'].strip().lower()
            name = (addr.get('name') or '').strip()
            if q_lower not in email and q_lower not in name.lower():
                continue
            email_count[email] += 1
            if name:
                email_names[email][name] += 1

    results = []
    for email, _count in email_count.most_common(limit):
        name_counter = email_names.get(email)
        name = name_counter.most_common(1)[0][0] if name_counter else ''
        display = f'{name} <{email}>' if name else email
        uid = hashlib.md5(email.encode()).hexdigest()
        results.append(SearchResult(
            uuid=uid,
            name=name or email,
            url=f'/mail?compose={email}',
            matched_value=display,
            match_type='contact',
            type_icon='user',
            module_slug='mail',
            module_color='warning',
        ))

    return results
