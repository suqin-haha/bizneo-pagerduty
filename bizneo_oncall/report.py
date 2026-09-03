"""Colored before/after report for planned Bizneo changes."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from rich.console import Console
from rich.table import Table
from rich.text import Text

from bizneo_oncall.models import DayReport, PlannedEntry, TimeEntry


def build_day_reports(
    *,
    current: list[TimeEntry],
    planned: list[PlannedEntry],
) -> list[DayReport]:
    current_by_day: dict[date, list[TimeEntry]] = defaultdict(list)
    planned_by_day: dict[date, list[PlannedEntry]] = defaultdict(list)

    for entry in current:
        current_by_day[entry.day].append(entry)
    for entry in planned:
        planned_by_day[entry.day].append(entry)

    days = sorted(set(current_by_day) | set(planned_by_day))
    reports: list[DayReport] = []
    for day in days:
        cur = tuple(sorted(current_by_day[day], key=lambda e: e.start))
        plan = tuple(sorted(planned_by_day[day], key=lambda e: e.start))
        reports.append(DayReport(day=day, current=cur, planned=plan))
    return reports


def render_report(
    reports: list[DayReport],
    console: Console | None = None,
    *,
    employee_id: str | None = None,
) -> None:
    """Print a colorized before/after table."""
    out = console or Console()
    title = "Bizneo on-call time report"
    if employee_id:
        title += f"  (employee {employee_id})"
    table = Table(title=title, show_lines=True)
    table.add_column("Date", style="bold")
    table.add_column("Current (now)", overflow="fold")
    table.add_column("After (planned)", overflow="fold")
    table.add_column("Hours +", justify="right")

    total_new = 0.0
    for report in reports:
        current_text = _format_current(report.current)
        planned_text = _format_planned(report.planned)
        hours = sum(item.duration_hours for item in report.planned)
        total_new += hours
        hours_text = Text(f"{hours:.2f}" if hours else "—")
        if hours:
            hours_text.stylize("bold green")
        table.add_row(
            report.day.isoformat() + f" ({report.day.strftime('%a')})",
            current_text,
            planned_text,
            hours_text,
        )

    out.print(table)
    out.print(Text(f"Total hours to submit: {total_new:.2f}", style="bold green"))


def _format_current(entries: tuple[TimeEntry, ...]) -> Text:
    if not entries:
        return Text("(none)", style="dim")
    text = Text()
    for index, entry in enumerate(entries):
        if index:
            text.append("\n")
        text.append(entry.label(), style="cyan")
        detail = f"  {entry.project_name}"
        if entry.description:
            detail += f" — {entry.description}"
        text.append(detail, style="dim cyan")
    return text


def _format_planned(entries: tuple[PlannedEntry, ...]) -> Text:
    if not entries:
        return Text("(no change)", style="dim")
    text = Text()
    for index, entry in enumerate(entries):
        if index:
            text.append("\n")
        text.append(entry.label(), style="bold green")
        detail = f"  {entry.project_name}"
        if entry.description:
            detail += f" — {entry.description}"
        text.append(detail, style="green")
    return text
