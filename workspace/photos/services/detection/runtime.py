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

# Protobuf field numbers of the ONNX messages _with_outputs writes.
_MODEL_GRAPH = 7
_GRAPH_OUTPUT = 12
_VALUE_INFO_NAME = 1
_VALUE_INFO_TYPE = 2
_TYPE_TENSOR = 1
_TENSOR_ELEM_TYPE = 1
_FLOAT = 1


def session(model, expose=()):
    """The onnxruntime InferenceSession of *model*, downloading it if needed.

    *expose* names float values inside the graph to return too, after the
    model's own outputs: a published export sometimes ends by discarding
    what the pipeline needs, like the length of a vector it normalizes.
    """
    path = weights.ensure(model)
    expose = tuple(expose)
    with _lock:
        cached = _sessions.get((path, expose))
        if cached is None:
            # Imported here: the web process never runs a model, and the
            # runtime is heavy to import.
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = settings.PHOTOS_ONNX_THREADS
            options.inter_op_num_threads = 1
            # Warnings only: some exported graphs log a line per initializer.
            options.log_severity_level = 3
            source = _with_outputs(path.read_bytes(), expose) if expose else str(path)
            cached = onnxruntime.InferenceSession(
                source, options, providers=["CPUExecutionProvider"]
            )
            _sessions[(path, expose)] = cached
        return cached


def _with_outputs(model, names):
    """*model*, a serialized ONNX model, with the float values *names* as
    outputs of its graph too.

    A protobuf parser merges a message field that occurs twice, repeated
    fields appended: a second graph holding only these outputs, written after
    the model, adds them to its graph. No onnx package needed to edit it.
    """
    tensor = _varint(_TENSOR_ELEM_TYPE << 3) + _varint(_FLOAT)
    outputs = b"".join(
        _field(
            _GRAPH_OUTPUT,
            _field(_VALUE_INFO_NAME, name.encode())
            + _field(_VALUE_INFO_TYPE, _field(_TYPE_TENSOR, tensor)),
        )
        for name in names
    )
    return model + _field(_MODEL_GRAPH, outputs)


def _field(number, payload):
    """A length-delimited protobuf field."""
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _varint(value):
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)
