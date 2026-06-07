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
