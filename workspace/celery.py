"""Celery application for the Workspace project."""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace.settings')

app = Celery('workspace')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
