"""Tests for on-call time-range parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

from bizneo_oncall.parse import parse_datetime, parse_time_range


def test_parse_range_week_long() -> None:
    result = parse_time_range("Jul 31, 3:00pm - Aug 7, 3:00pm", year=2025)
    assert result.start == datetime(2025, 7, 31, 15, 0)
    assert result.end == datetime(2025, 8, 7, 15, 0)


def test_parse_range_same_day() -> None:
    result = parse_time_range("Jul 19, 10:00am - Jul 19, 6:00pm", year=2025)
    assert result.start == datetime(2025, 7, 19, 10, 0)
    assert result.end == datetime(2025, 7, 19, 18, 0)


def test_parse_datetime_with_space_before_am_pm() -> None:
    assert parse_datetime("Jul 31, 3:00 pm", year=2025) == datetime(2025, 7, 31, 15, 0)


def test_parse_iso_datetime() -> None:
    assert parse_datetime("2025-07-31 15:00", year=2099) == datetime(2025, 7, 31, 15, 0)


def test_parse_invalid_range() -> None:
    with pytest.raises(ValueError, match="Cannot parse time range"):
        parse_time_range("not a range", year=2025)
