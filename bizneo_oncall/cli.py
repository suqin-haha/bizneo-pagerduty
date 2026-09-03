"""CLI for previewing and submitting Bizneo employee time requests."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Confirm, Prompt

from bizneo_oncall.calendar import (
    available_months,
    clip_completed,
    clip_completed_shifts,
    fetch_calendar,
    filter_shifts,
    format_shift_description,
    level_from_project,
    local_now,
    month_label,
    month_progress,
    parse_ics_events,
    parse_month,
    previous_month,
    resolve_timezone,
    shifts_for_month,
    title_from_summary,
)
from bizneo_oncall.client import HttpxBizneoClient
from bizneo_oncall.intervals import (
    DEFAULT_WORK_END,
    DEFAULT_WORK_START,
    compute_log_intervals,
    to_planned_entries,
)
from bizneo_oncall.login import save_browser_session
from bizneo_oncall.models import MonthShift, OncallShift, PlannedEntry, Project, TimeRange, UpdateMode
from bizneo_oncall.parse import parse_time_range
from bizneo_oncall.report import build_day_reports, render_report


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bizneo on-call employee helper")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env)",
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    login_parser = subparsers.add_parser(
        "login",
        help="Open Bizneo in a browser and save your session cookies",
    )
    login_parser.add_argument(
        "--session-file",
        default=None,
        help="Path to save the Playwright session file",
    )

    submit_parser = subparsers.add_parser(
        "submit",
        help="Preview and submit logged-time requests for non-working hours",
    )
    submit_parser.add_argument(
        "--range",
        default=None,
        help='Manual on-call window, e.g. "Jul 31, 3:00pm - Aug 7, 3:00pm"',
    )
    submit_parser.add_argument(
        "--month",
        default=None,
        help="Month to request from the PagerDuty calendar (YYYY-MM)",
    )
    submit_parser.add_argument(
        "--last-month",
        action="store_true",
        help="Request the previous calendar month based on the current time",
    )
    submit_parser.add_argument(
        "--calendar-url",
        default=None,
        help="PagerDuty WebCal/ICS URL (overrides PAGERDUTY_CALENDAR_URL)",
    )
    submit_parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="Year used when --range has no year (default: current year)",
    )
    submit_parser.add_argument(
        "--mode",
        choices=[m.value for m in UpdateMode],
        default=None,
        help="all = every day; weekends = Saturday/Sunday only",
    )
    submit_parser.add_argument(
        "--project",
        default=None,
        help="Skip the picker and use this project name or id",
    )
    submit_parser.add_argument(
        "--description",
        default=None,
        help="Override the calendar-generated per-shift description",
    )
    submit_parser.add_argument(
        "--work-start",
        default="08:00",
        help="Weekday working-hours start (HH:MM)",
    )
    submit_parser.add_argument(
        "--work-end",
        default="17:00",
        help="Weekday working-hours end (HH:MM)",
    )
    submit_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation (not recommended)",
    )
    return parser


def _choose_mode(console: Console, value: str | None) -> UpdateMode:
    if value:
        return UpdateMode(value)
    console.print("Update mode:")
    console.print("  [1] all days (weekdays outside work + weekends)")
    console.print("  [2] weekends only")
    choice = Prompt.ask("Choose", choices=["1", "2"], default="1")
    return UpdateMode.ALL if choice == "1" else UpdateMode.WEEKENDS


def _choose_project(
    console: Console,
    projects: list[Project],
    selected: str | None,
) -> Project:
    if not projects:
        console.print(
            "[yellow]No projects were scraped from Bizneo.[/yellow] "
            "Enter one manually, or run login again on a logged-time page."
        )
        project_id = Prompt.ask("Project id")
        project_name = Prompt.ask("Project name", default=project_id)
        if not project_id.strip():
            raise RuntimeError("Project id is required.")
        return Project(id=project_id.strip(), name=project_name.strip() or project_id)

    default = "1"
    if selected:
        needle = selected.strip().lower()
        for index, project in enumerate(projects, start=1):
            if project.id.lower() == needle or project.name.lower() == needle:
                default = str(index)
                break

    console.print("\n[bold]Projects[/bold]")
    for index, project in enumerate(projects, start=1):
        console.print(f"  [cyan]{index}[/cyan]  {project.name}  [dim]({project.id})[/dim]")
    choices = [str(index) for index in range(1, len(projects) + 1)]
    raw = Prompt.ask("Choose project number", choices=choices, default=default)
    return projects[int(raw) - 1]


def _run_login(args: argparse.Namespace, console: Console) -> int:
    base_url = os.environ.get("BIZNEO_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError("Set BIZNEO_BASE_URL in .env")
    session_file = Path(
        args.session_file
        or os.environ.get("BIZNEO_SESSION_FILE", ".bizneo-session.json")
    )
    employee_id = save_browser_session(base_url, session_file)
    if employee_id:
        console.print(f"[green]Employee id:[/green] [bold]{employee_id}[/bold]")
    console.print(
        "[green]Saved Bizneo session.[/green] CSRF will be fetched automatically from the form page."
    )
    return 0


def _resolve_oncall_windows(
    args: argparse.Namespace,
    console: Console,
) -> list[MonthShift] | None:
    """Return completed on-call windows, or None when submit should stop."""
    if sum(bool(item) for item in (args.range, args.month, args.last_month)) > 1:
        raise RuntimeError("Use only one of --range, --month, or --last-month.")

    tz = resolve_timezone(os.environ.get("PAGERDUTY_TIMEZONE"))
    now = local_now(tz)

    if args.range:
        ranges = [parse_time_range(args.range, year=args.year)]
        completed = clip_completed(ranges, now)
        if not completed:
            console.print(
                "[yellow]On-call time has not ended yet. Nothing to request.[/yellow]"
            )
            return None
        return [_manual_month_shift(item) for item in completed]

    url = (args.calendar_url or os.environ.get("PAGERDUTY_CALENDAR_URL", "")).strip()
    if not url:
        raise RuntimeError(
            "Set PAGERDUTY_CALENDAR_URL in .env, or pass --calendar-url or --range."
        )

    console.print("[dim]Fetching PagerDuty calendar…[/dim]")
    ics = fetch_calendar(url)
    who = (
        os.environ.get("PAGERDUTY_SUMMARY_CONTAINS")
        or os.environ.get("PAGERDUTY_ATTENDEE")
        or ""
    ).strip()
    shifts = filter_shifts(parse_ics_events(ics, tz=tz), who or None)
    if not shifts:
        console.print("[yellow]No on-call shifts found in the PagerDuty calendar.[/yellow]")
        if not who:
            console.print(
                "[dim]This feed includes everyone. Set PAGERDUTY_SUMMARY_CONTAINS "
                "to your name (or PAGERDUTY_ATTENDEE to your email).[/dim]"
            )
        return None

    selected = _choose_month(
        console,
        args.month,
        shifts,
        now,
        last_month=args.last_month,
    )
    if selected is None:
        return None
    year, month = selected
    label = month_label(year, month)
    month_shifts = shifts_for_month(shifts, year, month)
    if not month_shifts:
        console.print(
            f"[yellow]No on-call shifts in the calendar for {label}.[/yellow] "
            "PagerDuty feeds only keep about one month of history."
        )
        return None

    completed = clip_completed_shifts(month_shifts, now)
    if not completed:
        console.print(
            f"[yellow]On-call for {label} has not ended yet. "
            "Nothing to request.[/yellow]"
        )
        return None
    return completed


def _manual_month_shift(window: TimeRange) -> MonthShift:
    return MonthShift(
        source=OncallShift(
            summary="On-call",
            start=window.start,
            end=window.end,
            calendar_name="On-call",
        ),
        window=window,
    )


def _window_description(
    item: MonthShift,
    *,
    project: Project,
    override: str | None,
) -> str:
    if override:
        return override
    title = item.source.calendar_name or title_from_summary(item.source.summary)
    return format_shift_description(
        title=title,
        start=item.source.start,
        end=item.source.end,
        level=level_from_project(project),
    )


def _choose_month(
    console: Console,
    value: str | None,
    shifts: list[OncallShift],
    now: datetime,
    *,
    last_month: bool = False,
) -> tuple[int, int] | None:
    if last_month:
        year, month = previous_month(now)
        console.print(
            f"[green]Last month:[/green] [bold]{month_label(year, month)}[/bold]"
        )
        return year, month
    if value:
        return parse_month(value)

    months = available_months(shifts)
    if not months:
        console.print("[yellow]No on-call months found in the PagerDuty calendar.[/yellow]")
        return None

    console.print("\n[bold]On-call months from PagerDuty[/bold]")
    for index, (year, month) in enumerate(months, start=1):
        progress = month_progress(year, month, now)
        if progress == "completed":
            note = "completed"
        elif progress == "in_progress":
            note = "in progress — only finished days"
        else:
            note = "not started — cannot request"
        console.print(
            f"  [cyan]{index}[/cyan]  {month_label(year, month)}  [dim]({note})[/dim]"
        )
    choices = [str(index) for index in range(1, len(months) + 1)]
    raw = Prompt.ask("Choose month", choices=choices, default="1")
    return months[int(raw) - 1]


def _run_submit(args: argparse.Namespace, console: Console) -> int:
    windows = _resolve_oncall_windows(args, console)
    if windows is None:
        return 0
    work_start = _parse_hhmm(args.work_start) if args.work_start else DEFAULT_WORK_START
    work_end = _parse_hhmm(args.work_end) if args.work_end else DEFAULT_WORK_END

    with HttpxBizneoClient.from_env() as client:
        employee_id = client.resolve_employee_id(around=windows[0].window.start.date())
        console.print(f"[green]Employee id:[/green] [bold]{employee_id}[/bold]")

        mode = _choose_mode(console, args.mode)
        projects = client.list_projects(around=windows[0].window.start.date())
        project = _choose_project(console, projects, args.project)

        planned: list[PlannedEntry] = []
        for item in windows:
            description = _window_description(
                item,
                project=project,
                override=args.description,
            )
            intervals = compute_log_intervals(
                item.window,
                mode=mode,
                work_start=work_start,
                work_end=work_end,
            )
            planned.extend(
                to_planned_entries(
                    intervals,
                    project=project,
                    description=description,
                )
            )
        planned.sort(key=lambda item: (item.day, item.start, item.end))

        if not planned:
            console.print("[yellow]Nothing to log for this range/mode.[/yellow]")
            return 0

        current = client.list_time_entries(
            start=planned[0].day,
            end=planned[-1].day,
        )
        reports = build_day_reports(current=current, planned=planned)

        console.print()
        for item in windows:
            description = _window_description(
                item,
                project=project,
                override=args.description,
            )
            console.print(
                f"On-call: [bold]{item.window.start}[/bold] -> "
                f"[bold]{item.window.end}[/bold]"
            )
            console.print(f"[green]{description}[/green]")
        console.print(
            f"Employee: [bold]{employee_id}[/bold] | "
            f"Mode: [bold]{mode.value}[/bold] | "
            f"Work hours: [bold]{work_start.strftime('%H:%M')}-"
            f"{work_end.strftime('%H:%M')}[/bold] (weekdays) | "
            f"Project: [bold]{project.name}[/bold]"
        )
        console.print()
        render_report(reports, console=console, employee_id=employee_id)
        console.print()

        if not args.yes:
            if not Confirm.ask("Submit these Bizneo time requests?", default=False):
                console.print("[yellow]Aborted. No changes made.[/yellow]")
                return 1

        results = client.submit_entries(planned)
        failed = 0
        skipped = 0
        submitted = 0
        for result in results:
            if result.skipped:
                style = "yellow"
                skipped += 1
            elif result.ok:
                style = "green"
                submitted += 1
            else:
                style = "red"
                failed += 1
            console.print(f"[{style}]{result.day}[/{style}] {result.message}")
            if not result.ok and result.detail:
                console.print(f"[red]{result.detail}[/red]")
        if failed:
            console.print(
                f"\n[bold red]{failed} day(s) failed.[/bold red] "
                "If every day is 404, run `python -m bizneo_oncall login` "
                "and wait until the Bizneo logged-time page is open."
            )
            return 1
        console.print(
            f"\n[bold green]Done. Submitted {submitted} day(s), "
            f"skipped {skipped} already-correct day(s).[/bold green]"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    env_path = Path(args.env_file)
    load_dotenv(env_path)
    command = args.command or "submit"
    if command == "login":
        return _run_login(args, console)
    return _run_submit(args, console)


if __name__ == "__main__":
    sys.exit(main())
