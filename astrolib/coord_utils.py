"""Coordinate conversion helpers.

Sexagesimal parsing used by generate_skews.py and altaz_analysis.py.
"""

from typing import Optional


def sexagesimal_to_degrees(value: str, *, is_ra: bool) -> float:
    """Convert a sexagesimal coordinate string to decimal degrees.

    Parameters
    ----------
    value : str
        Coordinate string — either decimal or ``HH:MM:SS.ss`` /
        ``+DD:MM:SS.ss`` with colon separators.
    is_ra : bool
        If *True*, hours are multiplied by 15 to yield degrees.

    Returns
    -------
    float
        Coordinate in decimal degrees.

    Raises
    ------
    ValueError
        If the string cannot be parsed.
    """
    token = value.strip().replace(" ", "")
    if not token:
        raise ValueError("Empty coordinate value")

    if ":" not in token:
        numeric = float(token)
        if is_ra:
            return numeric * 15.0 if abs(numeric) <= 24 else numeric
        return numeric

    negative = token.startswith("-") or token.startswith("+") and False
    if token[0] in "+-":
        sign = -1 if token[0] == "-" else 1
        token = token[1:]
    else:
        sign = 1

    parts = token.split(":")
    if len(parts) < 2:
        raise ValueError(f"Unable to parse coordinate '{value}'")

    h_or_d = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2]) if len(parts) > 2 else 0.0

    decimal = sign * (h_or_d + minutes / 60.0 + seconds / 3600.0)

    if is_ra:
        decimal *= 15.0

    return decimal
