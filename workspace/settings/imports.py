"""Imports module: job pacing and retention."""

import os

from .env import env_bool, env_list

# A running job yields after this many seconds and re-enqueues itself, so a
# multi-hour import never meets the Celery time limits.
IMPORTS_BATCH_SECONDS = int(os.getenv("IMPORTS_BATCH_SECONDS", "1200"))
# Consecutive remote failures after which a job gives up instead of burning
# through a dead connection one entry at a time.
IMPORTS_MAX_CONSECUTIVE_ERRORS = int(os.getenv("IMPORTS_MAX_CONSECUTIVE_ERRORS", "20"))
# Per-entry error reports of jobs finished longer ago than this are purged;
# the job rows and their DONE items stay (they keep later runs incremental).
IMPORTS_JOB_RETENTION_DAYS = int(os.getenv("IMPORTS_JOB_RETENTION_DAYS", "90"))
IMPORTS_HTTP_TIMEOUT = int(os.getenv("IMPORTS_HTTP_TIMEOUT", "60"))
# Remote URLs are vetted before the worker contacts them (SSRF guard): loopback
# and link-local addresses are always refused; private networks only when this
# is on; hosts listed in IMPORTS_ALLOWED_HOSTS skip the check entirely.
IMPORTS_ALLOW_PRIVATE_NETWORKS = env_bool("IMPORTS_ALLOW_PRIVATE_NETWORKS", False)
IMPORTS_ALLOWED_HOSTS = env_list("IMPORTS_ALLOWED_HOSTS")
