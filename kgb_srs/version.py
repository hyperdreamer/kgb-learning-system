"""Application version display helpers."""

import subprocess
from pathlib import Path


def get_git_branch(repo_dir=None) -> str | None:
    """Return the checked-out branch, or ``None`` outside a normal Git checkout."""
    if repo_dir is None:
        repo_dir = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def is_development_branch(branch: str | None) -> bool:
    """Whether *branch* denotes a development build branch."""
    return branch == "dev" or bool(branch and branch.startswith("dev-"))


def get_app_version(branch_getter=None) -> str:
    """Return the package version, marked for development branches.

    Git is consulted only when this function is called, not while importing
    the package.  ``branch_getter`` keeps the branch-dependent behavior easy
    to test without a checkout or subprocess.
    """
    from . import __version__

    branch = (branch_getter or get_git_branch)()
    return f"{__version__}.dev" if is_development_branch(branch) else __version__
