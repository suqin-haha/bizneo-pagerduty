"""Build and parse the employee logged-time request form."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, time

from bizneo_oncall.auth import extract_input_values, normalize_html_text
from bizneo_oncall.models import PlannedEntry, Project, TimeEntry

_OPTION_RE = re.compile(
    r"""<option\b(?P<attrs>[^>]*)>(?P<label>.*?)</option>""",
    re.IGNORECASE | re.DOTALL,
)
_VALUE_RE = re.compile(r"""value\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_PROJECT_SELECT_RE = re.compile(
    r"""<select[^>]*name=["']logged_time_request\[requested_ranges\]\[\d+\]\[project_ids\]\[\]["'][^>]*>(.*?)</select>""",
    re.IGNORECASE | re.DOTALL,
)
_JSON_PROJECT_RE = re.compile(
    r'''"id"\s*:\s*(\d+)\s*,\s*"(?:name|title)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'''
)


def parse_projects_env(value: str) -> list[tuple[str, str]]:
    """Parse ``id:Name,id:Name`` from ``BIZNEO_PROJECTS``."""
    projects: list[tuple[str, str]] = []
    for item in value.split(","):
        part = item.strip()
        if not part:
            continue
        if ":" not in part:
            projects.append((part, part))
            continue
        project_id, project_name = part.split(":", 1)
        project_id = project_id.strip()
        project_name = project_name.strip() or project_id
        if project_id:
            projects.append((project_id, project_name))
    return projects


def parse_project_options(html: str) -> list[Project]:
    """Read project id/name pairs from the request-form project select."""
    chunks = _PROJECT_SELECT_RE.findall(html) or [html]
    projects: list[Project] = []
    seen: set[str] = set()
    for chunk in chunks:
        for match in _OPTION_RE.finditer(chunk):
            value_match = _VALUE_RE.search(match.group("attrs"))
            if value_match is None:
                continue
            project_id = (value_match.group(1) or value_match.group(2) or "").strip()
            if not project_id.isdigit() or project_id in seen:
                continue
            name = normalize_html_text(match.group("label")) or project_id
            seen.add(project_id)
            projects.append(Project(id=project_id, name=name))
    if not projects:
        for match in _JSON_PROJECT_RE.finditer(html):
            project_id = match.group(1)
            if project_id in seen:
                continue
            name = match.group(2).encode("utf-8").decode("unicode_escape")
            seen.add(project_id)
            projects.append(Project(id=project_id, name=name or project_id))
    return projects


def merge_projects(*groups: list[Project]) -> list[Project]:
    """Merge project lists, first occurrence of each id wins."""
    merged: list[Project] = []
    seen: set[str] = set()
    for group in groups:
        for project in group:
            if project.id in seen:
                continue
            seen.add(project.id)
            merged.append(project)
    return merged


def group_entries_by_day(entries: list[PlannedEntry]) -> list[tuple[date, list[PlannedEntry]]]:
    """Group planned entries by calendar day."""
    grouped: dict[date, list[PlannedEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.day].append(entry)
    return [(day, grouped[day]) for day in sorted(grouped)]


_REQUEST_ID_RE = re.compile(r"/time-attendance/logged-time-requests/(\d+)")
_FORM_DAY_RE = re.compile(r"form-(\d{4}-\d{2}-\d{2})")
_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_NEW_REGISTRATIONS_RE = re.compile(
    r"New registrations(?P<body>.*?)(?:Previous|Expected schedule|Comment|modal-footer)",
    re.IGNORECASE | re.DOTALL,
)
_REQUEST_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+"
    r"(?P<year>\d{4})",
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_TIME_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2})\s*(?P<start_ampm>am|pm)?\s*(?:-|–|—|to)\s*"
    r"(?P<end>\d{1,2}:\d{2})\s*(?P<end_ampm>am|pm)?",
    re.IGNORECASE,
)


def period_label(start: time, end: time) -> str:
    """Format one requested range as ``HH:MM-HH:MM``."""
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def format_period_list(entries: list[PlannedEntry] | list[TimeEntry]) -> str:
    """Join period labels for error and verify messages."""
    if not entries:
        return "none"
    return ", ".join(period_label(entry.start, entry.end) for entry in entries)


def parse_existing_ranges(html: str, *, day: date) -> list[TimeEntry]:
    """Read current requested ranges from the Bizneo form page."""
    values = extract_input_values(html)
    indexes: set[int] = set()
    prefix = "logged_time_request[requested_ranges]["

    for name in values:
        if not name.startswith(prefix):
            continue
        raw_index = name[len(prefix) :].split("]", 1)[0]
        if raw_index.isdigit():
            indexes.add(int(raw_index))

    entries: list[TimeEntry] = []
    for index in sorted(indexes):
        start_raw = values.get(f"{prefix}{index}][start_at]", "")
        end_raw = values.get(f"{prefix}{index}][end_at]", "")
        if not start_raw or not end_raw:
            continue
        try:
            start = _parse_hhmm_flex(start_raw)
            end = _parse_hhmm_flex(end_raw)
        except ValueError:
            continue
        if start == end:
            continue
        entries.append(
            TimeEntry(
                id=str(index),
                day=day,
                start=start,
                end=end,
                project_id=values.get(f"{prefix}{index}][project_ids][]", ""),
                project_name="",
                description=values.get(f"{prefix}{index}][comment]", ""),
            )
        )
    return entries


def parse_request_ids(html: str) -> list[str]:
    """Read logged-time request ids from a my-logs page."""
    seen: set[str] = set()
    ids: list[str] = []
    for request_id in _REQUEST_ID_RE.findall(html):
        if request_id in seen:
            continue
        seen.add(request_id)
        ids.append(request_id)
    return ids


def parse_day_request_ids(html: str) -> dict[date, str]:
    """Map each timesheet day to its request id from the my-logs row."""
    mapping: dict[date, str] = {}
    for row in _ROW_RE.findall(html):
        days = _FORM_DAY_RE.findall(row)
        request_ids = parse_request_ids(row)
        if not days or not request_ids:
            continue
        mapping[date.fromisoformat(days[0])] = request_ids[0]
    return mapping


def parse_logged_hours(html: str, *, day: date) -> list[TimeEntry]:
    """Read current timesheet hours from ``/logged-time/new``."""
    values = extract_input_values(html)
    indexes: set[int] = set()
    prefix = "logged_time[logged_hours]["
    for name in values:
        if not name.startswith(prefix):
            continue
        raw_index = name[len(prefix) :].split("]", 1)[0]
        if raw_index.isdigit():
            indexes.add(int(raw_index))

    entries: list[TimeEntry] = []
    for index in sorted(indexes):
        start_raw = values.get(f"{prefix}{index}][start_at]", "")
        end_raw = values.get(f"{prefix}{index}][end_at]", "")
        if not start_raw or not end_raw:
            continue
        try:
            start = _parse_hhmm_flex(start_raw)
            end = _parse_hhmm_flex(end_raw)
        except ValueError:
            continue
        if start == end:
            continue
        entries.append(
            TimeEntry(
                id=values.get(f"{prefix}{index}][id]", str(index)),
                day=day,
                start=start,
                end=end,
                project_id=values.get(f"{prefix}{index}][projects][]", ""),
                project_name="",
                description=values.get(f"{prefix}{index}][comment]", ""),
            )
        )
    return entries


def parse_request_date(html: str) -> date | None:
    """Read the request day from a Bizneo request show page."""
    match = _REQUEST_DATE_RE.search(html)
    if match is None:
        return None
    return date(
        int(match.group("year")),
        _MONTH_NUMBERS[match.group("month").lower()[:3]],
        int(match.group("day")),
    )


def parse_request_show(html: str, *, request_id: str) -> tuple[date | None, list[TimeEntry]]:
    """Read the day and *New registrations* periods from a request show page."""
    day = parse_request_date(html)
    if day is None:
        return None, []
    section = _NEW_REGISTRATIONS_RE.search(html)
    chunk = section.group("body") if section is not None else html
    periods = parse_range_pairs(chunk, day=day)
    for index, entry in enumerate(periods):
        periods[index] = TimeEntry(
            id=f"{request_id}:{entry.id}",
            day=day,
            start=entry.start,
            end=entry.end,
            project_id=entry.project_id,
            project_name=entry.project_name,
            description=entry.description,
        )
    return day, periods


def parse_range_pairs(html: str, *, day: date) -> list[TimeEntry]:
    """Read ranges from form fields and visible ``HH:MM-HH:MM`` text."""
    entries = parse_existing_ranges(html, day=day)
    seen = {_period_key(entry.start, entry.end) for entry in entries}
    text = normalize_html_text(html)
    for index, match in enumerate(_TIME_RANGE_RE.finditer(text), start=len(entries)):
        start = _parse_hhmm_flex(match.group("start"), match.group("start_ampm"))
        end = _parse_hhmm_flex(match.group("end"), match.group("end_ampm"))
        if start == end:
            continue
        key = _period_key(start, end)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            TimeEntry(
                id=f"visible-{index}",
                day=day,
                start=start,
                end=end,
                project_id="",
                project_name="",
                description="",
            )
        )
    return entries


def verify_expected_ranges(
    expected: list[PlannedEntry],
    found: list[TimeEntry],
) -> tuple[bool, str]:
    """Compare posted periods against what Bizneo shows after submit."""
    expected_labels = [period_label(entry.start, entry.end) for entry in expected]
    found_keys = {_period_key(entry.start, entry.end) for entry in found}
    found_labels = sorted({period_label(entry.start, entry.end) for entry in found})
    missing = [
        label
        for entry, label in zip(expected, expected_labels)
        if _period_key(entry.start, entry.end) not in found_keys
    ]
    if missing:
        return False, (
            f"expected {len(expected)} period(s) [{', '.join(expected_labels) or 'none'}], "
            f"found {len(found_labels)} [{', '.join(found_labels) or 'none'}]; "
            f"missing: {', '.join(missing)}"
        )
    return True, (
        f"verified {len(expected)} period(s): {', '.join(expected_labels)}"
    )


def build_logged_time_request(
    *,
    csrf_token: str,
    employee_id: str,
    day: date,
    entries: list[PlannedEntry],
) -> dict[str, str]:
    """Build the POST form body used by Bizneo's employee request page."""
    day_text = day.isoformat()
    body: dict[str, str] = {
        "_csrf_token": csrf_token,
        "logged_time_request[date]": day_text,
        "logged_time_request[user_id]": employee_id,
        "logged_time_request[comment]": entries[0].description if entries else "",
    }
    for index, entry in enumerate(entries):
        prefix = f"logged_time_request[requested_ranges][{index}]"
        body[f"{prefix}[shift_id]"] = ""
        body[f"{prefix}[start_at]"] = entry.start.strftime("%H:%M")
        body[f"{prefix}[end_at]"] = entry.end.strftime("%H:%M")
        body[f"{prefix}[date]"] = day_text
        body[f"{prefix}[kind]"] = "working_time"
        body[f"{prefix}[project_ids][]"] = entry.project_id
        body[f"{prefix}[comment]"] = entry.description
    return body


def count_requested_ranges(body: dict[str, str]) -> int:
    """How many ``requested_ranges`` indexes were put in the POST body."""
    indexes: set[int] = set()
    prefix = "logged_time_request[requested_ranges]["
    for name in body:
        if not name.startswith(prefix):
            continue
        raw_index = name[len(prefix) :].split("]", 1)[0]
        if raw_index.isdigit():
            indexes.add(int(raw_index))
    return len(indexes)


def _period_key(start: time, end: time) -> tuple[time, time]:
    if end == time(0, 0):
        end = time(23, 59)
    return start, end


def _parse_hhmm_flex(value: str, ampm: str | None = None) -> time:
    cleaned = value.strip()
    if "T" in cleaned:
        cleaned = cleaned.split("T", 1)[1]
    elif " " in cleaned:
        cleaned = cleaned.rsplit(" ", 1)[-1]
    parts = cleaned.split(":")
    if len(parts) < 2:
        raise ValueError(f"not a time: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if ampm:
        suffix = ampm.lower()
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
    return time(hour, minute)
