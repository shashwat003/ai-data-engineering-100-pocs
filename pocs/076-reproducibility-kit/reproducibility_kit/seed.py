"""Seed every source of randomness we can reach, and say what we cannot."""

from __future__ import annotations

import os
import random

import numpy as np

# PYTHONHASHSEED is read once at interpreter start: setting it later does nothing for this process.
HASHSEED_NOTE = "PYTHONHASHSEED must be set in the environment before Python starts"


def set_all_seeds(seed: int) -> dict:
    random.seed(seed)
    np.random.seed(seed)  # noqa: NPY002 (legacy global state, still used by many libraries)
    status = {
        "random": True,
        "numpy_global": True,
        "numpy_generator": "use np.random.default_rng(seed) explicitly",
        "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "unset (randomised per process)"),
    }
    try:  # optional frameworks
        import torch  # type: ignore[import-not-found]

        torch.manual_seed(seed)
        status["torch"] = True
    except ImportError:
        status["torch"] = "not installed"
    return status
