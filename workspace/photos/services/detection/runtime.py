"""One lazy onnxruntime session per model file, per process.

A session costs its weights in memory and a second or so to build, so it is
created on the first photo a worker analyzes and reused for every one after.
"""

from __future__ import annotations

import threading

from django.conf import settings

from . import weights

_sessions = {}
_lock = threading.Lock()


def session(model):
    """The onnxruntime InferenceSession of *model*, downloading it if needed."""
    path = weights.ensure(model)
    with _lock:
        cached = _sessions.get(path)
        if cached is None:
            # Imported here: the web process never runs a model, and the
            # runtime is heavy to import.
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = settings.PHOTOS_ONNX_THREADS
            options.inter_op_num_threads = 1
            # Warnings only: some exported graphs log a line per initializer.
            options.log_severity_level = 3
            cached = onnxruntime.InferenceSession(
                str(path), options, providers=["CPUExecutionProvider"]
            )
            _sessions[path] = cached
        return cached
