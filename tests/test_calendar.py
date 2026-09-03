"""Tests for PagerDuty ICS parsing and month clipping."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bizneo_oncall.calendar import (
    available_months,
    clip_completed,
    clip_completed_shifts,
    filter_shifts,
    format_shift_description,
    level_from_project,
    month_progress,
    normalize_calendar_url,
    parse_ics_events,
    parse_month,
    previous_month,
    resolve_timezone,
    shifts_for_month,
    title_from_summary,
)
from bizneo_oncall.models import MonthShift, OncallShift, Project, TimeRange

UTC = ZoneInfo("UTC")

# Folded SUMMARY plus a Jul 31 15:00 – Aug 7 15:00 UTC shift.
_SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PagerDuty//On-Call Schedule//EN
X-WR-CALNAME:On Call Schedule for On-Call Team
BEGIN:VEVENT
UID:week-1@pagerduty.com
DTSTAMP:20250701T000000Z
SUMMARY:On Call - Jane Doe - On-Call
  Team
ATTENDEE:jane@example.com
DTSTART:20250731T150000Z
DTEND:20250807T150000Z
END:VEVENT
BEGIN:VEVENT
UID:future@pagerduty.com
DTSTAMP:20250701T000000Z
SUMMARY:On Call - Next Week
DTSTART:20251001T150000Z
DTEND:20251008T150000Z
END:VEVENT
END:VCALENDAR
"""


def test_normalize_webcal_url() -> None:
    assert (
        normalize_calendar_url(
            "webcal://example.pagerduty.com/private/token/feed/SCHEDULE"
        )
        == "https://example.pagerduty.com/private/token/feed/SCHEDULE"
    )
    assert normalize_calendar_url("https://example.com/feed") == "https://example.com/feed"


def test_parse_ics_events_reads_folded_summary_and_utc_times() -> None:
    shifts = parse_ics_events(_SAMPLE_ICS, tz=UTC)
    assert len(shifts) == 2
    first = shifts[0]
    assert first.summary == "On Call - Jane Doe - On-Call Team"
    assert first.calendar_name == "On-Call Team"
    assert first.attendee == "jane@example.com"
    assert first.start == datetime(2025, 7, 31, 15, 0)
    assert first.end == datetime(2025, 8, 7, 15, 0)


def test_shifts_for_month_clips_cross_month_window() -> None:
    shifts = parse_ics_events(_SAMPLE_ICS, tz=UTC)
    august = shifts_for_month(shifts, 2025, 8)
    assert [item.window for item in august] == [
        TimeRange(start=datetime(2025, 8, 1, 0, 0), end=datetime(2025, 8, 7, 15, 0))
    ]
    assert august[0].source.start == datetime(2025, 7, 31, 15, 0)
    july = shifts_for_month(shifts, 2025, 7)
    assert [item.window for item in july] == [
        TimeRange(start=datetime(2025, 7, 31, 15, 0), end=datetime(2025, 8, 1, 0, 0))
    ]


def test_clip_completed_drops_future_and_shortens_in_progress() -> None:
    now = datetime(2025, 8, 3, 12, 0)
    ranges = [
        TimeRange(start=datetime(2025, 7, 31, 15, 0), end=datetime(2025, 8, 7, 15, 0)),
        TimeRange(start=datetime(2025, 10, 1, 15, 0), end=datetime(2025, 10, 8, 15, 0)),
    ]
    assert clip_completed(ranges, now) == [
        TimeRange(start=datetime(2025, 7, 31, 15, 0), end=now)
    ]


def test_clip_completed_keeps_fully_finished_window() -> None:
    now = datetime(2025, 8, 10, 9, 0)
    ranges = [
        TimeRange(start=datetime(2025, 7, 31, 15, 0), end=datetime(2025, 8, 7, 15, 0))
    ]
    assert clip_completed(ranges, now) == ranges


def test_available_months_includes_overlap_months() -> None:
    shifts = parse_ics_events(_SAMPLE_ICS, tz=UTC)
    assert available_months(shifts) == [(2025, 7), (2025, 8), (2025, 10)]


def test_filter_shifts_by_summary() -> None:
    shifts = parse_ics_events(_SAMPLE_ICS, tz=UTC)
    assert [item.summary for item in filter_shifts(shifts, "jane")] == [
        "On Call - Jane Doe - On-Call Team"
    ]
    assert [item.summary for item in filter_shifts(shifts, "jane@")] == [
        "On Call - Jane Doe - On-Call Team"
    ]


def test_parse_month_and_progress() -> None:
    assert parse_month("2026-09") == (2026, 9)
    now = datetime(2026, 9, 3, 20, 0)
    assert month_progress(2026, 8, now) == "completed"
    assert month_progress(2026, 9, now) == "in_progress"
    assert month_progress(2026, 10, now) == "not_started"


def test_previous_month_from_current_time() -> None:
    assert previous_month(datetime(2026, 9, 3, 20, 10)) == (2026, 8)
    assert previous_month(datetime(2026, 1, 1, 0, 0)) == (2025, 12)


def test_parse_month_rejects_bad_value() -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        parse_month("September")


def test_tzid_event_converts_to_target_zone() -> None:
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:On Call
DTSTART;TZID=America/Los_Angeles:20250731T080000
DTEND;TZID=America/Los_Angeles:20250807T080000
END:VEVENT
END:VCALENDAR
"""
    shifts = parse_ics_events(ics, tz=UTC)
    assert shifts == [
        OncallShift(
            summary="On Call",
            start=datetime(2025, 7, 31, 15, 0),
            end=datetime(2025, 8, 7, 15, 0),
            calendar_name="On Call",
        )
    ]


def test_resolve_timezone_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        resolve_timezone("Not/AZone")


def test_format_shift_description_matches_requested_layout() -> None:
    text = format_shift_description(
        title="On-Call Team",
        start=datetime(2026, 9, 11, 15, 0),
        end=datetime(2026, 9, 18, 15, 0),
        level="Level 1",
    )
    assert text == (
        "On-Call Team\n"
        "Level 1\n"
        "Sep 11, 3:00pm - Sep 18, 3:00pm(1 week)"
    )


def test_title_and_level_helpers() -> None:
    assert title_from_summary("On Call - Jane Doe - On-Call Team") == (
        "On-Call Team"
    )
    assert level_from_project(Project(id="1", name="On-Call (1st level support)")) == (
        "Level 1"
    )
    assert level_from_project(Project(id="2", name="On Call (backup levels of support)")) == (
        "Backup"
    )


def test_clip_completed_shifts_keeps_original_source() -> None:
    source = OncallShift(
        summary="On Call - Jane Doe - On-Call Team",
        start=datetime(2026, 9, 11, 15, 0),
        end=datetime(2026, 9, 18, 15, 0),
        calendar_name="On-Call Team",
    )
    items = [
        MonthShift(
            source=source,
            window=TimeRange(start=source.start, end=source.end),
        )
    ]
    clipped = clip_completed_shifts(items, datetime(2026, 9, 14, 12, 0))
    assert len(clipped) == 1
    assert clipped[0].source is source
    assert clipped[0].window.end == datetime(2026, 9, 14, 12, 0)
