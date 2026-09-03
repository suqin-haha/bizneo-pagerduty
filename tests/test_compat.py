"""Tests for interpreter environment workarounds."""

from __future__ import annotations

import os

from bizneo_oncall.compat import sanitize_python_sysconfig


def test_sanitize_drops_empty_sysconfig_name() -> None:
    previous = os.environ.get("_PYTHON_SYSCONFIGDATA_NAME")
    os.environ["_PYTHON_SYSCONFIGDATA_NAME"] = ""
    try:
        sanitize_python_sysconfig()
        assert "_PYTHON_SYSCONFIGDATA_NAME" not in os.environ
    finally:
        if previous is None:
            os.environ.pop("_PYTHON_SYSCONFIGDATA_NAME", None)
        else:
            os.environ["_PYTHON_SYSCONFIGDATA_NAME"] = previous


def test_sanitize_keeps_real_sysconfig_name() -> None:
    previous = os.environ.get("_PYTHON_SYSCONFIGDATA_NAME")
    os.environ["_PYTHON_SYSCONFIGDATA_NAME"] = "_sysconfigdata__linux_x86_64-linux-gnu"
    try:
        sanitize_python_sysconfig()
        assert (
            os.environ["_PYTHON_SYSCONFIGDATA_NAME"]
            == "_sysconfigdata__linux_x86_64-linux-gnu"
        )
    finally:
        if previous is None:
            os.environ.pop("_PYTHON_SYSCONFIGDATA_NAME", None)
        else:
            os.environ["_PYTHON_SYSCONFIGDATA_NAME"] = previous
