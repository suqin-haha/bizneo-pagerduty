"""Read on-call shifts from a PagerDuty WebCal / ICS feed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bizneo_oncall.compat import sanitize_python_sysconfig

sanitize_python_sysconfig()

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from bizneo_oncall.models import MonthShift, OncallShift, Project, TimeRange

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_ICS_DT_FORMATS = (
    "%Y%m%dT%H%M%S",
    "%Y%m%dT%H%M%SZ",
    "%Y%m%d",
)


def normalize_calendar_url(url: str) -> str:
    """Turn ``webcal://`` into ``https://`` so httpx can fetch the feed."""
    cleaned = url.strip()
    if cleaned.lower().startswith("webcal://"):
        return "https://" + cleaned[len("webcal://") :]
    return cleaned


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Return the zone used to convert ICS times to naive local datetimes."""
    if name and name.strip():
        try:
            return ZoneInfo(name.strip())
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone {name!r}.") from exc
    local = datetime.now().astimezone().tzinfo
    if isinstance(local, ZoneInfo):
        return local
    key = getattr(local, "key", None)
    if isinstance(key, str):
        return ZoneInfo(key)
    return ZoneInfo("UTC")


def fetch_calendar(url: str, *, client: httpx.Client | None = None) -> str:
    """Download the ICS text for a PagerDuty calendar URL."""
    target = normalize_calendar_url(url)
    headers = {"User-Agent": _BROWSER_UA, "Accept": "text/calendar, text/plain, */*"}
    if client is not None:
        response = client.get(target, headers=headers)
        response.raise_for_status()
        return response.text

    with httpx.Client(follow_redirects=True, timeout=30.0) as owned:
        response = owned.get(target, headers=headers)
        response.raise_for_status()
        return response.text


def parse_ics_events(text: str, *, tz: ZoneInfo) -> list[OncallShift]:
    """Parse ``VEVENT`` blocks into local-time on-call shifts."""
    shifts: list[OncallShift] = []
    current: dict[str, tuple[str, dict[str, str]]] = {}
    in_event = False
    calendar_name = ""

    for raw_line in _unfold_ics(text):
        line = raw_line.strip()
        if not line:
            continue
        if line == "BEGIN:VEVENT":
            in_event = True
            current = {}
            continue
        if line == "END:VEVENT":
            if in_event:
                shift = _event_to_shift(current, tz=tz, calendar_name=calendar_name)
                if shift is not None:
                    shifts.append(shift)
            in_event = False
            current = {}
            continue
        name, _params, value = _parse_ics_property(line)
        if not in_event:
            if name == "X-WR-CALNAME" and value:
                calendar_name = schedule_title(value)
            continue
        current[name] = (value, _params)

    shifts.sort(key=lambda item: item.start)
    return shifts


def filter_shifts(shifts: list[OncallShift], contains: str | None) -> list[OncallShift]:
    """Keep events whose summary or attendee contains ``contains``."""
    if not contains or not contains.strip():
        return list(shifts)
    needle = contains.strip().lower()
    return [
        item
        for item in shifts
        if needle in item.summary.lower() or needle in item.attendee.lower()
    ]


def available_months(shifts: list[OncallShift]) -> list[tuple[int, int]]:
    """Sorted unique ``(year, month)`` values that overlap any shift."""
    months: set[tuple[int, int]] = set()
    for shift in shifts:
        months.update(_months_overlapping(shift.start, shift.end))
    return sorted(months)


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Naive local ``[start, end)`` for a calendar month."""
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def month_progress(year: int, month: int, now: datetime) -> str:
    """``completed``, ``in_progress``, or ``not_started`` relative to ``now``."""
    start, end = month_bounds(year, month)
    if now < start:
        return "not_started"
    if now >= end:
        return "completed"
    return "in_progress"


def parse_month(value: str) -> tuple[int, int]:
    """Parse ``YYYY-MM`` into ``(year, month)``."""
    cleaned = value.strip()
    try:
        parsed = datetime.strptime(cleaned, "%Y-%m")
    except ValueError as exc:
        raise ValueError(f"Month must be YYYY-MM, got {value!r}.") from exc
    return parsed.year, parsed.month


def previous_month(now: datetime) -> tuple[int, int]:
    """Calendar month before ``now``."""
    if now.month == 1:
        return now.year - 1, 12
    return now.year, now.month - 1


def shifts_for_month(
    shifts: list[OncallShift],
    year: int,
    month: int,
) -> list[MonthShift]:
    """Clip shifts to the given month and keep the original calendar event."""
    month_start, month_end = month_bounds(year, month)
    items: list[MonthShift] = []
    for shift in shifts:
        start = max(shift.start, month_start)
        end = min(shift.end, month_end)
        if end <= start:
            continue
        items.append(MonthShift(source=shift, window=TimeRange(start=start, end=end)))
    return items


def clip_completed(ranges: list[TimeRange], now: datetime) -> list[TimeRange]:
    """Keep only time that has already ended; clip in-progress windows to ``now``."""
    completed: list[TimeRange] = []
    for item in ranges:
        if item.start >= now:
            continue
        end = item.end if item.end <= now else now
        if end <= item.start:
            continue
        completed.append(TimeRange(start=item.start, end=end))
    return completed


def clip_completed_shifts(items: list[MonthShift], now: datetime) -> list[MonthShift]:
    """Same as :func:`clip_completed`, keeping the original calendar shift."""
    completed: list[MonthShift] = []
    for item in items:
        windows = clip_completed([item.window], now)
        if not windows:
            continue
        completed.append(MonthShift(source=item.source, window=windows[0]))
    return completed


def schedule_title(value: str) -> str:
    """Turn ``On Call Schedule for On-Call Team`` into ``On-Call Team``."""
    cleaned = value.strip()
    prefix = "on call schedule for "
    if cleaned.lower().startswith(prefix):
        return cleaned[len(prefix) :].strip() or cleaned
    return cleaned


def title_from_summary(summary: str) -> str:
    """Use the last `` - `` segment, e.g. ``On Call - Name - On-Call Team``."""
    parts = [part.strip() for part in summary.split(" - ") if part.strip()]
    if len(parts) >= 2:
        return parts[-1]
    return summary.strip() or "On-call"


def level_from_project(project: Project) -> str:
    """Map a Bizneo project name to ``Level 1`` / ``Level 2`` / ``Backup``."""
    name = project.name.lower()
    if "backup" in name:
        return "Backup"
    if "2nd" in name or "second" in name or "level 2" in name:
        return "Level 2"
    return "Level 1"


def format_shift_clock(value: datetime) -> str:
    """Format like ``Sep 11, 3:00pm``."""
    hour = value.strftime("%I").lstrip("0") or "0"
    return f"{value.strftime('%b')} {value.day}, {hour}:{value.strftime('%M')}{value.strftime('%p').lower()}"


def format_shift_duration(start: datetime, end: datetime) -> str:
    """Human duration, preferring weeks when the span is about N weeks."""
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return "0 days"
    weeks = seconds / (7 * 86400)
    if abs(weeks - round(weeks)) < 0.02:
        count = max(1, round(weeks))
        return "1 week" if count == 1 else f"{count} weeks"
    days = max(1, round(seconds / 86400))
    return "1 day" if days == 1 else f"{days} days"


def format_shift_description(
    *,
    title: str,
    start: datetime,
    end: datetime,
    level: str | None = "Level 1",
) -> str:
    """Default Bizneo comment from the calendar shift.

    Example::

        On-Call Team
        Level 1
        Sep 11, 3:00pm - Sep 18, 3:00pm(1 week)
    """
    heading = title.strip() or "On-call"
    span = f"{format_shift_clock(start)} - {format_shift_clock(end)}"
    when = f"{span}({format_shift_duration(start, end)})"
    if level and level.strip():
        return f"{heading}\n{level.strip()}\n{when}"
    return f"{heading}\n{when}"


def local_now(tz: ZoneInfo) -> datetime:
    """Naive local clock in ``tz``."""
    return datetime.now(tz).replace(tzinfo=None)


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_ics_property(line: str) -> tuple[str, dict[str, str], str]:
    if ":" not in line:
        return line.upper(), {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for item in parts[1:]:
        if "=" not in item:
            continue
        key, param_value = item.split("=", 1)
        params[key.upper()] = param_value
    return name, params, _unescape_ics(value)


def _unescape_ics(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _event_to_shift(
    event: dict[str, tuple[str, dict[str, str]]],
    *,
    tz: ZoneInfo,
    calendar_name: str = "",
) -> OncallShift | None:
    start_raw = event.get("DTSTART")
    end_raw = event.get("DTEND")
    if start_raw is None:
        return None
    start = _parse_ics_datetime(start_raw[0], start_raw[1], tz=tz)
    if end_raw is not None:
        end = _parse_ics_datetime(end_raw[0], end_raw[1], tz=tz)
    else:
        duration = event.get("DURATION")
        if duration is None:
            return None
        end = start + _parse_ics_duration(duration[0])
    if end <= start:
        return None
    summary = event.get("SUMMARY", ("On-call", {}))[0] or "On-call"
    attendee = event.get("ATTENDEE", ("", {}))[0]
    if attendee.lower().startswith("mailto:"):
        attendee = attendee[7:]
    title = calendar_name or title_from_summary(summary)
    return OncallShift(
        summary=summary,
        start=start,
        end=end,
        calendar_name=title,
        attendee=attendee,
    )


def _parse_ics_datetime(
    value: str,
    params: dict[str, str],
    *,
    tz: ZoneInfo,
) -> datetime:
    cleaned = value.strip()
    utc = cleaned.endswith("Z")
    parsed: datetime | None = None
    for fmt in _ICS_DT_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"Cannot parse ICS datetime {value!r}.")

    if utc:
        aware = parsed.replace(tzinfo=timezone.utc)
    elif "TZID" in params:
        aware = parsed.replace(tzinfo=ZoneInfo(_unquote_tzid(params["TZID"])))
    else:
        aware = parsed.replace(tzinfo=tz)
    return aware.astimezone(tz).replace(tzinfo=None)


def _unquote_tzid(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_ics_duration(value: str) -> timedelta:
    """Parse a subset of RFC 5545 durations used by calendar feeds."""
    text = value.strip()
    if not text.startswith("P"):
        raise ValueError(f"Cannot parse ICS duration {value!r}.")
    sign = 1
    rest = text[1:]
    days = hours = minutes = seconds = 0
    number = ""
    in_time = False
    for char in rest:
        if char.isdigit():
            number += char
            continue
        if char == "T":
            in_time = True
            continue
        if not number:
            raise ValueError(f"Cannot parse ICS duration {value!r}.")
        amount = int(number)
        number = ""
        if char == "D" and not in_time:
            days = amount
        elif char == "H" and in_time:
            hours = amount
        elif char == "M" and in_time:
            minutes = amount
        elif char == "S" and in_time:
            seconds = amount
        else:
            raise ValueError(f"Cannot parse ICS duration {value!r}.")
    return sign * timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _months_overlapping(start: datetime, end: datetime) -> list[tuple[int, int]]:
    if end <= start:
        return []
    last = end - timedelta(microseconds=1)
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return months


def month_label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"
