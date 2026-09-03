"""Session cookies and CSRF handling for the Bizneo web form."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"""([^\s=]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE,
)
_LOGIN_MARKERS = (
    "iniciar sesión",
    "iniciar sesion",
    "log in to",
    "users/sign_in",
)
_FLASH_CLASS_RE = re.compile(
    r"""<(?:div|p|span|li)[^>]*class=["'][^"']*(?:flash|alert|notice|error|success)[^"']*["'][^>]*>(.*?)</(?:div|p|span|li)>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_EMPLOYEE_QUERY_RE = re.compile(
    r"""(?:employee_id|user_id)=(\d+)""",
    re.IGNORECASE,
)
_EMPLOYEE_INPUT_NAMES = (
    "logged_time_request[user_id]",
    "employee_id",
    "user_id",
)


def extract_input_values(html: str) -> dict[str, str]:
    """Return ``name -> value`` for HTML ``<input>`` tags."""
    values: dict[str, str] = {}
    for tag in _INPUT_TAG_RE.findall(html):
        attrs: dict[str, str] = {}
        for match in _ATTR_RE.finditer(tag):
            key = match.group(1).lower()
            attrs[key] = match.group(2) if match.group(2) is not None else match.group(3)
        name = attrs.get("name")
        if name:
            values[name] = attrs.get("value", "")
    return values


def extract_csrf_token(html: str) -> str:
    """Read ``_csrf_token`` or a meta CSRF token from HTML."""
    values = extract_input_values(html)
    token = values.get("_csrf_token", "").strip()
    if token:
        return token

    meta = re.search(
        r"""<meta[^>]+name=["']csrf-token["'][^>]+content=["']([^"']+)["']""",
        html,
        re.IGNORECASE,
    )
    if meta:
        return meta.group(1)

    raise RuntimeError(
        "Could not find Bizneo CSRF token. "
        "Your session may have expired; run `python -m bizneo_oncall login` again."
    )


@dataclass(frozen=True, slots=True)
class SessionCookie:
    name: str
    value: str
    domain: str
    path: str


def cookies_from_storage_state(payload: dict[str, Any]) -> dict[str, str]:
    """Read cookies from a Playwright ``storage_state`` JSON object."""
    cookies: dict[str, str] = {}
    for item in payload.get("cookies", []):
        name = str(item.get("name", "")).strip()
        if name:
            cookies[name] = str(item.get("value", ""))
    return cookies


def session_cookies_from_storage_state(payload: dict[str, Any]) -> list[SessionCookie]:
    """Read full cookie records from a Playwright ``storage_state`` object."""
    cookies: list[SessionCookie] = []
    for item in payload.get("cookies", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        cookies.append(
            SessionCookie(
                name=name,
                value=str(item.get("value", "")),
                domain=str(item.get("domain", "")),
                path=str(item.get("path") or "/"),
            )
        )
    return cookies


def cookie_matches_host(domain: str, host: str) -> bool:
    """True when a cookie domain should be sent to ``host``."""
    cleaned = domain.lstrip(".").lower()
    host = host.lower()
    if not cleaned or not host:
        return False
    return host == cleaned or host.endswith("." + cleaned)


def cookies_for_host(cookies: list[SessionCookie], host: str) -> dict[str, str]:
    """Keep cookies for ``host``. More specific domains win on name clashes."""
    ranked: dict[str, tuple[int, str]] = {}
    for cookie in cookies:
        if not cookie_matches_host(cookie.domain, host):
            continue
        score = len(cookie.domain.lstrip("."))
        current = ranked.get(cookie.name)
        if current is None or score >= current[0]:
            ranked[cookie.name] = (score, cookie.value)
    return {name: value for name, (_score, value) in ranked.items()}


def extract_employee_id(*, url: str = "", html: str = "") -> str | None:
    """Read the logged-in employee id from a Bizneo URL or form HTML."""
    query = _EMPLOYEE_QUERY_RE.search(url)
    if query:
        return query.group(1)

    values = extract_input_values(html)
    for name in _EMPLOYEE_INPUT_NAMES:
        value = values.get(name, "").strip()
        if value.isdigit():
            return value

    html_query = _EMPLOYEE_QUERY_RE.search(html)
    if html_query:
        return html_query.group(1)
    return None


def load_session_payload(session_file: Path) -> dict[str, Any]:
    """Load the Playwright session JSON object."""
    if not session_file.is_file():
        raise RuntimeError(
            f"Session file not found: {session_file}. "
            "Run `python -m bizneo_oncall login` first."
        )
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid Playwright session file.")
    return payload


def load_session_employee_id(session_file: Path) -> str | None:
    """Read a previously saved employee id from the session file."""
    if not session_file.is_file():
        return None
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    value = str(payload.get("employee_id") or "").strip()
    return value if value.isdigit() else None


def save_session_employee_id(session_file: Path, employee_id: str) -> None:
    """Store the discovered employee id next to Playwright cookies."""
    payload = load_session_payload(session_file)
    payload["employee_id"] = employee_id
    session_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_session_projects(session_file: Path) -> list[tuple[str, str]]:
    """Read projects saved during login: ``[(id, name), ...]``."""
    if not session_file.is_file():
        return []
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    rows = payload.get("projects")
    if not isinstance(rows, list):
        return []
    projects: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        project_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or project_id).strip()
        if project_id.isdigit():
            projects.append((project_id, name or project_id))
    return projects


def save_session_projects(session_file: Path, projects: list[tuple[str, str]]) -> None:
    """Store scraped projects next to Playwright cookies."""
    payload = load_session_payload(session_file)
    payload["projects"] = [
        {"id": project_id, "name": name} for project_id, name in projects
    ]
    session_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_session_cookies(session_file: Path, host: str | None = None) -> dict[str, str]:
    """Load cookies from the saved Playwright session file."""
    payload = load_session_payload(session_file)
    records = session_cookies_from_storage_state(payload)
    cookies = cookies_for_host(records, host) if host else cookies_from_storage_state(payload)
    if not cookies:
        raise RuntimeError(
            "No cookies found in the saved session. "
            "Run `python -m bizneo_oncall login` again and wait until the "
            "Bizneo home page is visible before pressing Enter."
        )
    return cookies


def is_login_page(*, html: str, url: str) -> bool:
    """True when Bizneo redirected to the login screen."""
    haystack = f"{url}\n{html}".lower()
    return any(marker in haystack for marker in _LOGIN_MARKERS)


def assert_logged_in(*, html: str, url: str) -> None:
    """Raise if the page content looks like a login page."""
    if is_login_page(html=html, url=url):
        raise RuntimeError(
            "Bizneo session expired or missing. "
            "Run `python -m bizneo_oncall login`."
        )


def extract_flash_messages(html: str) -> list[str]:
    """Extract visible success/error-like messages from Bizneo HTML."""
    messages: list[str] = []
    for raw in _FLASH_CLASS_RE.findall(html):
        cleaned = normalize_html_text(raw)
        if cleaned:
            messages.append(cleaned)
    return messages


def normalize_html_text(value: str) -> str:
    """Strip tags and collapse whitespace."""
    without_tags = _TAG_RE.sub(" ", value)
    return " ".join(without_tags.replace("&nbsp;", " ").split())


def detect_submit_result(html: str) -> tuple[bool, str]:
    """Infer submit success/failure from Bizneo HTML response."""
    messages = extract_flash_messages(html)
    if messages:
        lowered = " | ".join(messages).lower()
        if any(word in lowered for word in ("error", "errors", "invalid", "denied", "forbidden", "obligatorio", "required")):
            return False, "; ".join(messages)
        if any(word in lowered for word in ("success", "correct", "created", "saved", "solicitud", "enviado", "submitted", "pending")):
            return True, "; ".join(messages)

    text = normalize_html_text(html).lower()
    still_form = 'name="add" value="working_time"' in html.lower() or "add period" in text
    if any(word in text for word in ("pending", "awaiting approval", "request created", "request sent", "has been requested")):
        return True, "Bizneo accepted the time request (pending approval)."
    if still_form:
        return False, (
            "Bizneo returned the request form again. "
            "The Request was not submitted (do not send add=working_time)."
        )
    if any(word in text for word in ("invalid", "denied", "forbidden")):
        return False, "Bizneo returned an error after submit."
    if any(word in text for word in ("solicitud", "enviado", "submitted", "guardado", "correctamente")):
        return True, "Bizneo accepted the time request."
    return False, "Could not confirm whether Bizneo accepted the time request."


def describe_http_response(
    *,
    method: str,
    request_url: str,
    status_code: int,
    response_url: str,
    html: str,
    headers: dict[str, str] | None = None,
) -> str:
    """Build a short diagnostic dump of a Bizneo HTTP response."""
    snippet = normalize_html_text(html)
    if len(snippet) > 400:
        snippet = snippet[:400] + "..."
    if not snippet:
        snippet = "(empty body)"
    flashes = extract_flash_messages(html)
    flash_line = "; ".join(flashes) if flashes else "(none)"
    redirect = ""
    if headers:
        for key in ("location", "hx-redirect", "hx-location"):
            value = headers.get(key) or headers.get(key.title())
            if value:
                redirect += f"\n  {key}: {value}"
    return (
        f"{method} {request_url}\n"
        f"  status: {status_code}\n"
        f"  final url: {response_url}\n"
        f"  flash: {flash_line}{redirect}\n"
        f"  body: {snippet}"
    )
