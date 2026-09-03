"""Parse on-call time frames like ``Jul 31, 3:00pm - Aug 7, 3:00pm``."""

from __future__ import annotations

import re
from datetime import datetime

from bizneo_oncall.models import TimeRange

_RANGE_RE = re.compile(
    r"""
    ^\s*
    (?P<start>.+?)
    \s*-\s*
    (?P<end>.+?)
    \s*$
    """,
    re.VERBOSE,
)

# "Jul 31, 3:00pm" or "July 31, 3:00 pm" or "Jul 31 15:00"
_FORMATS: tuple[str, ...] = (
    "%b %d, %I:%M%p",
    "%b %d, %I:%M %p",
    "%B %d, %I:%M%p",
    "%B %d, %I:%M %p",
    "%b %d %I:%M%p",
    "%b %d %H:%M",
    "%b %d, %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
)


def parse_datetime(value: str, *, year: int) -> datetime:
    """Parse a single datetime fragment and attach ``year`` when missing."""
    cleaned = " ".join(value.strip().split())
    # Normalize am/pm without space: 3:00pm → keep as-is for %I:%M%p
    cleaned = cleaned.replace("a.m.", "am").replace("p.m.", "pm")
    cleaned = cleaned.replace("A.M.", "AM").replace("P.M.", "PM")

    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=year)
        return parsed

    raise ValueError(
        f"Cannot parse datetime {value!r}. "
        "Expected examples: 'Jul 31, 3:00pm' or '2025-07-31 15:00'."
    )


def parse_time_range(value: str, *, year: int) -> TimeRange:
    """Parse ``START - END`` into a :class:`TimeRange`."""
    match = _RANGE_RE.match(value)
    if match is None:
        raise ValueError(
            f"Cannot parse time range {value!r}. "
            "Expected: 'Jul 31, 3:00pm - Aug 7, 3:00pm'."
        )

    start = parse_datetime(match.group("start"), year=year)
    end = parse_datetime(match.group("end"), year=year)

    # If end month/day is before start and year was implied, end may be next year.
    if end <= start and "%Y-" not in value and "T" not in value:
        # Cross-year on-call is rare; only bump when clearly wrapped.
        if (end.month, end.day) < (start.month, start.day):
            end = end.replace(year=year + 1)

    return TimeRange(start=start, end=end)
