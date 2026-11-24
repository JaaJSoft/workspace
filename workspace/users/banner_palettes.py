import re

from django.utils.translation import gettext_lazy as _

from workspace.users.services.settings import get_setting

BANNER_PALETTES = {
    "sunset": {
        "label": _("Sunset"),
        "from": "#f97316",
        "via": "#e11d48",
        "to": "#7c3aed",
    },
    "ocean": {
        "label": _("Ocean"),
        "from": "#0ea5e9",
        "via": "#2563eb",
        "to": "#4f46e5",
    },
    "forest": {
        "label": _("Forest"),
        "from": "#10b981",
        "via": "#059669",
        "to": "#047857",
    },
    "aurora": {
        "label": _("Aurora"),
        "from": "#06b6d4",
        "via": "#8b5cf6",
        "to": "#ec4899",
    },
    "ember": {
        "label": _("Ember"),
        "from": "#ef4444",
        "via": "#dc2626",
        "to": "#991b1b",
    },
    "midnight": {
        "label": _("Midnight"),
        "from": "#1e293b",
        "via": "#334155",
        "to": "#475569",
    },
    "golden": {
        "label": _("Golden"),
        "from": "#f59e0b",
        "via": "#d97706",
        "to": "#b45309",
    },
    "lavender": {
        "label": _("Lavender"),
        "from": "#a78bfa",
        "via": "#7c3aed",
        "to": "#6d28d9",
    },
}

HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def gradient_from_palette_value(value):
    """Return a CSS linear-gradient string for a raw ``banner_palette`` value, or None."""
    if value is None:
        return None
    if isinstance(value, str) and value in BANNER_PALETTES:
        p = BANNER_PALETTES[value]
        return f"linear-gradient(135deg, {p['from']}, {p['via']}, {p['to']})"
    if isinstance(value, dict):
        f, v, t = value.get("from"), value.get("via"), value.get("to")
        if all(HEX_COLOUR.match(c or "") for c in (f, v, t)):
            return f"linear-gradient(135deg, {f}, {v}, {t})"
    return None


def resolve_banner_gradient(user):
    """Return a CSS linear-gradient string for the user's banner, or None for default."""
    return gradient_from_palette_value(get_setting(user, "profile", "banner_palette"))
