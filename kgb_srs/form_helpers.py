"""Shared support for card-entry dialogs."""

import json
import urllib.error

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont

from .ai_provider import AIClient, AIMissingConfigError, AIProviderConfig, http_request


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
