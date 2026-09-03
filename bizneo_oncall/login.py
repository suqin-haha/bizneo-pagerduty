"""Browser login flow for saving a reusable Bizneo employee session."""

from __future__ import annotations

from pathlib import Path

from bizneo_oncall.auth import (
    extract_employee_id,
    save_session_employee_id,
    save_session_projects,
)
from bizneo_oncall.form import parse_project_options


def save_browser_session(base_url: str, session_file: Path) -> str | None:
    """Open Chromium, wait for manual login, then save cookies/state.

    Returns the employee id when it can be read from the open page.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for login.\n"
            "Install it with:\n"
            "  uv pip install playwright\n"
            "  uv run playwright install chromium"
        ) from exc

    session_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening {base_url}")
    print("Log into Bizneo in the browser, then press Enter here to save the session.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url.rstrip("/"), wait_until="domcontentloaded")
        print(
            "After login, open a new request page, for example:\n"
            f"  {base_url.rstrip('/')}/time-attendance/logged-time-requests/new\n"
            "then press Enter."
        )
        try:
            input()
        except EOFError as exc:
            browser.close()
            raise RuntimeError("Login cancelled.") from exc

        html = page.content()
        employee_id = extract_employee_id(url=page.url, html=html)
        projects = parse_project_options(html)
        context.storage_state(path=str(session_file))
        browser.close()

    if employee_id:
        save_session_employee_id(session_file, employee_id)
        print(f"Employee id: {employee_id}")
    else:
        print("Could not read employee id from the open page.")
        print("Open a logged-time request URL, then run login again.")
    if projects:
        save_session_projects(
            session_file,
            [(project.id, project.name) for project in projects],
        )
        print(f"Saved {len(projects)} project(s) from the open page.")
    else:
        print("No projects found on the open page.")

    print(f"Saved session to {session_file}")
    return employee_id
