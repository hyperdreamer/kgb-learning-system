"""Shared pytest fixtures for the KGB SRS test suite."""

import os
import sys

# Ensure the project root is on sys.path for imports.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


_HEADLESS_QPA_PLATFORMS = {"offscreen", "minimal", "headless"}


def _has_display_server() -> bool:
    if not sys.platform.startswith("linux"):
        return True
    return bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _uses_headless_qt() -> bool:
    platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    return platform in _HEADLESS_QPA_PLATFORMS or not _has_display_server()


def pytest_configure():
    if not _has_display_server():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_runtest_setup():
    if not _uses_headless_qt():
        return
    from kgb_srs import graphics

    graphics.HAS_WEBENGINE = False
