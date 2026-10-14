"""A deterministic detector for tests: CI never downloads weights.

A photo's score is the share of red in its mean colour: a red picture is
explicit, a blue one is not, a dark red one sits in between.
"""

import numpy as np

from workspace.files.sensitivity import BlurPreference

from ..detection.base import BackendHealth
from .base import SensitiveBackend


class FakeSensitiveBackend(SensitiveBackend):
    key = "fake"
    thresholds = {
        BlurPreference.STRICT: 0.2,
        BlurPreference.HIGH: 0.35,
        BlurPreference.STANDARD: 0.5,
        BlurPreference.LOW: 0.8,
    }

    def score(self, image):
        mean = np.asarray(image.convert("RGB"), dtype=np.float64).mean(axis=(0, 1))
        total = mean.sum()
        return float(mean[0] / total) if total else 0.0

    def health(self):
        return BackendHealth(ok=True, detail="fake backend (tests only)")
