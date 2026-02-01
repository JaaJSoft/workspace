"""Centralized MIME type lookup service backed by MimeTypeRule and Django cache."""

from django.core.cache import cache

CACHE_KEY = "mime_type_rules"

_DEFAULT = {
    "icon": "file",
    "color": "text-base-content/60",
    "category": "unknown",
    "viewer_type": None,
}


def _build_cache():
    from workspace.files.models import MimeTypeRule

    rules = MimeTypeRule.objects.order_by("priority", "pattern")
    exact = {}
    wildcards = []
    for r in rules:
        entry = {
            "icon": r.icon,
            "color": r.color,
            "category": r.category,
            "viewer_type": r.viewer_type or None,
        }
        if r.is_wildcard:
            # "image/*" -> prefix "image/"
            wildcards.append({"prefix": r.pattern[:-1], **entry})
        else:
            exact[r.pattern] = entry

    data = {"exact": exact, "wildcards": wildcards}
    cache.set(CACHE_KEY, data, timeout=None)
    return data


def _get_data():
    data = cache.get(CACHE_KEY)
    if data is None:
        data = _build_cache()
    return data


def get_rule(mime_type):
    """Return the full rule dict for a MIME type (exact > wildcard > default)."""
    if not mime_type:
        return _DEFAULT

    mime_type = mime_type.lower()
    data = _get_data()

    # 1. Exact match
    rule = data["exact"].get(mime_type)
    if rule is not None:
        return rule

    # 2. Wildcard match (list already sorted by priority)
    for wc in data["wildcards"]:
        if mime_type.startswith(wc["prefix"]):
            return wc

    return _DEFAULT


def get_icon(mime_type):
    return get_rule(mime_type)["icon"]


def get_color(mime_type):
    return get_rule(mime_type)["color"]


def get_category(mime_type):
    return get_rule(mime_type)["category"]


def get_viewer_type(mime_type):
    return get_rule(mime_type)["viewer_type"]


def is_viewable(mime_type):
    return get_viewer_type(mime_type) is not None


def invalidate_cache():
    cache.delete(CACHE_KEY)
