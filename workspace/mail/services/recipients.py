"""Suggestions for the composer's recipient fields.

Three sources, in this order: the persons of the user's address book, the
workspace accounts, then the correspondents derived from message history.
An address shows up once, under the first source that has it.
"""

from collections import Counter, defaultdict

from django.db.models import Q

from workspace.common.text import fold_text
from workspace.people.queries import user_persons
from workspace.users.queries import search_people

from ..models import MailMessage

PERSON_LIMIT = 8
ACCOUNT_LIMIT = 5
HISTORY_LIMIT = 15
# Messages scanned for correspondents, most recent first.
HISTORY_SCAN = 500

KIND_PERSON = "person"
KIND_ACCOUNT = "account"
KIND_HISTORY = "history"


def _person_suggestion(person, needle):
    emails = [e for e in person.emails or [] if e.get("value")]
    # The address the query names comes first: it is the one being typed.
    emails.sort(key=lambda e: needle not in e["value"].lower())
    return {
        "kind": KIND_PERSON,
        "uuid": str(person.uuid),
        "name": person.display_name,
        "emails": [{"value": e["value"], "type": e.get("type", "")} for e in emails],
        "user_id": person.linked_user_id,
        "avatar_url": (
            f"/api/v1/people/{person.uuid}/avatar?v={int(person.updated_at.timestamp())}"
            if person.has_avatar
            else None
        ),
    }


def person_suggestions(user, needle):
    persons = user_persons(user).filter(search_text__contains=fold_text(needle))
    suggestions = []
    # A person without an email cannot be a recipient; scan a little past the
    # limit so a few of them do not empty the section.
    for person in persons[: PERSON_LIMIT * 3]:
        if any(e.get("value") for e in person.emails or []):
            suggestions.append(_person_suggestion(person, needle))
            if len(suggestions) == PERSON_LIMIT:
                break
    return suggestions


def account_suggestions(user, query):
    return [
        {
            "kind": KIND_ACCOUNT,
            "user_id": account.pk,
            "username": account.username,
            "name": account.get_full_name() or account.username,
            "email": account.email,
        }
        for account in search_people(
            query, requesting_user=user, limit=ACCOUNT_LIMIT, with_email=True
        )
    ]


def history_suggestions(account_filter, query):
    """Correspondents of the messages ``account_filter`` selects, most frequent first."""
    needle = query.lower()
    rows = (
        MailMessage.objects.filter(account_filter, deleted_at__isnull=True)
        .filter(
            Q(from_email__icontains=query)
            | Q(from_name__icontains=query)
            | Q(recipients_text__icontains=query)
        )
        .order_by("-date")
        .values("from_email", "from_name", "to_addresses", "cc_addresses")[
            :HISTORY_SCAN
        ]
    )

    email_count = Counter()
    email_names = defaultdict(Counter)
    for row in rows:
        addresses = []
        if row["from_email"]:
            addresses.append({"name": row["from_name"], "email": row["from_email"]})
        for field in (row["to_addresses"], row["cc_addresses"]):
            if isinstance(field, list):
                addresses.extend(
                    a for a in field if isinstance(a, dict) and a.get("email")
                )

        for addr in addresses:
            email = addr["email"].strip().lower()
            name = (addr.get("name") or "").strip()
            # The message matched, but each of its addresses must match too.
            if needle not in email and needle not in name.lower():
                continue
            email_count[email] += 1
            if name:
                email_names[email][name] += 1

    results = []
    for email, count in email_count.most_common():
        name_counter = email_names.get(email)
        name = name_counter.most_common(1)[0][0] if name_counter else ""
        results.append(
            {"kind": KIND_HISTORY, "name": name, "email": email, "count": count}
        )
    return results


def recipient_suggestions(user, query, account_filter):
    needle = query.lower()
    persons = person_suggestions(user, needle)
    taken = {e["value"].lower() for p in persons for e in p["emails"]}
    linked = {p["user_id"] for p in persons if p["user_id"] is not None}

    accounts = [
        a
        for a in account_suggestions(user, query)
        if a["user_id"] not in linked and a["email"].lower() not in taken
    ]
    taken.update(a["email"].lower() for a in accounts)

    history = [
        h for h in history_suggestions(account_filter, query) if h["email"] not in taken
    ]
    return [*persons, *accounts, *history[:HISTORY_LIMIT]]
