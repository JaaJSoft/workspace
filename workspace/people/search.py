from workspace.core.module_registry import SearchResult

from .queries import user_persons


def _matched_value(person, needle):
    for entry in person.emails or []:
        value = entry.get("value", "")
        if needle in value.lower():
            return value
    return person.display_name


def search_persons(query, user, limit):
    needle = query.strip().lower()
    if not needle:
        return []
    persons = user_persons(user).filter(search_text__contains=needle)[:limit]
    return [
        SearchResult(
            uuid=str(p.uuid),
            name=p.display_name,
            url=f"/people?person={p.uuid}",
            matched_value=_matched_value(p, needle),
            match_type="person",
            type_icon="contact",
            module_slug="people",
            module_color="secondary",
        )
        for p in persons
    ]
