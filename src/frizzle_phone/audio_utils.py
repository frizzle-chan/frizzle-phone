"""Shared audio utilities."""

import numpy as np


def stereo_to_mono(data: bytes) -> np.ndarray:
    """Convert 48 kHz stereo s16le PCM to mono int16 array."""
    stereo = np.frombuffer(data, dtype=np.int16).reshape(-1, 2)
    mixed = stereo[:, 0].astype(np.int32)
    mixed += stereo[:, 1]
    mixed >>= 1
    return mixed.astype(np.int16)
