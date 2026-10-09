"""Rebuild ``Person.search_text`` without accents.

The column was only lowercased, so a query typed without accents missed every
accented name. Searches now fold their query, and a row saved before that
keeps its accents until it is rebuilt here.
"""

import unicodedata
from itertools import batched

from django.db import migrations

# Frozen copies of common.text.fold_text and people.models.search_text_for as
# of this migration, and of the lowercasing they replaced: a later change to
# either must not rewrite what it did, nor what undoing it restores.
SEARCH_TEXT_MAX_LENGTH = 1024


def _fold(text):
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _search_text(person, normalize):
    parts = [
        person.display_name,
        person.given_name,
        person.family_name,
        person.organization,
        *(entry.get("value", "") for entry in person.emails or []),
        *(entry.get("value", "") for entry in person.phones or []),
    ]
    text = normalize(" ".join(part.strip() for part in parts if part and part.strip()))
    return text[:SEARCH_TEXT_MAX_LENGTH]


def _rebuild(apps, schema_editor, normalize):
    Person = apps.get_model("people", "Person")
    db = schema_editor.connection.alias
    persons = (
        Person.objects.using(db)
        .only(
            "display_name",
            "given_name",
            "family_name",
            "organization",
            "emails",
            "phones",
            "search_text",
        )
        .iterator(chunk_size=500)
    )
    for batch in batched(persons, 500, strict=False):
        changed = []
        for person in batch:
            text = _search_text(person, normalize)
            if text != person.search_text:
                person.search_text = text
                changed.append(person)
        Person.objects.using(db).bulk_update(changed, ["search_text"])


def fold_search_text(apps, schema_editor):
    _rebuild(apps, schema_editor, _fold)


def lowercase_search_text(apps, schema_editor):
    _rebuild(apps, schema_editor, str.lower)


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0002_person_source_import_uid"),
    ]

    operations = [
        migrations.RunPython(fold_search_text, lowercase_search_text),
    ]
