"""Reusable math helpers.

Gaussian function, RMS, and moving-average — used by camera-noise,
session-quality, and focus-analyzer.
"""

import numpy as np


def gaussian(x: np.ndarray, amplitude: float, mean: float, std: float) -> np.ndarray:
    """Evaluate a 1-D Gaussian at each point in *x*."""
    return amplitude * np.exp(-((x - mean) ** 2) / (2 * std ** 2))


def compute_rms(values: np.ndarray) -> float:
    """Root Mean Square of an array of values."""
    return float(np.sqrt(np.mean(np.square(values))))


def moving_average(data: np.ndarray, window: int) -> np.ndarray:
    """Simple moving average with the given *window* size.

    Returns an array of length ``len(data) - window + 1``.
    """
    return np.convolve(data, np.ones(window) / window, mode="valid")
