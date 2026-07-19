"""KGB 5-Box SRS System.

A spaced-repetition learning application with Markdown + MathJax support.
"""

# Non-PyQt modules are safe to import eagerly.
from .config import load_settings, save_settings, DEFAULT_SETTINGS, DIR_DB

__all__ = [
    "BarskyApp",
    "load_settings",
    "save_settings",
    "DEFAULT_SETTINGS",
    "DIR_DB",
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
