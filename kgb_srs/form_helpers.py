"""Shared support for card-entry dialogs."""

import json
import sys
import urllib.error

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont

from .ai_provider import AIClient, AIMissingConfigError, AIProviderConfig, http_request


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


class _AIGenerateWorker(QThread):
    """Background QThread for AI API calls."""

    result = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config: AIProviderConfig, prompt: str):
        super().__init__()
        self._config = config
        self._prompt = prompt

    def run(self):
        try:
            client = AIClient(self._config)
            url, headers, body = client.build_request(self._prompt)
            raw = http_request(
                url,
                headers,
                body=json.dumps(body).encode("utf-8"),
                timeout=self._config.timeout_seconds,
                method="POST",
            )
            self.result.emit(client.parse_response(raw))
        except AIMissingConfigError as exc:
            self.error.emit(str(exc))
        except urllib.error.URLError as exc:
            self.error.emit(f"Network error: {getattr(exc, 'reason', str(exc))}")
        except ValueError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"Unexpected error: {exc}")


def create_ai_worker(config: AIProviderConfig, prompt: str):
    """Create an AI worker while honoring an explicit legacy facade override."""
    worker_class = _legacy_form_helper_override("_AIGenerateWorker", _AIGenerateWorker)
    return worker_class(config, prompt)
