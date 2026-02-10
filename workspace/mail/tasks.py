"""Celery tasks for mail synchronization."""

import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(name='mail.sync_all_accounts', bind=True, max_retries=0)
def sync_all_accounts(self):
    """Sync all active mail accounts. Scheduled via Celery Beat."""
    from workspace.mail.models import MailAccount
    from workspace.mail.services.imap import sync_account

    accounts = MailAccount.objects.filter(is_active=True).select_related('owner')
    total = {'synced': 0, 'errors': 0}

    for account in accounts:
        try:
            sync_account(account)
            total['synced'] += 1
        except Exception as e:
            logger.exception("Failed to sync account %s", account.email)
            account.last_sync_error = str(e)
            account.save(update_fields=['last_sync_error', 'updated_at'])
            total['errors'] += 1

    logger.info("Mail sync complete: %s", total)
    return total


@shared_task(name='mail.sync_account', bind=True, max_retries=0)
def sync_single_account(self, account_uuid):
    """Sync a single mail account on demand."""
    from workspace.mail.models import MailAccount
    from workspace.mail.services.imap import sync_account

    try:
        account = MailAccount.objects.get(uuid=account_uuid, is_active=True)
    except MailAccount.DoesNotExist:
        logger.warning("Account %s not found or inactive", account_uuid)
        return {'status': 'not_found'}

    try:
        sync_account(account)
        return {'status': 'ok', 'email': account.email}
    except Exception as e:
        logger.exception("Failed to sync account %s", account.email)
        account.last_sync_error = str(e)
        account.save(update_fields=['last_sync_error', 'updated_at'])
        return {'status': 'error', 'error': str(e)}
