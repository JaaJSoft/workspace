"""Celery tasks of the users module."""

from datetime import datetime

from celery import shared_task

from workspace.common.task_priority import NORMAL_PRIORITY


@shared_task(name="users.sync_presence", priority=NORMAL_PRIORITY, ignore_result=True)
def sync_presence(user_id, seen_at, update_public=True):
    """Copy the presence ``touch`` recorded in the cache to the user's row."""
    from workspace.users.services.presence import sync_to_db

    sync_to_db(user_id, datetime.fromisoformat(seen_at), update_public=update_public)
