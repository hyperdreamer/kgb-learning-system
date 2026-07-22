"""Shared pytest fixtures for the KGB SRS test suite."""

import os
from pathlib import Path
import sys
import warnings

import pytest

# Ensure the project root is on sys.path for imports.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


_HEADLESS_QPA_PLATFORMS = {"offscreen", "minimal", "headless"}
_LEGACY_REGRESSION_ENTRYPOINT = Path(__file__).with_name("test_regression.py").resolve()


def _is_legacy_regression_entrypoint(path) -> bool:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve() == _LEGACY_REGRESSION_ENTRYPOINT


def _legacy_regression_entrypoint_requested(config) -> bool:
    return any(
        _is_legacy_regression_entrypoint(str(argument).split("::", 1)[0])
        for argument in config.invocation_params.args
    )


class _LegacyRegressionEntrypoint(pytest.File):
    """Collect the focused regression modules for the deprecated path."""

    def collect(self):
        for module_path in sorted(self.path.parent.glob("test_*_regressions.py")):
            yield pytest.Module.from_parent(self, path=module_path)


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path, config):
    return _is_legacy_regression_entrypoint(
        collection_path
    ) and not _legacy_regression_entrypoint_requested(config)


@pytest.hookimpl(tryfirst=True)
def pytest_collect_file(file_path, parent):
    if _is_legacy_regression_entrypoint(
        file_path
    ) and _legacy_regression_entrypoint_requested(parent.config):
        warnings.warn(
            "tests/test_regression.py is deprecated; invoke python -m pytest "
            "tests/ or a focused regression module instead. This entry point "
            "will be removed in 3.0.",
            pytest.PytestDeprecationWarning,
            stacklevel=2,
        )
        return _LegacyRegressionEntrypoint.from_parent(parent, path=file_path)
    return None


def _has_display_server() -> bool:
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


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
