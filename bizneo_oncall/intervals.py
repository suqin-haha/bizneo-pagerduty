"""Compute Bizneo log intervals from an on-call window.

Default working hours are 08:00–17:00 on weekdays.
Only on-call time *outside* working hours is logged.

Example (work 08:00–17:00, on-call Jul 31 15:00 → Aug 7 15:00):
  Jul 31: 17:00–23:59
  weekday middle days: 00:00–08:00 and 17:00–23:59
  weekend middle days: full day
  Aug 7: 00:00–08:00
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from bizneo_oncall.models import DayInterval, PlannedEntry, Project, TimeRange, UpdateMode

MIDNIGHT = time(0, 0)
DEFAULT_WORK_START = time(8, 0)
DEFAULT_WORK_END = time(17, 0)


def _is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def _to_day_interval(day: date, start: datetime, end: datetime) -> DayInterval:
    """Map a same-day span to a DayInterval.

    Midnight next day is stored as 23:59 (Bizneo same-day end).
    """
    day_end = datetime.combine(day + timedelta(days=1), MIDNIGHT)
    end_time = time(23, 59) if end == day_end else end.time()
    return DayInterval(day=day, start=start.time(), end=end_time)


def _day_intersection(
    day: date,
    window_start: datetime,
    window_end: datetime,
) -> tuple[datetime, datetime] | None:
    day_start = datetime.combine(day, MIDNIGHT)
    day_end = datetime.combine(day + timedelta(days=1), MIDNIGHT)
    start = max(day_start, window_start)
    end = min(day_end, window_end)
    if end <= start:
        return None
    return start, end


def _weekday_blocks(
    day: date,
    window_start: datetime,
    window_end: datetime,
    work_start: time,
    work_end: time,
) -> list[DayInterval]:
    overlap = _day_intersection(day, window_start, window_end)
    if overlap is None:
        return []

    seg_start, seg_end = overlap
    work_start_dt = datetime.combine(day, work_start)
    work_end_dt = datetime.combine(day, work_end)
    blocks: list[DayInterval] = []

    if seg_start < work_start_dt:
        blocks.append(_to_day_interval(day, seg_start, min(seg_end, work_start_dt)))

    if seg_end > work_end_dt:
        blocks.append(_to_day_interval(day, max(seg_start, work_end_dt), seg_end))

    return blocks


def _weekend_block(
    day: date,
    window_start: datetime,
    window_end: datetime,
) -> list[DayInterval]:
    overlap = _day_intersection(day, window_start, window_end)
    if overlap is None:
        return []
    return [_to_day_interval(day, overlap[0], overlap[1])]


def compute_log_intervals(
    oncall: TimeRange,
    *,
    mode: UpdateMode,
    work_start: time = DEFAULT_WORK_START,
    work_end: time = DEFAULT_WORK_END,
) -> list[DayInterval]:
    """Return day intervals that should be logged in Bizneo."""
    if work_end <= work_start:
        raise ValueError("work_end must be after work_start")

    intervals: list[DayInterval] = []
    day = oncall.start.date()
    last_day = oncall.end.date()
    if oncall.end.time() == MIDNIGHT and oncall.end > oncall.start:
        last_day = (oncall.end - timedelta(seconds=1)).date()

    while day <= last_day:
        weekend = _is_weekend(day)
        if mode is UpdateMode.WEEKENDS and not weekend:
            day += timedelta(days=1)
            continue

        if weekend:
            intervals.extend(_weekend_block(day, oncall.start, oncall.end))
        else:
            intervals.extend(
                _weekday_blocks(day, oncall.start, oncall.end, work_start, work_end)
            )
        day += timedelta(days=1)

    return intervals


def to_planned_entries(
    intervals: list[DayInterval],
    *,
    project: Project,
    description: str,
) -> list[PlannedEntry]:
    return [
        PlannedEntry(
            day=item.day,
            start=item.start,
            end=item.end,
            project_id=project.id,
            project_name=project.name,
            description=description,
        )
        for item in intervals
    ]
