"""Imports module: job pacing and retention."""

import os

# A running job yields after this many seconds and re-enqueues itself, so a
# multi-hour import never meets the Celery time limits.
IMPORTS_BATCH_SECONDS = int(os.getenv("IMPORTS_BATCH_SECONDS", "1200"))
# Consecutive remote failures after which a job gives up instead of burning
# through a dead connection one entry at a time.
IMPORTS_MAX_CONSECUTIVE_ERRORS = int(os.getenv("IMPORTS_MAX_CONSECUTIVE_ERRORS", "20"))
# Terminal jobs (and their per-entry items) older than this are purged.
IMPORTS_JOB_RETENTION_DAYS = int(os.getenv("IMPORTS_JOB_RETENTION_DAYS", "90"))
IMPORTS_HTTP_TIMEOUT = int(os.getenv("IMPORTS_HTTP_TIMEOUT", "60"))
