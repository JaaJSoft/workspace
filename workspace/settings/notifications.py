"""Notifications module: Web Push (VAPID)."""

import os

WEBPUSH_VAPID_PRIVATE_KEY = os.getenv("WEBPUSH_VAPID_PRIVATE_KEY", "")
WEBPUSH_VAPID_PUBLIC_KEY = os.getenv("WEBPUSH_VAPID_PUBLIC_KEY", "")
WEBPUSH_VAPID_CLAIMS = {"sub": os.getenv("WEBPUSH_VAPID_MAILTO", "")}
