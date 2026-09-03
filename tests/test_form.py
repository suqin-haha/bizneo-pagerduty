"""Tests for Bizneo employee request form helpers."""

from __future__ import annotations

from datetime import date, time

from bizneo_oncall.form import (
    build_logged_time_request,
    count_requested_ranges,
    group_entries_by_day,
    merge_projects,
    parse_existing_ranges,
    parse_day_request_ids,
    parse_logged_hours,
    parse_project_options,
    parse_projects_env,
    parse_range_pairs,
    parse_request_ids,
    parse_request_show,
    verify_expected_ranges,
)
from bizneo_oncall.models import PlannedEntry, Project, TimeEntry


def test_parse_projects_env() -> None:
    assert parse_projects_env("15624873:On-Call Team,42:Other") == [
        ("15624873", "On-Call Team"),
        ("42", "Other"),
    ]


def test_parse_project_options() -> None:
    html = """
    <select name="logged_time_request[requested_ranges][0][date]">
      <option selected value="2026-08-02">Aug 2</option>
    </select>
    <select name="logged_time_request[requested_ranges][0][project_ids][]" multiple>
      <option value="15616932">On Call (backup levels of support)</option>
      <option value="15616986">Overtime (OT)</option>
      <option value="15624873">On-Call (1st level support)</option>
    </select>
    """
    projects = parse_project_options(html)
    assert projects == [
        Project(id="15616932", name="On Call (backup levels of support)"),
        Project(id="15616986", name="Overtime (OT)"),
        Project(id="15624873", name="On-Call (1st level support)"),
    ]


def test_parse_project_options_from_new_form_html() -> None:
    html = """
    <form action="/time-attendance/logged-time-requests?date=2026-08-02&amp;employee_id=1001" method="post">
      <select name="logged_time_request[requested_ranges][0][project_ids][]" multiple>
        <option value="15616932">On Call (backup levels of support)</option>
        <option value="15616986">Overtime (OT)</option>
        <option value="15624873">On-Call (1st level support)</option>
      </select>
    </form>
    """
    names = [project.name for project in parse_project_options(html)]
    assert names == [
        "On Call (backup levels of support)",
        "Overtime (OT)",
        "On-Call (1st level support)",
    ]


def test_parse_project_options_from_json() -> None:
    html = '{"id":15624873,"name":"On-Call Team"}'
    assert parse_project_options(html) == [
        Project(id="15624873", name="On-Call Team"),
    ]


def test_merge_projects() -> None:
    merged = merge_projects(
        [Project(id="1", name="From env")],
        [Project(id="1", name="From page"), Project(id="2", name="Extra")],
    )
    assert merged == [
        Project(id="1", name="From env"),
        Project(id="2", name="Extra"),
    ]


def test_group_entries_by_day() -> None:
    entries = [
        PlannedEntry(
            day=date(2026, 8, 1),
            start=time(0, 0),
            end=time(8, 0),
            project_id="p1",
            project_name="On-Call",
            description="oncall",
        ),
        PlannedEntry(
            day=date(2026, 8, 1),
            start=time(17, 0),
            end=time(23, 59),
            project_id="p1",
            project_name="On-Call",
            description="oncall",
        ),
    ]
    grouped = group_entries_by_day(entries)
    assert len(grouped) == 1
    assert grouped[0][0] == date(2026, 8, 1)
    assert len(grouped[0][1]) == 2


def test_build_logged_time_request_for_two_ranges() -> None:
    entries = [
        PlannedEntry(
            day=date(2026, 8, 1),
            start=time(0, 0),
            end=time(8, 0),
            project_id="15624873",
            project_name="On-Call Team",
            description="range one",
        ),
        PlannedEntry(
            day=date(2026, 8, 1),
            start=time(17, 0),
            end=time(23, 59),
            project_id="15624873",
            project_name="On-Call Team",
            description="range two",
        ),
    ]
    body = build_logged_time_request(
        csrf_token="csrf-token",
        employee_id="1001",
        day=date(2026, 8, 1),
        entries=entries,
    )
    assert body["_csrf_token"] == "csrf-token"
    assert body["logged_time_request[user_id]"] == "1001"
    assert body["logged_time_request[requested_ranges][0][start_at]"] == "00:00"
    assert body["logged_time_request[requested_ranges][1][end_at]"] == "23:59"
    assert body["logged_time_request[requested_ranges][1][comment]"] == "range two"
    assert body["logged_time_request[comment]"] == "range one"
    assert "add" not in body
    assert count_requested_ranges(body) == 2


def test_parse_existing_ranges() -> None:
    html = """
    <input type="text" name="logged_time_request[requested_ranges][0][start_at]" value="00:00">
    <input type="text" name="logged_time_request[requested_ranges][0][end_at]" value="23:59">
    <input type="text" name="logged_time_request[requested_ranges][0][project_ids][]" value="15624873">
    <input type="text" name="logged_time_request[requested_ranges][0][comment]" value="weekend oncall">
    """
    current = parse_existing_ranges(html, day=date(2026, 8, 1))
    assert len(current) == 1
    assert current[0].start == time(0, 0)
    assert current[0].end == time(23, 59)
    assert current[0].project_id == "15624873"


def test_parse_existing_ranges_with_seconds() -> None:
    html = """
    <input name="logged_time_request[requested_ranges][0][start_at]" value="00:00:00">
    <input name="logged_time_request[requested_ranges][0][end_at]" value="08:00:00">
    """
    current = parse_existing_ranges(html, day=date(2026, 8, 1))
    assert [(item.start, item.end) for item in current] == [(time(0, 0), time(8, 0))]


def test_parse_day_request_ids_from_my_logs_row() -> None:
    html = """
    <tr>
      <td id="form-2026-08-02">2 Sun</td>
      <td><a href="/time-attendance/logged-time-requests/1544160" hx-get="/time-attendance/logged-time-requests/1544160">View request</a></td>
    </tr>
    <tr>
      <td id="form-2026-08-03">3 Mon</td>
      <td><a href="/time-attendance/logged-time-requests/1544161">View request</a></td>
    </tr>
    """
    assert parse_day_request_ids(html) == {
        date(2026, 8, 2): "1544160",
        date(2026, 8, 3): "1544161",
    }


def test_parse_logged_hours_from_timesheet_form() -> None:
    html = """
    <tr id="form-2026-08-01">
      <input type="hidden" name="logged_time[date]" value="2026-08-01">
      <input type="hidden" name="logged_time[logged_hours][0][id]" value="189537896">
      <input type="text" name="logged_time[logged_hours][0][start_at]" value="00:00:00">
      <input type="text" name="logged_time[logged_hours][0][end_at]" value="23:59:00">
      <select name="logged_time[logged_hours][0][projects][]">
        <option selected value="15624873">On-Call (1st level support)</option>
      </select>
      <input name="logged_time[logged_hours][0][comment]" value="On-Call Team Level 1">
    </tr>
    """
    hours = parse_logged_hours(html, day=date(2026, 8, 1))
    assert len(hours) == 1
    assert hours[0].start == time(0, 0)
    assert hours[0].end == time(23, 59)
    assert hours[0].id == "189537896"


def test_parse_request_show_new_registrations_only() -> None:
    html = """
    <div class="modal-swap-content">
      <div><b>Date</b><p>Sunday 2 Aug 2026</p></div>
      <p class="h3">New registrations</p>
      <table>
        <tr><td>00:00 - 23:59</td><td>On-Call (1st level support)</td></tr>
      </table>
      <p>Previous</p>
      <p>Expected schedule</p>
      <p>08:00 - 17:00</p>
      <p>Comment</p>
      <p>On-Call Team Level 1</p>
      <a href="/time-attendance/logged-time-requests/1544160">Remove</a>
    </div>
    """
    day, periods = parse_request_show(html, request_id="1544160")
    assert day == date(2026, 8, 2)
    assert [(item.start, item.end) for item in periods] == [(time(0, 0), time(23, 59))]
    assert parse_request_ids(html) == ["1544160"]


def test_parse_request_show_weekday_two_periods() -> None:
    html = """
    <h1>Request for change in the time logs</h1>
    <div>Monday 3 Aug 2026</div>
    <div>00:00 - 08:00</div>
    <div>On-Call (1st level support)</div>
    <div>17:00 - 23:59</div>
    <div>On-Call (1st level support)</div>
    <a href="/time-attendance/logged-time-requests/1544161">view</a>
    """
    day, periods = parse_request_show(html, request_id="1544161")
    assert day == date(2026, 8, 3)
    assert [(item.start, item.end) for item in periods] == [
        (time(0, 0), time(8, 0)),
        (time(17, 0), time(23, 59)),
    ]
    assert parse_request_ids(html) == ["1544161"]


def test_parse_range_pairs_from_visible_text() -> None:
    html = "<table><tr><td>00:00-08:00</td></tr><tr><td>17:00-23:59</td></tr></table>"
    found = parse_range_pairs(html, day=date(2026, 8, 3))
    assert [(item.start, item.end) for item in found] == [
        (time(0, 0), time(8, 0)),
        (time(17, 0), time(23, 59)),
    ]


def test_verify_expected_ranges_requires_both_weekday_periods() -> None:
    expected = [
        PlannedEntry(
            day=date(2026, 8, 3),
            start=time(0, 0),
            end=time(8, 0),
            project_id="1",
            project_name="On-Call",
            description="oncall",
        ),
        PlannedEntry(
            day=date(2026, 8, 3),
            start=time(17, 0),
            end=time(23, 59),
            project_id="1",
            project_name="On-Call",
            description="oncall",
        ),
    ]
    found = [
        TimeEntry(
            id="0",
            day=date(2026, 8, 3),
            start=time(0, 0),
            end=time(8, 0),
            project_id="1",
            project_name="On-Call",
            description="oncall",
        )
    ]
    ok, message = verify_expected_ranges(expected, found)
    assert ok is False
    assert "expected 2 period(s)" in message
    assert "missing: 17:00-23:59" in message

    ok, message = verify_expected_ranges(
        expected,
        found
        + [
            TimeEntry(
                id="1",
                day=date(2026, 8, 3),
                start=time(17, 0),
                end=time(23, 59),
                project_id="1",
                project_name="On-Call",
                description="oncall",
            )
        ],
    )
    assert ok is True
    assert message == "verified 2 period(s): 00:00-08:00, 17:00-23:59"
