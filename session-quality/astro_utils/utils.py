"""Utility functions for astro_utils package."""

from pathlib import Path
from typing import List, Optional, Dict, Any
import re
from datetime import datetime
import numpy as np
from astropy.io import fits

def find_files_with_prefix(directory: Path, prefix: str, extension: str) -> List[Path]:
    """Find all files in directory with given prefix and extension."""
    return list(directory.glob(f"{prefix}*{extension}"))

def parse_datetime(dt_str: str, formats: List[str]) -> Optional[datetime]:
    """Parse datetime string using multiple possible formats."""
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None

def compute_rms(values: np.ndarray) -> float:
    """Compute Root Mean Square of values."""
    return np.sqrt(np.mean(np.square(values)))

def read_fits_header(file_path: Path) -> Dict[str, Any]:
    """Read FITS header and return as dictionary."""
    with fits.open(file_path) as hdul:
        return dict(hdul[0].header)

def extract_number_from_string(text: str, pattern: str) -> Optional[float]:
    """Extract number from string using regex pattern."""
    match = re.search(pattern, text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def validate_directory(directory: Path) -> bool:
    """Validate directory exists and is accessible."""
    return directory.exists() and directory.is_dir()

def ensure_directory(directory: Path) -> None:
    """Create directory if it doesn't exist."""
    directory.mkdir(parents=True, exist_ok=True)

def format_time_delta(seconds: float) -> str:
    """Format time delta in seconds to human readable string."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    
    return " ".join(parts)

def moving_average(data: np.ndarray, window: int) -> np.ndarray:
    """Calculate moving average of data with given window size."""
    return np.convolve(data, np.ones(window)/window, mode='valid')

def find_peaks(data: np.ndarray, threshold: float) -> np.ndarray:
    """Find peaks in data above threshold."""
    peaks = []
    for i in range(1, len(data)-1):
        if data[i] > threshold and data[i] > data[i-1] and data[i] > data[i+1]:
            peaks.append(i)
    return np.array(peaks)

def gaussian(x: np.ndarray, amplitude: float, mean: float, std: float) -> np.ndarray:
    """Compute Gaussian function."""
    return amplitude * np.exp(-((x - mean) ** 2) / (2 * std ** 2)) 