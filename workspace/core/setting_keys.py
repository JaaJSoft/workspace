"""Identifiers for per-user settings owned by the core module.

Settings are persisted by ``workspace.users.services.settings`` and keyed
by ``(module, key)`` pairs. Every reader and writer in the core app
imports these names rather than re-declaring the strings inline, so
renames stay confined to a single file.
"""

MODULE = "core"

# Last release whose changelog entry has been auto-displayed to the user.
CHANGELOG_LAST_SEEN_VERSION = "changelog_last_seen_version"

# Flipped to True the first time the user closes the onboarding modal,
# whatever the exit path (X, Escape, backdrop click, "Get started").
ONBOARDING_COMPLETED = "onboarding_completed"

# The module sidebar reduced to its icon rail. Stored under each module's own
# namespace (``files``, ``chat``, ...) since the preference is per module; the
# shell reads it for whichever module the page belongs to and renders the
# sidebar at that width before any script runs.
SIDEBAR_COLLAPSED = "sidebar_collapsed"

# The global search's "Mine only" switch: a SearchScope value, ``all`` when
# unset. The palette sends it with every query rather than the API reading it,
# since the search response is cached per query string.
SEARCH_SCOPE = "search_scope"

# Whether sensitive previews (files/sensitivity.py) are blurred: a boolean,
# on when unset. A core setting rather than a photos one because the shell
# renders it on every page, Files' included, from the core settings it reads
# anyway.
BLUR_SENSITIVE = "blur_sensitive"

# Two softenings of the blur, booleans off when unset: a blurred preview shows
# in clear while the pointer rests on it, and the viewer opens a sensitive file
# without its "Sensitive content" cover. Neither applies once the blur is off.
REVEAL_SENSITIVE_ON_HOVER = "reveal_sensitive_on_hover"
REVEAL_SENSITIVE_IN_VIEWER = "reveal_sensitive_in_viewer"
