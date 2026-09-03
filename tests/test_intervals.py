"""Tests for non-working-hour interval calculation."""

from __future__ import annotations

from datetime import date, datetime, time

from bizneo_oncall.intervals import compute_log_intervals
from bizneo_oncall.models import DayInterval, TimeRange, UpdateMode


def _labels(intervals: list[DayInterval]) -> list[tuple[date, str]]:
    return [(item.day, item.label()) for item in intervals]


def test_oncall_week_all_mode() -> None:
    """Jul 31 15:00 – Aug 7 15:00 with work 08:00–17:00.

    2025-07-31 = Thursday, 2025-08-02/03 = weekend.
    """
    oncall = TimeRange(
        start=datetime(2025, 7, 31, 15, 0),
        end=datetime(2025, 8, 7, 15, 0),
    )
    intervals = compute_log_intervals(oncall, mode=UpdateMode.ALL)

    assert (date(2025, 7, 31), "17:00-23:59") in _labels(intervals)
    assert (date(2025, 8, 7), "00:00-08:00") in _labels(intervals)

    # Middle weekday (Aug 1 Friday): morning + evening
    assert (date(2025, 8, 1), "00:00-08:00") in _labels(intervals)
    assert (date(2025, 8, 1), "17:00-23:59") in _labels(intervals)

    # Weekend full days
    assert (date(2025, 8, 2), "00:00-23:59") in _labels(intervals)
    assert (date(2025, 8, 3), "00:00-23:59") in _labels(intervals)

    # First day must NOT include 15:00–17:00 (covered by working hours)
    first_day = [i for i in intervals if i.day == date(2025, 7, 31)]
    assert first_day == [
        DayInterval(day=date(2025, 7, 31), start=time(17, 0), end=time(23, 59))
    ]


def test_oncall_weekends_only() -> None:
    oncall = TimeRange(
        start=datetime(2025, 7, 31, 15, 0),
        end=datetime(2025, 8, 7, 15, 0),
    )
    intervals = compute_log_intervals(oncall, mode=UpdateMode.WEEKENDS)
    days = {item.day for item in intervals}
    assert days == {date(2025, 8, 2), date(2025, 8, 3)}
    assert _labels(intervals) == [
        (date(2025, 8, 2), "00:00-23:59"),
        (date(2025, 8, 3), "00:00-23:59"),
    ]


def test_same_day_partial_after_work() -> None:
    """Weekday 10:00–18:00 → only 17:00–18:00 is outside working hours.

    2025-07-18 is a Friday.
    """
    oncall = TimeRange(
        start=datetime(2025, 7, 18, 10, 0),
        end=datetime(2025, 7, 18, 18, 0),
    )
    intervals = compute_log_intervals(oncall, mode=UpdateMode.ALL)
    assert _labels(intervals) == [(date(2025, 7, 18), "17:00-18:00")]


def test_weekend_same_day_logs_full_oncall() -> None:
    """Saturday on-call is fully non-working → log entire window."""
    oncall = TimeRange(
        start=datetime(2025, 7, 19, 10, 0),
        end=datetime(2025, 7, 19, 18, 0),
    )
    intervals = compute_log_intervals(oncall, mode=UpdateMode.ALL)
    assert _labels(intervals) == [(date(2025, 7, 19), "10:00-18:00")]


def test_same_day_fully_inside_working_hours() -> None:
    oncall = TimeRange(
        start=datetime(2025, 7, 21, 10, 0),
        end=datetime(2025, 7, 21, 16, 0),
    )
    assert compute_log_intervals(oncall, mode=UpdateMode.ALL) == []


def test_oncall_before_work_only() -> None:
    oncall = TimeRange(
        start=datetime(2025, 7, 21, 5, 0),
        end=datetime(2025, 7, 21, 7, 30),
    )
    intervals = compute_log_intervals(oncall, mode=UpdateMode.ALL)
    assert _labels(intervals) == [(date(2025, 7, 21), "05:00-07:30")]


def test_weekend_mode_skips_weekday_only_shift() -> None:
    oncall = TimeRange(
        start=datetime(2025, 7, 21, 10, 0),  # Monday
        end=datetime(2025, 7, 21, 20, 0),
    )
    assert compute_log_intervals(oncall, mode=UpdateMode.WEEKENDS) == []
