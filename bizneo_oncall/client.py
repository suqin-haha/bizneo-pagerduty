"""Bizneo employee logged-time-request client using session cookies."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from bizneo_oncall.auth import (
    assert_logged_in,
    describe_http_response,
    extract_csrf_token,
    extract_employee_id,
    load_session_cookies,
    load_session_employee_id,
    load_session_projects,
    save_session_employee_id,
    save_session_projects,
)
from bizneo_oncall.form import (
    build_logged_time_request,
    count_requested_ranges,
    format_period_list,
    group_entries_by_day,
    merge_projects,
    parse_day_request_ids,
    parse_logged_hours,
    parse_project_options,
    parse_request_show,
    verify_expected_ranges,
)
from bizneo_oncall.models import PlannedEntry, Project, SubmitResult, TimeEntry


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        months.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


class BizneoClient(Protocol):
    def list_projects(self) -> list[Project]: ...

    def list_time_entries(self, start: date, end: date) -> list[TimeEntry]: ...

    def create_time_entry(self, entry: PlannedEntry) -> TimeEntry: ...


class HttpxBizneoClient:
    """Client for Bizneo employee logged-time requests."""

    def __init__(
        self,
        *,
        base_url: str,
        employee_id: str,
        projects: list[Project],
        session_file: Path,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._employee_id = employee_id
        self._session_file = session_file
        self._projects = projects
        self._request_path = "/time-attendance/logged-time-requests"
        self._new_request_path = "/time-attendance/logged-time-requests/new"
        self._logged_time_path = "/time-attendance/logged-time/new"
        self._request_cache: dict[str, tuple[date | None, list[TimeEntry]]] = {}
        self._day_request_ids: dict[date, str] = {}
        host = urlparse(base_url).hostname or ""
        cookies = load_session_cookies(session_file, host=host)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Accept": "text/html,application/xhtml+xml,application/json",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            cookies=cookies,
            follow_redirects=True,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> HttpxBizneoClient:
        base_url = os.environ.get("BIZNEO_BASE_URL", "").strip()
        session_file = Path(
            os.environ.get("BIZNEO_SESSION_FILE", ".bizneo-session.json")
        )
        if not base_url:
            raise RuntimeError("Set BIZNEO_BASE_URL in .env")
        employee_id = load_session_employee_id(session_file) or ""
        projects: list[Project] = []
        return cls(
            base_url=base_url,
            employee_id=employee_id,
            projects=projects,
            session_file=session_file,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpxBizneoClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def employee_id(self) -> str:
        return self._employee_id

    def resolve_employee_id(self, around: date | None = None) -> str:
        """Discover the logged-in employee id from session or Bizneo pages."""
        if self._employee_id:
            return self._employee_id

        saved = load_session_employee_id(self._session_file)
        if saved:
            self._employee_id = saved
            return saved

        day = around or date.today()
        probes: list[httpx.Response | None] = [
            self.get_request_page(day, missing_ok=True),
        ]
        home = self._client.get("/")
        if home.status_code < 400:
            assert_logged_in(html=home.text, url=str(home.url))
            probes.append(home)

        for page in probes:
            if page is None:
                continue
            found = extract_employee_id(url=str(page.url), html=page.text)
            if found:
                self._employee_id = found
                save_session_employee_id(self._session_file, found)
                return found

        raise RuntimeError(
            "Could not detect Bizneo employee id from the login session. "
            "Run `python -m bizneo_oncall login` and open a logged-time page "
            "before pressing Enter."
        )

    def list_projects(self, around: date | None = None) -> list[Project]:
        """Return projects saved at login, plus any scraped from request pages."""
        cached = [
            Project(id=project_id, name=name)
            for project_id, name in load_session_projects(self._session_file)
        ]
        days: list[date] = []
        if around is not None:
            days.extend((around, around + timedelta(days=1)))
        days.append(date.today())
        scraped: list[Project] = []
        for day in days:
            page = self.get_request_page(day, missing_ok=True)
            if page is None:
                continue
            scraped = merge_projects(scraped, parse_project_options(page.text))
            if scraped:
                save_session_projects(
                    self._session_file,
                    [(project.id, project.name) for project in merge_projects(cached, scraped)],
                )
                break
        return merge_projects(self._projects, cached, scraped)

    def list_time_entries(self, start: date, end: date) -> list[TimeEntry]:
        """Read existing requests via my-logs day→id map, then each request page."""
        self._refresh_day_request_ids(start, end)
        entries: list[TimeEntry] = []
        day = start
        while day <= end:
            request_id = self._day_request_ids.get(day)
            if request_id:
                request_day, periods = self._get_request_show(request_id)
                if request_day == day:
                    entries.extend(periods)
            else:
                page = self.get_logged_time_page(day, missing_ok=True)
                if page is not None:
                    entries.extend(parse_logged_hours(page.text, day=day))
            day += timedelta(days=1)
        return entries

    def create_time_entry(self, entry: PlannedEntry) -> TimeEntry:
        self.submit_entries([entry])
        return TimeEntry(
            id="",
            day=entry.day,
            start=entry.start,
            end=entry.end,
            project_id=entry.project_id,
            project_name=entry.project_name,
            description=entry.description,
        )

    def existing_periods(self, day: date) -> tuple[str | None, list[TimeEntry]]:
        """Return the request id and periods already present for ``day``."""
        self._refresh_day_request_ids(day, day)
        request_id = self._day_request_ids.get(day)
        if request_id:
            request_day, periods = self._get_request_show(request_id)
            if request_day == day:
                return request_id, periods
        page = self.get_logged_time_page(day, missing_ok=True)
        if page is None:
            return request_id, []
        return request_id, parse_logged_hours(page.text, day=day)

    def submit_entries(self, entries: list[PlannedEntry]) -> list[SubmitResult]:
        """Submit one Bizneo logged-time request per day, then GET-verify periods."""
        results: list[SubmitResult] = []
        for day, day_entries in group_entries_by_day(entries):
            results.append(self._submit_day(day, day_entries))
        return results

    def _submit_day(self, day: date, day_entries: list[PlannedEntry]) -> SubmitResult:
        posted = format_period_list(day_entries)
        request_id, found = self.existing_periods(day)
        already_ok, already_message = verify_expected_ranges(day_entries, found)
        if already_ok:
            suffix = f" (request {request_id})" if request_id else " (already on timesheet)"
            return SubmitResult(
                day=day,
                ok=True,
                skipped=True,
                message=f"already correct, skipped {already_message}{suffix}",
            )
        page = self.get_request_page(day, missing_ok=True)
        if page is None:
            new_url = str(
                self._client.build_request(
                    "GET",
                    self._new_request_path,
                    params={"date": day.isoformat(), "employee_id": self._employee_id},
                ).url
            )
            return SubmitResult(
                day=day,
                ok=False,
                message=(
                    f"GET {new_url} returned HTTP 404. "
                    f"Cannot submit {len(day_entries)} period(s) [{posted}]. "
                    "The day may be closed, or the session is incomplete."
                ),
            )

        csrf_token = extract_csrf_token(page.text)
        body = build_logged_time_request(
            csrf_token=csrf_token,
            employee_id=self._employee_id,
            day=day,
            entries=day_entries,
        )
        posted_count = count_requested_ranges(body)
        if posted_count != len(day_entries):
            return SubmitResult(
                day=day,
                ok=False,
                message=(
                    f"Request body has {posted_count} period(s), "
                    f"expected {len(day_entries)} [{posted}]."
                ),
            )

        request_url = str(
            self._client.build_request(
                "POST",
                self._request_path,
                params={"date": day.isoformat(), "employee_id": self._employee_id},
            ).url
        )
        response = self._client.post(
            self._request_path,
            params={"date": day.isoformat(), "employee_id": self._employee_id},
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": str(self._client.base_url),
                "Referer": str(page.url),
                "HX-Request": "true",
                "HX-Current-URL": str(page.url),
            },
        )
        post_detail = describe_http_response(
            method="POST",
            request_url=request_url,
            status_code=response.status_code,
            response_url=str(response.url),
            html=response.text,
            headers=dict(response.headers),
        )
        post_detail = f"posted periods: {posted}\n{post_detail}"
        if response.status_code >= 400:
            return SubmitResult(
                day=day,
                ok=False,
                message=(
                    f"POST returned HTTP {response.status_code}. "
                    f"Sent {len(day_entries)} period(s) [{posted}]."
                ),
                detail=post_detail,
            )
        if response.text:
            assert_logged_in(html=response.text, url=str(response.url))
        return self._verify_day(
            day,
            day_entries,
            post_html=response.text,
            post_detail=post_detail,
        )

    def _verify_day(
        self,
        day: date,
        day_entries: list[PlannedEntry],
        *,
        post_html: str,
        post_detail: str,
    ) -> SubmitResult:
        posted = format_period_list(day_entries)
        self._request_cache.clear()
        self._day_request_ids.clear()
        self._refresh_day_request_ids(day, day)
        request_id = self._day_request_ids.get(day)
        found: list[TimeEntry] = []
        verify_detail = ""
        logs = self.get_my_logs(day.year, day.month)
        if logs is not None:
            verify_detail = describe_http_response(
                method="GET",
                request_url=str(logs.url),
                status_code=logs.status_code,
                response_url=str(logs.url),
                html=logs.text,
                headers=dict(logs.headers),
            )
        if request_id is None:
            verify_detail += f"\n  request id for {day}: none"
        else:
            show = self._client.get(f"{self._request_path}/{request_id}")
            verify_detail += (
                f"\n  request id for {day}: {request_id}\n"
                + describe_http_response(
                    method="GET",
                    request_url=str(show.url),
                    status_code=show.status_code,
                    response_url=str(show.url),
                    html=show.text,
                    headers=dict(show.headers),
                )
            )
            if show.status_code < 400:
                request_day, found = parse_request_show(show.text, request_id=request_id)
                self._request_cache[request_id] = (request_day, found)
                if request_day != day:
                    verify_detail += (
                        f"\n  request {request_id} date is {request_day}, expected {day}"
                    )
                    found = []

        ok, message = verify_expected_ranges(day_entries, found)
        if request_id:
            message = f"{message} (request {request_id})"
        still_form = (
            'name="add" value="working_time"' in post_html.lower()
            or "add period" in post_html.lower()
        )
        if request_id is None:
            message = (
                f"No request id for {day} on my-logs after POST. "
                f"Sent {len(day_entries)} period(s) [{posted}]. {message}"
            )
            ok = False
        elif still_form and not ok:
            message = (
                "POST re-rendered the request form instead of submitting. "
                f"Sent {len(day_entries)} period(s) [{posted}]. {message}"
            )
        elif not ok:
            message = (
                f"GET request {request_id} verify failed after POST. "
                f"Sent {len(day_entries)} period(s) [{posted}]. {message}"
            )
        return SubmitResult(
            day=day,
            ok=ok,
            message=message,
            detail=f"{post_detail}\n{verify_detail}",
        )

    def get_my_logs(self, year: int, month: int) -> httpx.Response | None:
        """Fetch the employee timesheet for one month."""
        if not self._employee_id:
            return None
        response = self._client.get(
            f"/time-attendance/my-logs/{self._employee_id}",
            params={"month": str(month), "year": str(year)},
        )
        if response.status_code >= 400:
            return None
        assert_logged_in(html=response.text, url=str(response.url))
        return response

    def _refresh_day_request_ids(self, start: date, end: date) -> None:
        for year, month in _months_between(start, end):
            page = self.get_my_logs(year, month)
            if page is None:
                continue
            self._day_request_ids.update(parse_day_request_ids(page.text))

    def get_logged_time_page(
        self,
        day: date,
        *,
        missing_ok: bool = False,
    ) -> httpx.Response | None:
        """Fetch the timesheet day form at ``/logged-time/new``."""
        if not self._employee_id:
            if missing_ok:
                return None
            raise RuntimeError("Employee id is required to query logged time.")
        response = self._client.get(
            self._logged_time_path,
            params={
                "logged_time[user_id]": self._employee_id,
                "logged_time[date]": day.isoformat(),
                "viewed_month": str(day.month),
            },
        )
        if response.status_code == 404 and missing_ok:
            return None
        response.raise_for_status()
        assert_logged_in(html=response.text, url=str(response.url))
        return response

    def _get_request_show(self, request_id: str) -> tuple[date | None, list[TimeEntry]]:
        cached = self._request_cache.get(request_id)
        if cached is not None:
            return cached
        response = self._client.get(f"{self._request_path}/{request_id}")
        if response.status_code >= 400:
            parsed: tuple[date | None, list[TimeEntry]] = (None, [])
        else:
            parsed = parse_request_show(response.text, request_id=request_id)
        self._request_cache[request_id] = parsed
        return parsed

    def get_list_page(
        self,
        day: date,
        *,
        missing_ok: bool = False,
    ) -> httpx.Response | None:
        """Fetch the logged-time request list for one calendar day."""
        params_options: list[dict[str, str]] = [{"date": day.isoformat()}]
        if self._employee_id:
            params_options.insert(
                0,
                {"date": day.isoformat(), "employee_id": self._employee_id},
            )
        last_response: httpx.Response | None = None
        for params in params_options:
            response = self._client.get(self._request_path, params=params)
            last_response = response
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                if missing_ok:
                    continue
                response.raise_for_status()
            if response.status_code < 400:
                return response
        if missing_ok:
            return None
        assert last_response is not None
        raise RuntimeError(
            f"Bizneo returned {last_response.status_code} for {last_response.url}."
        )

    def get_request_page(
        self,
        day: date,
        *,
        missing_ok: bool = False,
    ) -> httpx.Response | None:
        """Fetch the employee request page for one calendar day."""
        params_options: list[dict[str, str]] = [{"date": day.isoformat()}]
        if self._employee_id:
            params_options.insert(
                0,
                {"date": day.isoformat(), "employee_id": self._employee_id},
            )
        last_response: httpx.Response | None = None
        for params in params_options:
            response = self._client.get(self._new_request_path, params=params)
            last_response = response
            if response.status_code == 404:
                continue
            response.raise_for_status()
            assert_logged_in(html=response.text, url=str(response.url))
            return response

        if missing_ok:
            return None
        assert last_response is not None
        raise RuntimeError(
            f"Bizneo returned 404 for {last_response.url}. "
            "Open that URL in the browser after login. If it works there, "
            "run `python -m bizneo_oncall login` again and wait until the "
            "logged-time page is visible before pressing Enter."
        )
