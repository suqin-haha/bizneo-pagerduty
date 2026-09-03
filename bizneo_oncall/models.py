"""Domain models for on-call → Bizneo time logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum


class UpdateMode(str, Enum):
    """Which days to update in Bizneo."""

    ALL = "all"
    WEEKENDS = "weekends"


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Inclusive on-call window [start, end)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("end must be after start")


@dataclass(frozen=True, slots=True)
class OncallShift:
    """One PagerDuty calendar event, in naive local time."""

    summary: str
    start: datetime
    end: datetime
    calendar_name: str = ""
    attendee: str = ""

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("end must be after start")


@dataclass(frozen=True, slots=True)
class MonthShift:
    """A calendar shift clipped to the requested month / completed time."""

    source: OncallShift
    window: TimeRange


@dataclass(frozen=True, slots=True)
class DayInterval:
    """One continuous block to log on a single calendar day."""

    day: date
    start: time
    end: time

    @property
    def duration_hours(self) -> float:
        start_dt = datetime.combine(self.day, self.start)
        if self.end == time(23, 59):
            end_dt = datetime.combine(self.day, time(0, 0)) + timedelta(days=1)
        else:
            end_dt = datetime.combine(self.day, self.end)
        return (end_dt - start_dt).total_seconds() / 3600.0

    def label(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class TimeEntry:
    """Existing Bizneo time entry."""

    id: str
    day: date
    start: time
    end: time
    project_id: str
    project_name: str
    description: str

    def label(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


@dataclass(frozen=True, slots=True)
class PlannedEntry:
    """Proposed Bizneo time entry for on-call non-working hours."""

    day: date
    start: time
    end: time
    project_id: str
    project_name: str
    description: str

    @property
    def duration_hours(self) -> float:
        start_dt = datetime.combine(self.day, self.start)
        if self.end == time(23, 59):
            end_dt = datetime.combine(self.day, time(0, 0)) + timedelta(days=1)
        else:
            end_dt = datetime.combine(self.day, self.end)
        return (end_dt - start_dt).total_seconds() / 3600.0

    def label(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


@dataclass(frozen=True, slots=True)
class DayReport:
    """Before/after snapshot for one day."""

    day: date
    current: tuple[TimeEntry, ...]
    planned: tuple[PlannedEntry, ...]


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """Outcome of one Bizneo logged-time request submission."""

    day: date
    ok: bool
    message: str
    detail: str = ""
    skipped: bool = False
