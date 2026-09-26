"""What the admin dashboard says about a backend's weights.

Answered without building a session: the card renders in the web process,
which never runs a model and should not pay for loading one.
"""

from importlib.util import find_spec

from . import weights
from .base import BackendHealth


def weights_health(label, models):
    if find_spec("onnxruntime") is None:
        return BackendHealth(ok=False, detail="onnxruntime is not installed")
    missing = [model for model in models if not weights.is_present(model)]
    if missing:
        return BackendHealth(ok=True, detail=f"{label}, weights download on first use")
    try:
        weights.ensure_all(models)
    except weights.WeightsError as exc:
        return BackendHealth(ok=False, detail=str(exc))
    return BackendHealth(ok=True, detail=f"{label}, ready")
