"""Application version display helpers."""

from pathlib import Path


def _read_git_directory(marker: Path) -> Path | None:
    """Resolve a checkout's ``.git`` directory or linked-worktree marker."""
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        marker_text = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    prefix = "gitdir: "
    if not marker_text.startswith(prefix):
        return None
    git_directory = Path(marker_text[len(prefix) :].strip())
    if not git_directory.is_absolute():
        git_directory = marker.parent / git_directory
    return git_directory if git_directory.is_dir() else None


def _find_git_directory(repo_dir: Path) -> Path | None:
    """Find Git metadata from *repo_dir* or one of its parent directories."""
    try:
        directory = repo_dir.resolve()
    except OSError:
        return None
    if directory.is_file():
        directory = directory.parent
    for candidate in (directory, *directory.parents):
        git_directory = _read_git_directory(candidate / ".git")
        if git_directory is not None:
            return git_directory
    return None


def get_git_branch(repo_dir=None) -> str | None:
    """Return the checked-out branch without starting a Git subprocess."""
    if repo_dir is None:
        repo_dir = Path(__file__).resolve().parent.parent
    git_directory = _find_git_directory(Path(repo_dir))
    if git_directory is None:
        return None
    try:
        head = (git_directory / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    prefix = "ref: refs/heads/"
    if not head.startswith(prefix):
        return None
    branch = head[len(prefix) :].strip()
    return branch or None


def is_development_branch(branch: str | None) -> bool:
    """Whether *branch* denotes a development build branch."""
    return branch == "dev" or bool(branch and branch.startswith("dev-"))


def get_app_version(branch_getter=None) -> str:
    """Return the package version with one ``-dev`` suffix on dev branches.

    Git is consulted only when this function is called, not while importing
    the package.  ``branch_getter`` keeps the branch-dependent behavior easy
    to test without a checkout or subprocess.
    """
    from . import __version__

    branch = (branch_getter or get_git_branch)()
    if not is_development_branch(branch):
        return __version__
    return f"{__version__.removesuffix('-dev')}-dev"
