"""Shared Qt application helper for headless UI tests."""

import os

import pytest


_QT_APP = None


def qt_app():
    """Return and retain a QApplication for headless UI tests."""
    global _QT_APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP
