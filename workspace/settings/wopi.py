"""WOPI office editing: discovery endpoint, host URL override, token lifetime.

The editor (Collabora, OnlyOffice, Office Online Server, ...) is chosen by the
deployer; workspace only speaks the WOPI protocol against whatever the
discovery XML advertises. An empty WOPI_DISCOVERY_URL disables the feature
entirely - office files stay download-only.
"""

import os

# Discovery XML of the WOPI client, e.g. https://collabora.example.com/hosting/discovery
WOPI_DISCOVERY_URL = os.getenv("WOPI_DISCOVERY_URL", "").strip()

# Base URL of this workspace *as reachable by the editor container*. When the
# editor runs on the same docker network the browser-facing hostname often
# doesn't resolve from inside it; this overrides the WOPISrc origin. Empty
# means "derive from the incoming request".
WOPI_HOST_URL = os.getenv("WOPI_HOST_URL", "").strip().rstrip("/")

# Lifetime of a WOPI access token in seconds. One token is minted per viewer
# render; the editor keeps using it for the whole session.
WOPI_TOKEN_TTL = int(os.getenv("WOPI_TOKEN_TTL", str(10 * 3600)))
