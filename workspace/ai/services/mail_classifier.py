"""Bulk mail classification (body of the ``ai.classify_mail`` Celery task).

Assigns user-defined labels to mail messages by batching them through the
small LLM model. Messages are grouped by account so each batch only sees
labels relevant to that account's mailbox.
"""

import logging
from collections import defaultdict

import orjson
from django.conf import settings
from django.db import transaction

from workspace.ai.services.ai_task import ai_task_lifecycle
from workspace.ai.services.llm import call_llm

logger = logging.getLogger(__name__)

CLASSIFY_BATCH_SIZE = 10
MAX_LABELS_PER_MESSAGE = 3


def classify_mail(task_id: str) -> dict:
    """Classify a batch of mail messages by assigning labels.

    Reads ``message_uuids`` from ``AITask.input_data``, groups them by
    mail account, and submits each account's messages to the LLM in
    fixed-size batches with the account's label set as the candidate
    list. Each message can receive up to ``MAX_LABELS_PER_MESSAGE``.
    """
    from workspace.ai.models import AITask
    from workspace.ai.prompts.mail import build_classify_messages
    from workspace.mail.models import MailLabel, MailMessage, MailMessageLabel

    try:
        with ai_task_lifecycle(task_id, log_label='Classify') as ai_task:
            message_uuids = ai_task.input_data.get('message_uuids', [])
            msgs = list(
                MailMessage.objects.filter(
                    uuid__in=message_uuids,
                    account__owner=ai_task.owner,
                ).only('uuid', 'subject', 'from_address', 'snippet', 'account_id')
            )

            if not msgs:
                ai_task.result = 'No messages to classify'
                return {'status': 'ok', 'task_id': task_id}

            msgs_by_account = defaultdict(list)
            for m in msgs:
                msgs_by_account[m.account_id].append(m)

            total_prompt = 0
            total_completion = 0
            model_used = ''

            for account_id, account_msgs in msgs_by_account.items():
                account_labels = list(MailLabel.objects.filter(account_id=account_id))
                label_names = [lbl.name for lbl in account_labels]
                label_by_lower = {lbl.name.lower(): lbl for lbl in account_labels}

                for batch_start in range(0, len(account_msgs), CLASSIFY_BATCH_SIZE):
                    batch = account_msgs[batch_start:batch_start + CLASSIFY_BATCH_SIZE]
                    uuid_index = {i + 1: m for i, m in enumerate(batch)}

                    emails = []
                    for m in batch:
                        from_addr = m.from_address if isinstance(m.from_address, dict) else {}
                        emails.append({
                            'subject': m.subject or '',
                            'from_name': from_addr.get('name', ''),
                            'from_email': from_addr.get('email', ''),
                            'snippet': m.snippet or '',
                        })

                    messages = build_classify_messages(emails, label_names)
                    result = call_llm(messages, model=settings.AI_SMALL_MODEL)

                    model_used = result['model']
                    total_prompt += result['prompt_tokens'] or 0
                    total_completion += result['completion_tokens'] or 0

                    try:
                        items = orjson.loads(result['content'])
                    except (ValueError, TypeError):
                        logger.warning('Classify: malformed JSON response for task %s', task_id)
                        raise ValueError('Malformed JSON response from LLM')

                    if not isinstance(items, list):
                        raise ValueError('Expected JSON array from LLM')

                    links_to_create = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        idx = item.get('i')
                        raw_labels = item.get('labels', [])

                        msg = uuid_index.get(idx)
                        if not msg:
                            continue

                        if not isinstance(raw_labels, list):
                            continue

                        count = 0
                        for raw_name in raw_labels:
                            if count >= MAX_LABELS_PER_MESSAGE:
                                break
                            if not isinstance(raw_name, str):
                                continue
                            label = label_by_lower.get(raw_name.lower())
                            if label:
                                links_to_create.append(
                                    MailMessageLabel(message=msg, label=label)
                                )
                                count += 1

                    if links_to_create:
                        MailMessageLabel.objects.bulk_create(links_to_create, ignore_conflicts=True)

            with transaction.atomic():
                ai_task.result = f'Classified {len(msgs)} messages'
                ai_task.model_used = model_used
                ai_task.prompt_tokens = total_prompt
                ai_task.completion_tokens = total_completion

            logger.info('Classify complete: task=%s messages=%d tokens=%d+%d',
                        task_id, len(msgs), total_prompt, total_completion)
            return {'status': 'ok', 'task_id': task_id}
    except AITask.DoesNotExist:
        logger.error('Classify task not found: %s', task_id)
        return {'status': 'error', 'error': 'Task not found'}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}
