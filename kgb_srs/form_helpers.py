"""Shared support for card-entry dialogs."""

import sys

from PyQt6.QtGui import QFont

from .ai_provider import AIProviderConfig, _get_ai_worker_class
from .ui_theme import install_design_system


def _legacy_form_helper_override(name: str, canonical):
    """Return an explicitly patched 2.x facade helper when one exists."""
    legacy_forms = sys.modules.get(f"{__package__}.forms")
    if legacy_forms is None:
        return canonical
    return vars(legacy_forms).get(name, canonical)


def _apply_ui_font(widget, settings: dict | None, parent=None) -> None:
    """Apply Appearance → UI Font to a dialog/widget.

    Prefer explicit settings (font_family / font_size). Fall back to the
    parent widget font when settings are incomplete so card editors still
    track the main window chrome font.
    """
    settings = settings or {}
    family = settings.get("font_family")
    size = settings.get("font_size")
    if family and size:
        try:
            widget.setFont(QFont(str(family), int(size)))
            return
        except (TypeError, ValueError):
            pass
    if parent is not None:
        widget.setFont(parent.font())


def apply_ui_font(widget, settings: dict | None, parent=None) -> None:
    """Apply the UI font while honoring an explicit legacy facade override."""
    helper = _legacy_form_helper_override("_apply_ui_font", _apply_ui_font)
    helper(widget, settings, parent)
    install_design_system(widget, widget.font().family(), widget.font().pointSize())


# Canonical AI worker class, sourced from :mod:`ai_provider`.
# Kept as a module-level name for backward-compatible imports and
# monkeypatching by test suites.
_AIGenerateWorker = _get_ai_worker_class()


def create_ai_worker(config: AIProviderConfig, prompt: str):
    """Create an AI worker while honoring an explicit legacy facade override."""
    worker_class = _legacy_form_helper_override("_AIGenerateWorker", _AIGenerateWorker)
    return worker_class(config, prompt)
