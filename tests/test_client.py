"""Tests for submit + GET verify of weekday multi-period requests."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from bizneo_oncall.client import HttpxBizneoClient
from bizneo_oncall.models import PlannedEntry

_NEW_FORM = """
<html>
  <form action="/time-attendance/logged-time-requests?date=2026-08-03&amp;employee_id=1001">
    <input type="hidden" name="_csrf_token" value="csrf-1">
    <input type="hidden" name="logged_time_request[user_id]" value="1001">
    <button type="submit" name="add" value="working_time">add period</button>
  </form>
</html>
"""

_MY_LOGS = """
<tr>
  <td id="form-2026-08-03">3 Mon</td>
  <td><a href="/time-attendance/logged-time-requests/1544161">View request</a></td>
</tr>
"""

_REQUEST_SHOW = """
<div><b>Date</b><p>Monday 3 Aug 2026</p></div>
<p>New registrations</p>
<table>
  <tr><td>00:00 - 08:00</td></tr>
  <tr><td>17:00 - 23:59</td></tr>
</table>
<p>Previous</p>
<p>Expected schedule</p>
<p>08:00 - 17:00</p>
"""

_REQUEST_SHOW_ONE = """
<div><b>Date</b><p>Monday 3 Aug 2026</p></div>
<p>New registrations</p>
<table><tr><td>00:00 - 08:00</td></tr></table>
<p>Previous</p>
"""

_TWO_PERIODS = [
    PlannedEntry(
        day=date(2026, 8, 3),
        start=time(0, 0),
        end=time(8, 0),
        project_id="15624873",
        project_name="On-Call (1st level support)",
        description="On-Call Team Level 1",
    ),
    PlannedEntry(
        day=date(2026, 8, 3),
        start=time(17, 0),
        end=time(23, 59),
        project_id="15624873",
        project_name="On-Call (1st level support)",
        description="On-Call Team Level 1",
    ),
]


def _session_file(tmp_path: Path) -> Path:
    path = tmp_path / ".bizneo-session.json"
    path.write_text(
        '{"cookies":[{"name":"_hcmex_key","value":"test","domain":"example.bizneohr.com","path":"/"}]}',
        encoding="utf-8",
    )
    return path


def _client(tmp_path: Path, handler: httpx.MockTransport) -> HttpxBizneoClient:
    return HttpxBizneoClient(
        base_url="https://example.bizneohr.com",
        employee_id="1001",
        projects=[],
        session_file=_session_file(tmp_path),
        transport=handler,
    )


def _route(request: httpx.Request, *, show_html: str, post_status: int = 302) -> httpx.Response:
    path = urlparse(str(request.url)).path
    if request.method == "GET" and path.endswith("/new"):
        return httpx.Response(200, text=_NEW_FORM, request=request)
    if request.method == "POST":
        return httpx.Response(post_status, text="", request=request)
    if request.method == "GET" and "/my-logs/" in path:
        return httpx.Response(200, text=_MY_LOGS, request=request)
    if request.method == "GET" and path.endswith("/logged-time-requests/1544161"):
        return httpx.Response(200, text=show_html, request=request)
    return httpx.Response(404, text="missing", request=request)


def test_submit_posts_two_periods_and_get_verifies(tmp_path: Path) -> None:
    posted: list[dict[str, list[str]]] = []
    created = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method == "POST":
            posted.append(parse_qs(request.content.decode()))
            created["done"] = True
            return httpx.Response(302, text="", request=request)
        if request.method == "GET" and "/my-logs/" in path:
            html = _MY_LOGS if created["done"] else "<table></table>"
            return httpx.Response(200, text=html, request=request)
        return _route(request, show_html=_REQUEST_SHOW)

    with _client(tmp_path, httpx.MockTransport(handler)) as client:
        results = client.submit_entries(_TWO_PERIODS)

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].skipped is False
    assert "verified 2 period(s): 00:00-08:00, 17:00-23:59" in results[0].message
    assert "request 1544161" in results[0].message
    body = posted[0]
    assert body["logged_time_request[requested_ranges][0][start_at]"] == ["00:00"]
    assert body["logged_time_request[requested_ranges][0][end_at]"] == ["08:00"]
    assert body["logged_time_request[requested_ranges][1][start_at]"] == ["17:00"]
    assert body["logged_time_request[requested_ranges][1][end_at]"] == ["23:59"]
    assert "add" not in body


def test_submit_skips_when_request_already_has_periods(tmp_path: Path) -> None:
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(request)
        return _route(request, show_html=_REQUEST_SHOW)

    with _client(tmp_path, httpx.MockTransport(handler)) as client:
        results = client.submit_entries(_TWO_PERIODS)

    assert posted == []
    assert results[0].ok is True
    assert results[0].skipped is True
    assert "already correct" in results[0].message
    assert "request 1544161" in results[0].message


def test_submit_fails_when_get_verify_finds_only_one_period(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(request, show_html=_REQUEST_SHOW_ONE)

    with _client(tmp_path, httpx.MockTransport(handler)) as client:
        results = client.submit_entries(_TWO_PERIODS)

    assert results[0].ok is False
    assert "GET request 1544161 verify failed" in results[0].message
    assert "expected 2 period(s)" in results[0].message
    assert "missing: 17:00-23:59" in results[0].message
    assert "GET " in results[0].detail
    assert "POST " in results[0].detail
