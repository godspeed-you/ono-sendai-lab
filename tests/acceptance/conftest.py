"""Acceptance fixtures and row bookkeeping; helpers live in labharness.py."""

from labharness import (  # noqa: F401 - re-exported pytest hooks and fixtures
    lab,
    pytest_configure,
    pytest_runtest_logreport,
    pytest_runtest_makereport,
    pytest_sessionfinish,
)
