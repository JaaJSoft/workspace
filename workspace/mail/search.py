import hashlib
from urllib.parse import quote

from django.db.models import Q
from django.utils.translation import gettext as _

from workspace.common.search import apply_fulltext
from workspace.common.search.schema import Col, FulltextIndex
from workspace.core.module_registry import SearchResult, SearchTag

from .models import MailMessage
from .queries import canonical_folder, user_account_ids
from .services.recipients import history_suggestions

# Column order is frozen into the applied bm25 config
# bm25(10.0, 2.0, 4.0, 4.0, 1.0): subject A, snippet C, from_email B,
# from_name B, body_text D. Do not reorder without a migration.
MAIL_FTS = FulltextIndex(
    table="mail_mailmessage",
    columns=(
        Col("subject", weight="A"),
        Col("snippet", weight="C"),
        Col("from_email", weight="B"),
        Col("from_name", weight="B"),
        Col("body_text", weight="D", cap=100_000),
    ),
)


def fts_messages(qs, query):
    """Apply mail full-text search to a MailMessage queryset.

    Returns qs filtered to matches and annotated with `search_rank`.
    Caller applies order_by.
    """
    return apply_fulltext(qs, query, index=MAIL_FTS)


def search_mail(query, user, limit):
    from workspace.users.services.settings import get_user_timezone

    account_ids = user_account_ids(user)
    tz = get_user_timezone(user)

    base = (
        MailMessage.objects.select_related("folder__alias_of")
        .filter(account_id__in=account_ids, deleted_at__isnull=True)
        .exclude(folder__is_hidden=True)
    )
    messages = fts_messages(base, query).order_by("-search_rank", "-date")[:limit]

    return [
        SearchResult(
            uuid=str(m.uuid),
            name=m.subject or _("(no subject)"),
            url=f"/mail?message={m.uuid}",
            matched_value=m.subject or m.snippet,
            match_type="subject",
            type_icon="mail",
            module_slug="mail",
            date=_format_date(m.date, tz),
            tags=(
                (SearchTag(canonical_folder(m.folder).display_name, "warning"),)
                if m.folder
                else ()
            ),
        )
        for m in messages
    ]


def _format_date(dt, tz):
    """Short date of a hit in *tz*, the searcher's zone.

    Explicit rather than the active timezone: the AI's search_everything
    runs this from a worker, where the active zone is UTC.
    """
    if not dt:
        return None
    from django.utils import timezone

    local = dt.astimezone(tz)
    now = timezone.now().astimezone(tz)
    diff = now - local
    if diff.days == 0 and local.date() == now.date():
        return local.strftime("%H:%M")
    if diff.days < 7:
        return local.strftime("%a")
    if local.year == now.year:
        return local.strftime("%d %b")
    return local.strftime("%d %b %Y")


def search_contacts(query, user, limit):
    account_filter = Q(account_id__in=user_account_ids(user))
    results = []
    for contact in history_suggestions(account_filter, query)[:limit]:
        name, email = contact["name"], contact["email"]
        display = f"{name} <{email}>" if name else email
        uid = hashlib.md5(email.encode()).hexdigest()
        results.append(
            SearchResult(
                uuid=uid,
                name=name or email,
                url=f"/mail?compose={quote(email)}",
                matched_value=display,
                match_type="contact",
                type_icon="user",
                module_slug="mail",
            )
        )

    return results
