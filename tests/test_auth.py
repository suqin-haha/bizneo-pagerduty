"""Tests for Bizneo session and CSRF helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from bizneo_oncall.auth import (
    SessionCookie,
    assert_logged_in,
    cookie_matches_host,
    cookies_for_host,
    cookies_from_storage_state,
    describe_http_response,
    detect_submit_result,
    extract_csrf_token,
    extract_employee_id,
    extract_flash_messages,
    load_session_cookies,
    load_session_employee_id,
    load_session_projects,
    save_session_employee_id,
    save_session_projects,
)


def test_extract_employee_id_from_url() -> None:
    assert (
        extract_employee_id(
            url="https://example.bizneohr.com/time-attendance/logged-time-requests?date=2026-08-01&employee_id=1001"
        )
        == "1001"
    )


def test_extract_employee_id_from_form() -> None:
    html = '<input type="hidden" name="logged_time_request[user_id]" value="1001">'
    assert extract_employee_id(html=html) == "1001"


def test_save_and_load_session_employee_id(tmp_path: Path) -> None:
    session_file = tmp_path / "state.json"
    session_file.write_text('{"cookies":[]}', encoding="utf-8")
    save_session_employee_id(session_file, "1001")
    assert load_session_employee_id(session_file) == "1001"
    save_session_projects(session_file, [("15624873", "On-Call Team")])
    assert load_session_projects(session_file) == [("15624873", "On-Call Team")]


def test_extract_csrf_token_from_hidden_input() -> None:
    html = """
    <form>
      <input type="hidden" name="_csrf_token" value="abc123">
    </form>
    """
    assert extract_csrf_token(html) == "abc123"


def test_extract_csrf_token_from_meta_tag() -> None:
    html = '<meta name="csrf-token" content="meta-token">'
    assert extract_csrf_token(html) == "meta-token"


def test_cookies_from_storage_state() -> None:
    payload = {"cookies": [{"name": "session", "value": "cookie-value"}]}
    assert cookies_from_storage_state(payload) == {"session": "cookie-value"}


def test_load_session_cookies(tmp_path: Path) -> None:
    session_file = tmp_path / "state.json"
    session_file.write_text(
        '{"cookies":[{"name":"session","value":"cookie-value"}]}',
        encoding="utf-8",
    )
    assert load_session_cookies(session_file) == {"session": "cookie-value"}


def test_cookies_for_host_prefers_bizneo_over_google() -> None:
    cookies = [
        SessionCookie(name="_hcmex_key", value="google", domain=".google.com", path="/"),
        SessionCookie(name="_hcmex_key", value="tenant", domain="example.bizneohr.com", path="/"),
        SessionCookie(name="_ga", value="ga", domain=".google.com", path="/"),
    ]
    assert cookies_for_host(cookies, "example.bizneohr.com") == {
        "_hcmex_key": "tenant",
    }


def test_cookie_matches_host() -> None:
    assert cookie_matches_host(".bizneohr.com", "example.bizneohr.com")
    assert not cookie_matches_host(".google.com", "example.bizneohr.com")


def test_assert_logged_in_raises_on_login_page() -> None:
    with pytest.raises(RuntimeError, match="session expired or missing"):
        assert_logged_in(
            html="<html>Iniciar sesión</html>",
            url="https://example.bizneohr.com/users/sign_in",
        )


def test_extract_flash_messages() -> None:
    html = '<div class="flash success">Saved correctly</div>'
    assert extract_flash_messages(html) == ["Saved correctly"]


def test_detect_submit_result_success() -> None:
    html = '<div class="alert success">Solicitud enviada correctamente</div>'
    assert detect_submit_result(html) == (
        True,
        "Solicitud enviada correctamente",
    )


def test_detect_submit_result_failure() -> None:
    html = '<div class="alert error">Project is required</div>'
    assert detect_submit_result(html) == (
        False,
        "Project is required",
    )


def test_detect_submit_result_unknown() -> None:
    html = "<html><body>Request page</body></html>"
    assert detect_submit_result(html) == (
        False,
        "Could not confirm whether Bizneo accepted the time request.",
    )


def test_detect_submit_result_form_returned() -> None:
    html = '<button type="submit" name="add" value="working_time">add period</button>'
    ok, message = detect_submit_result(html)
    assert ok is False
    assert "form again" in message


def test_describe_http_response() -> None:
    detail = describe_http_response(
        method="POST",
        request_url="https://example.bizneohr.com/time-attendance/logged-time-requests",
        status_code=200,
        response_url="https://example.bizneohr.com/time-attendance/logged-time-requests",
        html='<div class="alert">add period</div>',
    )
    assert "status: 200" in detail
    assert "add period" in detail


def test_detect_submit_result_pending() -> None:
    html = "<div>Request pending approval</div>"
    ok, message = detect_submit_result(html)
    assert ok is True
    assert "pending" in message.lower()
