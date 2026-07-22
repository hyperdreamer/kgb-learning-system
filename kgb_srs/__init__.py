"""KGB 5-Box SRS System.

A spaced-repetition learning application with Markdown + MathJax support.
"""

__version__ = "2.1.0"

# Non-PyQt modules are safe to import eagerly.
from .config import (
    load_settings,
    save_settings,
    DEFAULT_SETTINGS,
    DIR_DB,
    get_database_root,
    ensure_database_root_structure,
    CANONICAL_DB_SUBDIRS,
    is_path_under_root,
    relative_db_path,
    resolve_default_database,
    normalize_default_database,
)

__all__ = [
    "BarskyApp",
    "__version__",
    "load_settings",
    "save_settings",
    "DEFAULT_SETTINGS",
    "DIR_DB",
    "get_database_root",
    "ensure_database_root_structure",
    "CANONICAL_DB_SUBDIRS",
    "is_path_under_root",
    "relative_db_path",
    "resolve_default_database",
    "normalize_default_database",
]


def __getattr__(name: str):
    """Lazy import of BarskyApp to avoid requiring PyQt6 at import time.

    Usage::

        from kgb_srs import BarskyApp  # no PyQt imported until accessed
    """
    if name == "BarskyApp":
        from .main_window import BarskyApp

        return BarskyApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_app():
    """Lazy import of BarskyApp to avoid requiring PyQt6 at import time."""
    from .main_window import BarskyApp

    return BarskyApp
