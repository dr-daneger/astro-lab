"""FITS header reading helpers.

Thin wrappers around astropy.io.fits for common header fields
used across multiple astro-pipeline projects.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from astropy.io import fits


def read_fits_header(file_path: Path) -> Dict[str, Any]:
    """Read the primary FITS header and return it as a plain dict."""
    with fits.open(file_path) as hdul:
        return dict(hdul[0].header)


def get_header_value(header: fits.Header, keys: List[str]) -> Optional[Any]:
    """Return the value for the first key found in *header*, or ``None``."""
    for key in keys:
        value = header.get(key)
        if value is not None:
            return value
    return None


def read_focus_position(file_path: Path) -> Optional[int]:
    """Extract the integer FOCUSPOS from a FITS primary header."""
    header = fits.getheader(file_path, 0)
    raw = header.get("FOCUSPOS")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def read_exposure_time(header: fits.Header) -> Optional[float]:
    """Return exposure time in seconds, checking common header keys."""
    raw = get_header_value(header, ["EXPTIME", "EXPOSURE"])
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
