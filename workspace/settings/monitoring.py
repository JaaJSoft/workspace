"""Logging and the Prometheus metrics endpoint."""

import os
import sys

from .base import DEBUG

# HTTP Basic credentials for /metrics. Leaving either empty closes the
# endpoint rather than opening it. Whitespace is trimmed so a stray newline in
# a secret file doesn't turn into an unexplainable 401.
METRICS_USER = os.getenv("METRICS_USER", "").strip()
METRICS_PASSWORD = os.getenv("METRICS_PASSWORD", "").strip()

# Par défaut on loggue vers stdout, pour que l'orchestrateur (Docker/K8s)
# collecte les logs. Le niveau peut être contrôlé via l'env DJANGO_LOG_LEVEL.
DJANGO_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%SZ",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG" if DEBUG else DJANGO_LOG_LEVEL,
            "stream": sys.stdout,
            "formatter": "simple",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": DJANGO_LOG_LEVEL,
            "propagate": True,
        },
        # Log des erreurs de requêtes Django (500, etc.)
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # Log des requêtes SQL (DEBUG only)
        "django.db.backends": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        # REST framework
        "rest_framework": {
            "handlers": ["console"],
            "level": DJANGO_LOG_LEVEL,
            "propagate": False,
        },
        # Logger applicatif (utiliser logging.getLogger("workspace"))
        "workspace": {
            "handlers": ["console"],
            "level": DJANGO_LOG_LEVEL,
            "propagate": False,
        },
    },
}
