"""A web worker boots without the libraries only background tasks use.

gunicorn does not preload the app, so every worker imports it on its own and
pays for each of these once per worker. They come in through innocent-looking
paths (an app's ready(), an admin module, a service importing a task module to
call .delay()), so the only reliable check is to boot one and look.
"""

import json
import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

# Imported by the code that runs them, never by a web worker at boot.
TASK_ONLY = (
    "aiohttp",  # through pywebpush
    "magika",
    "onnxruntime",
    "openai",
    "pywebpush",
    "trafilatura",
)

BOOT_WEB_WORKER = """
import json, sys
import django
django.setup()
from django.urls import get_resolver
from workspace.wsgi import application
get_resolver().url_patterns
print(json.dumps(sorted(m for m in sys.argv[1:] if m in sys.modules)))
"""


class WebImportFootprintTests(SimpleTestCase):
    def test_a_web_worker_boots_without_task_only_libraries(self):
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "workspace.settings"}
        result = subprocess.run(
            [sys.executable, "-c", BOOT_WEB_WORKER, *TASK_ONLY],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        loaded = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(loaded, [], f"imported by a web worker at boot: {loaded}")
