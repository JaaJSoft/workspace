import re

from workspace.users.services.settings import get_setting

BANNER_PALETTES = {
    'sunset':   {'label': 'Sunset',   'from': '#f97316', 'via': '#e11d48', 'to': '#7c3aed'},
    'ocean':    {'label': 'Ocean',    'from': '#0ea5e9', 'via': '#2563eb', 'to': '#4f46e5'},
    'forest':   {'label': 'Forest',   'from': '#10b981', 'via': '#059669', 'to': '#047857'},
    'aurora':   {'label': 'Aurora',   'from': '#06b6d4', 'via': '#8b5cf6', 'to': '#ec4899'},
    'ember':    {'label': 'Ember',    'from': '#ef4444', 'via': '#dc2626', 'to': '#991b1b'},
    'midnight': {'label': 'Midnight', 'from': '#1e293b', 'via': '#334155', 'to': '#475569'},
    'golden':   {'label': 'Golden',   'from': '#f59e0b', 'via': '#d97706', 'to': '#b45309'},
    'lavender': {'label': 'Lavender', 'from': '#a78bfa', 'via': '#7c3aed', 'to': '#6d28d9'},
}

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def resolve_banner_gradient(user):
    """Return a CSS linear-gradient string for the user's banner, or None for default."""
    value = get_setting(user, 'profile', 'banner_palette')
    if value is None:
        return None
    if isinstance(value, str) and value in BANNER_PALETTES:
        p = BANNER_PALETTES[value]
        return f"linear-gradient(135deg, {p['from']}, {p['via']}, {p['to']})"
    if isinstance(value, dict):
        f, v, t = value.get('from'), value.get('via'), value.get('to')
        if all(_HEX_RE.match(c or '') for c in (f, v, t)):
            return f"linear-gradient(135deg, {f}, {v}, {t})"
    return None


def validate_profile_setting(key, value):
    """Validate a profile setting value. Returns (is_valid, error_message)."""
    if key == 'bio':
        if not isinstance(value, str):
            return False, 'Bio must be a string.'
        if len(value) > 200:
            return False, 'Bio must be at most 200 characters.'
        return True, None
    if key == 'role':
        if not isinstance(value, str):
            return False, 'Role must be a string.'
        if len(value) > 50:
            return False, 'Role must be at most 50 characters.'
        return True, None
    if key == 'banner_palette':
        if isinstance(value, str):
            if value not in BANNER_PALETTES:
                return False, f'Unknown palette: {value}'
            return True, None
        if isinstance(value, dict):
            if set(value.keys()) != {'from', 'via', 'to'}:
                return False, 'Custom palette must have exactly from, via, to keys.'
            if not all(_HEX_RE.match(value.get(k, '')) for k in ('from', 'via', 'to')):
                return False, 'Each color must be a valid #rrggbb hex string.'
            return True, None
        return False, 'Palette must be a preset ID or {from, via, to} object.'
    return True, None
