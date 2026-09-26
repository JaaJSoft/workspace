"""Download and verify the weights of a face backend ahead of its first use.

The pipeline downloads them on its own the first time it runs; this is for
an image that ships them (the Dockerfile's face-models stage) or a worker
without internet access at run time.
"""

from django.core.management.base import BaseCommand, CommandError

from workspace.photos.services.detection.registry import (
    MisconfiguredBackend,
    backend_keys,
    get_face_backend,
)
from workspace.photos.services.detection.weights import WeightsError, model_path


class Command(BaseCommand):
    help = "Download the weights of a face backend into PHOTOS_MODEL_DIR and check their hashes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backend",
            action="append",
            choices=backend_keys(),
            help="Backend to download (repeatable). Default: PHOTOS_FACE_BACKEND.",
        )

    def handle(self, *args, **options):
        for key in options["backend"] or [None]:
            backend = get_face_backend(key)
            if isinstance(backend, MisconfiguredBackend):
                raise CommandError(backend.health().detail)
            try:
                backend.prepare()
            except WeightsError as exc:
                raise CommandError(str(exc)) from exc
            except Exception as exc:
                raise CommandError(f"{backend.key}: {exc}") from exc
            for model in backend.models:
                self.stdout.write(f"{backend.key}: {model_path(model)}")
