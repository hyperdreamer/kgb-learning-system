"""Regression tests for deprecated test-suite entry points."""

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIRECTORY = PROJECT_ROOT / "tests"
FOCUSED_REGRESSION_MODULES = tuple(
    path.name for path in sorted(TESTS_DIRECTORY.glob("test_*_regressions.py"))
)


def test_legacy_regression_entrypoint_scope_is_exact():
    from tests.conftest import _is_legacy_regression_entrypoint

    assert _is_legacy_regression_entrypoint(TESTS_DIRECTORY / "test_regression.py")
    assert not _is_legacy_regression_entrypoint(
        TESTS_DIRECTORY / "nested" / "test_regression.py"
    )


def _collect_tests(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {"QT_QPA_PLATFORM": "offscreen"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )


def test_legacy_regression_entrypoint_collects_focused_modules():
    result = _collect_tests("tests/test_regression.py")

    assert result.returncode == 0, result.stderr
    for module_name in FOCUSED_REGRESSION_MODULES:
        assert module_name in result.stdout


def test_normal_suite_collection_excludes_legacy_regression_entrypoint():
    result = _collect_tests("tests/")

    assert result.returncode == 0, result.stderr
    assert "test_regression.py" not in result.stdout
