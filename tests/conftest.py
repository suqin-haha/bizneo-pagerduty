"""Pytest fixtures and environment fixes."""

from bizneo_oncall.compat import sanitize_python_sysconfig

sanitize_python_sysconfig()
