"""NINA Autorun log parsing primitives.

Regex patterns and helpers shared by focus-analyzer and session-quality.
"""

import re
from datetime import datetime
from typing import Optional

# ── Timestamp ────────────────────────────────────────────────
TIMESTAMP_RE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+(?P<msg>.+)$"
)


def parse_timestamp(raw_ts: str) -> Optional[datetime]:
    """Parse a ``YYYY/MM/DD HH:MM:SS`` timestamp string."""
    try:
        return datetime.strptime(raw_ts, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


# ── Autorun event boundaries ─────────────────────────────────
AUTORUN_BEGIN_RE = re.compile(
    r"\[Autorun\|Begin\]\s+(?P<target>.+)$", re.IGNORECASE
)
SHOOTING_RE = re.compile(
    r"Shooting .*? Bin\s*(?P<bin>\d+)", re.IGNORECASE
)

# ── AutoFocus events ─────────────────────────────────────────
AUTOFOCUS_BEGIN_RE = re.compile(
    r"\[AutoFocus\|Begin\].*?exposure\s+(?P<exp>[\d.]+)s"
    r".*?Bin\s*(?P<bin>\d+)"
    r".*?temperature\s+(?P<temp>[-\d.]+)",
    re.IGNORECASE,
)
MEASUREMENT_RE = re.compile(
    r"^(?P<stage>(?:Find Focus Star|Calculate V-Curve|Calculate Focus Point))"
    r":(?:.*?star size\s+(?P<starsize>[\d.]+))?\s*,\s*EAF position\s+(?P<eaf>\d+)",
    re.IGNORECASE,
)
AUTOFOCUS_SUCCESS_RE = re.compile(
    r"Auto focus succeeded, the focused position is\s+(?P<pos>\d+)",
    re.IGNORECASE,
)
AUTOFOCUS_FAIL_RE = re.compile(r"Auto focus failed", re.IGNORECASE)
