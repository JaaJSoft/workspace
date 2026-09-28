"""The Photos preferences a user sets in the sidebar's Preferences panel.

Each is a ``photos`` user setting. The API refuses a malformed value (see
``_validate_setting_value`` in users/views.py), and every reader here falls
back to the default on one anyway: a value stored before the rule existed
must not break the page.
"""

from workspace.users.services.settings import get_module_settings

from ..models import MediaItem
from ..queries import ALL, MINE, SHARED

MODULE = "photos"

TILE_SHAPE = "tile_shape"
TILE_BADGES = "tile_badges"
VIDEO_HOVER_PREVIEW = "video_hover_preview"
DEFAULT_SCOPE = "default_scope"
DEFAULT_MEDIA_TYPE = "default_media_type"
IMPORT_BY_DATE = "import_by_date"

SQUARE = "square"
ORIGINAL = "original"
TILE_SHAPES = (SQUARE, ORIGINAL)

# The ``?type=`` token for photos and videos together: needed in a URL only
# when the user's default is one kind, since no ``?type=`` means the default.
ALL_TYPES = "all"
MEDIA_TYPE_CHOICES = (ALL_TYPES, *MediaItem.MediaType.values)

SCOPE_CHOICES = (MINE, ALL, SHARED)
GROUP_SCOPE_PREFIX = "group:"


def is_scope_token(value):
    """Whether *value* reads as a library: mine, all, shared or group:<id>."""
    if not isinstance(value, str):
        return False
    if value in SCOPE_CHOICES:
        return True
    prefix, _, group_id = value.partition(":")
    return f"{prefix}:" == GROUP_SCOPE_PREFIX and group_id.isdecimal()


def _prefs(user):
    return get_module_settings(user, MODULE)


def _flag(user, key, default):
    value = _prefs(user).get(key, default)
    return value if isinstance(value, bool) else default


def tile_shape(user):
    value = _prefs(user).get(TILE_SHAPE)
    return value if value in TILE_SHAPES else SQUARE


def tile_badges(user):
    """Whether a tile keeps its favorite star and video length on screen,
    or shows them on hover only."""
    return _flag(user, TILE_BADGES, True)


def video_hover_preview(user):
    return _flag(user, VIDEO_HOVER_PREVIEW, False)


def import_by_date(user):
    """Whether photos imported from the Photos page are filed into
    year and month folders of the import folder."""
    return _flag(user, IMPORT_BY_DATE, False)


def default_scope_token(user):
    """The library the timeline opens on when the URL names none."""
    value = _prefs(user).get(DEFAULT_SCOPE)
    return value if is_scope_token(value) else MINE


def default_media_type(user):
    """The media type the timeline shows when the URL names none; None is both."""
    value = _prefs(user).get(DEFAULT_MEDIA_TYPE)
    if value in MediaItem.MediaType.values:
        return MediaItem.MediaType(value)
    return None


def display_preferences(user):
    """What the page and its Preferences panel need, as one dict."""
    media_type = default_media_type(user)
    return {
        "tile_shape": tile_shape(user),
        "tile_badges": tile_badges(user),
        "video_hover_preview": video_hover_preview(user),
        "default_scope": default_scope_token(user),
        "default_media_type": media_type.value if media_type else ALL_TYPES,
        "import_by_date": import_by_date(user),
    }
