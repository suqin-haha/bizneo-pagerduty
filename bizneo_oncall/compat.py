"""Workarounds for broken interpreter / terminal environments."""

from __future__ import annotations

import os


def sanitize_python_sysconfig() -> None:
    """Drop an empty ``_PYTHON_SYSCONFIGDATA_NAME``.

    Some terminals export the variable as an empty string. Importing
    ``zoneinfo`` then crashes in ``sysconfig`` with ``ValueError: Empty
    module name``.
    """
    name = os.environ.get("_PYTHON_SYSCONFIGDATA_NAME")
    if name is not None and not name.strip():
        del os.environ["_PYTHON_SYSCONFIGDATA_NAME"]


sanitize_python_sysconfig()
