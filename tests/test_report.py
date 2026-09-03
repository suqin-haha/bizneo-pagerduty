"""Tests for before/after report building."""

from __future__ import annotations

from datetime import date, time

from bizneo_oncall.models import PlannedEntry, TimeEntry
from bizneo_oncall.report import build_day_reports


def test_build_day_reports_merges_days() -> None:
    current = [
        TimeEntry(
            id="1",
            day=date(2025, 8, 1),
            start=time(9, 0),
            end=time(17, 0),
            project_id="p1",
            project_name="Regular",
            description="work",
        )
    ]
    planned = [
        PlannedEntry(
            day=date(2025, 8, 1),
            start=time(17, 0),
            end=time(23, 59),
            project_id="p2",
            project_name="Oncall",
            description="L1",
        ),
        PlannedEntry(
            day=date(2025, 8, 2),
            start=time(0, 0),
            end=time(23, 59),
            project_id="p2",
            project_name="Oncall",
            description="L1",
        ),
    ]
    reports = build_day_reports(current=current, planned=planned)
    assert [r.day for r in reports] == [date(2025, 8, 1), date(2025, 8, 2)]
    assert len(reports[0].current) == 1
    assert len(reports[0].planned) == 1
    assert len(reports[1].current) == 0
    assert len(reports[1].planned) == 1
