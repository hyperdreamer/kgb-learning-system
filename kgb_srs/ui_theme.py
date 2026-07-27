"""Validated, local semantic styling for the application's light interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
import unicodedata

from PyQt6.QtWidgets import QWidget


LIGHT_TOKENS: Mapping[str, str] = MappingProxyType(
    {
        "canvas": "#F5F7FB",
        "surface": "#FFFFFF",
        "surface_subtle": "#EEF2F7",
        "surface_hover": "#E7ECF5",
        "text": "#182230",
        "text_muted": "#52637A",
        "border": "#CBD5E1",
        "focus": "#2563EB",
        "primary": "#3949AB",
        "primary_hover": "#303F9F",
        "primary_pressed": "#283593",
        "success": "#16794A",
        "success_hover": "#12643D",
        "success_pressed": "#0D5131",
        "danger": "#B42318",
        "danger_hover": "#8F1C13",
        "danger_pressed": "#74170F",
        "disabled_surface": "#E2E8F0",
        "disabled_text": "#64748B",
        "review_meaning": "#3949AB",
    }
)

ROLE_PROPERTY = "kgbRole"
STATUS_TONE_PROPERTY = "kgbStatusTone"
VALID_ROLES = frozenset({"primary", "secondary", "success", "danger", "quiet", "icon"})
VALID_STATUS_TONES = frozenset({"neutral", "success", "danger"})


_ROLE_STYLE_TOKENS: Mapping[str, tuple[str, str, str, str]] = MappingProxyType(
    {
        "primary": ("primary", "primary_hover", "primary_pressed", "surface"),
        "secondary": ("surface", "surface_hover", "surface_subtle", "text"),
        "success": ("success", "success_hover", "success_pressed", "surface"),
        "danger": ("danger", "danger_hover", "danger_pressed", "surface"),
        "quiet": ("transparent", "surface_hover", "surface_subtle", "text_muted"),
        "icon": ("transparent", "surface_hover", "surface_subtle", "text"),
    }
)


def normalized_font_family(value: object, fallback: str = "Arial") -> str:
    """Return a QSS-safe font family or the explicit fallback."""
    if not isinstance(value, str):
        return fallback

    candidate = value.strip()
    if not candidate:
        return fallback

    if any(unicodedata.category(character).startswith("C") for character in candidate):
        return fallback
    if any(fragment in candidate for fragment in (";", "{", "}", "/*", "*/")):
        return fallback

    return candidate


def normalized_font_size(value: object, fallback: int = 14) -> int:
    """Return a positive integer font size, excluding booleans."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return int(value)
    return fallback


def font_css(font_family: object, font_size: object) -> str:
    """Build the only QSS font declaration used by theme generators."""
    family = normalized_font_family(font_family)
    escaped_family = family.replace("\\", "\\\\").replace("'", "\\'")
    size = int(normalized_font_size(font_size))
    return f"font-family: '{escaped_family}'; font-size: {size}px;"


def review_document_css(font_family: object, font_size: object) -> str:
    """Return token-backed CSS for the isolated review document renderer."""
    font_declaration = font_css(font_family, font_size)
    tokens = LIGHT_TOKENS

    return f"""
html, body {{
  margin: 0;
  padding: 0;
  background-color: transparent;
}}

body {{
  {font_declaration}
  color: {tokens["text"]};
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  box-sizing: border-box;
  overflow: auto;
}}

.content-wrapper {{
  text-align: left;
  display: inline-block;
  max-width: 100%;
  width: auto;
  padding: 20px;
  box-sizing: border-box;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}

.content-wrapper > *:first-child {{
  margin-top: 0;
}}

.content-wrapper > *:last-child {{
  margin-bottom: 0;
}}

p {{
  margin: 0.45em 0;
}}

h1, h2, h3, h4, h5, h6 {{
  margin: 0.65em 0 0.35em;
  color: {tokens["text"]};
  line-height: 1.2;
}}

ul, ol {{
  margin: 0.45em 0 0.45em 1.3em;
  padding-left: 1.2em;
}}

li {{
  margin: 0.2em 0;
}}

blockquote {{
  border-left: 4px solid {tokens["border"]};
  margin: 0.7em 0;
  padding: 0.2em 0 0.2em 0.8em;
  color: {tokens["text_muted"]};
  background-color: {tokens["surface_subtle"]};
}}

hr {{
  width: 100%;
  border: 0;
  border-top: 2px solid {tokens["border"]};
  margin: 1em 0;
}}

pre {{
  background-color: {tokens["surface_subtle"]};
  border: 1px solid {tokens["border"]};
  border-radius: 6px;
  padding: 10px;
  overflow-x: auto;
  white-space: pre-wrap;
}}

code {{
  background-color: {tokens["surface_hover"]};
  border-radius: 4px;
  padding: 2px 4px;
  font-family: Consolas, Menlo, Monaco, monospace;
}}

pre code {{
  background: inherit;
  padding: 0;
}}

table {{
  border-collapse: collapse;
  margin: 0.7em 0;
  max-width: 100%;
}}

th, td {{
  border: 1px solid {tokens["border"]};
  padding: 5px 8px;
}}

th {{
  background-color: {tokens["surface_hover"]};
  font-weight: bold;
}}

img {{
  max-width: 100%;
  height: auto;
}}

a {{
  color: {tokens["primary"]};
}}

.review-meaning {{
  color: {tokens["review_meaning"]};
}}
"""


def _rule(selectors: Sequence[str], declarations: Sequence[str]) -> str:
    """Format a static QSS rule from trusted module-owned tokens."""
    joined_selectors = ",\n".join(selectors)
    joined_declarations = "\n".join(f"  {declaration}" for declaration in declarations)
    return f"{joined_selectors} {{\n{joined_declarations}\n}}"


def _role_styles(scope: str = "") -> str:
    """Return property-based button states, optionally below a card root."""
    prefix = f"{scope} " if scope else ""
    rules = []

    for role, (normal, hover, pressed, foreground) in _ROLE_STYLE_TOKENS.items():
        controls = tuple(
            f'{prefix}{control}[{ROLE_PROPERTY}="{role}"]'
            for control in ("QPushButton", "QToolButton")
        )
        normal_border = normal if role in {"primary", "success", "danger"} else "border"
        if role in {"quiet", "icon"}:
            normal_border = "transparent"

        rules.append(
            _rule(
                controls,
                (
                    f"background-color: {LIGHT_TOKENS[normal] if normal != 'transparent' else normal};",
                    f"color: {LIGHT_TOKENS[foreground]};",
                    f"border: 1px solid {LIGHT_TOKENS[normal_border] if normal_border != 'transparent' else normal_border};",
                ),
            )
        )
        rules.append(
            _rule(
                tuple(f"{control}:hover" for control in controls),
                (
                    f"background-color: {LIGHT_TOKENS[hover]};",
                    f"border-color: {LIGHT_TOKENS['focus']};",
                ),
            )
        )
        rules.append(
            _rule(
                tuple(f"{control}:pressed" for control in controls),
                (
                    f"background-color: {LIGHT_TOKENS[pressed]};",
                    f"border-color: {LIGHT_TOKENS[pressed]};",
                ),
            )
        )
        rules.append(
            _rule(
                tuple(f"{control}:disabled" for control in controls),
                (
                    f"background-color: {LIGHT_TOKENS['disabled_surface']};",
                    f"color: {LIGHT_TOKENS['disabled_text']};",
                    f"border-color: {LIGHT_TOKENS['border']};",
                ),
            )
        )
        rules.append(
            _rule(
                tuple(f"{control}:focus" for control in controls),
                (f"border: 2px solid {LIGHT_TOKENS['focus']};",),
            )
        )

    return "\n\n".join(rules)


def stylesheet(font_family: object, font_size: object) -> str:
    """Return the local root stylesheet for light-theme application windows."""
    font_declaration = font_css(font_family, font_size)
    tokens = LIGHT_TOKENS
    role_styles = _role_styles()

    return f"""
QWidget {{
  background-color: {tokens["canvas"]};
  color: {tokens["text"]};
  {font_declaration}
}}

QMainWindow,
QDialog {{
  background-color: {tokens["canvas"]};
  color: {tokens["text"]};
  {font_declaration}
}}

QLabel {{
  background-color: transparent;
  color: {tokens["text"]};
}}

QPushButton,
QToolButton {{
  background-color: {tokens["surface_subtle"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 5px;
  padding: 6px 12px;
}}

QPushButton:hover,
QToolButton:hover {{
  background-color: {tokens["surface_hover"]};
  border-color: {tokens["focus"]};
}}

QPushButton:pressed,
QToolButton:pressed {{
  background-color: {tokens["surface_subtle"]};
}}

QPushButton:disabled,
QToolButton:disabled {{
  background-color: {tokens["disabled_surface"]};
  color: {tokens["disabled_text"]};
  border-color: {tokens["border"]};
}}

QPushButton:focus,
QToolButton:focus {{
  border: 2px solid {tokens["focus"]};
}}

{role_styles}

QLineEdit,
QTextEdit,
QPlainTextEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox,
QAbstractSpinBox {{
  background-color: {tokens["surface"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 4px;
  padding: 5px 7px;
  selection-background-color: {tokens["primary"]};
  selection-color: {tokens["surface"]};
}}

QLineEdit:hover,
QTextEdit:hover,
QPlainTextEdit:hover,
QComboBox:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QAbstractSpinBox:hover {{
  border-color: {tokens["focus"]};
}}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QAbstractSpinBox:focus {{
  border: 2px solid {tokens["focus"]};
}}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QAbstractSpinBox:disabled {{
  background-color: {tokens["disabled_surface"]};
  color: {tokens["disabled_text"]};
}}

QComboBox::drop-down {{
  border-left: 1px solid {tokens["border"]};
  width: 22px;
}}

QAbstractSpinBox::up-button,
QAbstractSpinBox::down-button {{
  background-color: {tokens["surface_subtle"]};
  border-left: 1px solid {tokens["border"]};
  width: 20px;
}}

QAbstractSpinBox::up-button:hover,
QAbstractSpinBox::down-button:hover {{
  background-color: {tokens["surface_hover"]};
}}

QCheckBox,
QRadioButton {{
  color: {tokens["text"]};
  spacing: 7px;
}}

QCheckBox:disabled,
QRadioButton:disabled {{
  color: {tokens["disabled_text"]};
}}

QCheckBox::indicator,
QRadioButton::indicator {{
  width: 15px;
  height: 15px;
  background-color: {tokens["surface"]};
  border: 1px solid {tokens["border"]};
}}

QCheckBox::indicator:hover,
QRadioButton::indicator:hover {{
  border: 2px solid {tokens["focus"]};
}}

QCheckBox::indicator:checked,
QRadioButton::indicator:checked {{
  background-color: {tokens["primary"]};
  border-color: {tokens["primary"]};
}}

QCheckBox::indicator:checked:hover,
QRadioButton::indicator:checked:hover {{
  background-color: {tokens["primary_hover"]};
}}

QCheckBox::indicator:disabled,
QRadioButton::indicator:disabled {{
  background-color: {tokens["disabled_surface"]};
  border-color: {tokens["border"]};
}}

QTableView,
QTreeView,
QListView,
QListWidget {{
  background-color: {tokens["surface"]};
  alternate-background-color: {tokens["surface_subtle"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  selection-background-color: {tokens["primary"]};
  selection-color: {tokens["surface"]};
}}

QTableView:focus,
QTreeView:focus,
QListView:focus,
QListWidget:focus {{
  border: 2px solid {tokens["focus"]};
}}

QTableView::item:hover,
QTreeView::item:hover,
QListView::item:hover,
QListWidget::item:hover {{
  background-color: {tokens["surface_hover"]};
}}

QTableView::item:selected,
QTreeView::item:selected,
QListView::item:selected,
QListWidget::item:selected {{
  background-color: {tokens["primary"]};
  color: {tokens["surface"]};
}}

QTableView::item:disabled,
QTreeView::item:disabled,
QListView::item:disabled,
QListWidget::item:disabled {{
  color: {tokens["disabled_text"]};
}}

QHeaderView::section {{
  background-color: {tokens["surface_subtle"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  padding: 5px 7px;
}}

QHeaderView::section:hover {{
  background-color: {tokens["surface_hover"]};
}}

QTabWidget::pane {{
  background-color: {tokens["surface"]};
  border: 1px solid {tokens["border"]};
}}

QTabBar::tab {{
  background-color: {tokens["surface_subtle"]};
  color: {tokens["text_muted"]};
  border: 1px solid {tokens["border"]};
  border-bottom: none;
  padding: 7px 14px;
}}

QTabBar::tab:hover {{
  background-color: {tokens["surface_hover"]};
  color: {tokens["text"]};
}}

QTabBar::tab:selected {{
  background-color: {tokens["surface"]};
  color: {tokens["primary"]};
  border-top: 2px solid {tokens["primary"]};
}}

QTabBar::tab:disabled {{
  background-color: {tokens["disabled_surface"]};
  color: {tokens["disabled_text"]};
}}

QTabBar::tab:focus {{
  border: 2px solid {tokens["focus"]};
}}

QProgressBar {{
  background-color: {tokens["disabled_surface"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 4px;
  text-align: center;
}}

QProgressBar::chunk {{
  background-color: {tokens["primary"]};
  border-radius: 3px;
}}

QMenu {{
  background-color: {tokens["surface"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 5px;
  {font_declaration}
}}

QMenu::item {{
  background-color: transparent;
  color: {tokens["text"]};
  padding: 6px 24px 6px 24px;
}}

QMenu::item:selected {{
  background-color: {tokens["surface_hover"]};
  color: {tokens["text"]};
}}

QMenu::item:disabled {{
  color: {tokens["disabled_text"]};
}}

QMenu::separator {{
  height: 1px;
  background-color: {tokens["border"]};
  margin: 4px 8px;
}}

QToolTip {{
  background-color: {tokens["text"]};
  color: {tokens["surface"]};
  border: 1px solid {tokens["border"]};
  padding: 4px 6px;
  {font_declaration}
}}

QWidget[{STATUS_TONE_PROPERTY}="neutral"] {{
  color: {tokens["text_muted"]};
}}

QWidget[{STATUS_TONE_PROPERTY}="success"] {{
  color: {tokens["success"]};
}}

QWidget[{STATUS_TONE_PROPERTY}="danger"] {{
  color: {tokens["danger"]};
}}
""".strip()


def review_card_stylesheet(font_family: object, font_size: object) -> str:
    """Return QSS restricted to the proxy-widget review-card subtree."""
    font_declaration = font_css(font_family, font_size)
    tokens = LIGHT_TOKENS
    role_styles = _role_styles("QWidget#reviewCard")

    return f"""
QWidget#reviewCard {{
  background-color: {tokens["surface"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 8px;
  {font_declaration}
}}

QWidget#reviewCard QLabel {{
  background-color: transparent;
  color: {tokens["text"]};
}}

QWidget#reviewCard QLabel#reviewMeaning {{
  color: {tokens["review_meaning"]};
}}

QWidget#reviewCard QTextBrowser,
QWidget#reviewCard QTextEdit {{
  background-color: {tokens["surface"]};
  color: {tokens["text"]};
  border: none;
  selection-background-color: {tokens["primary"]};
  selection-color: {tokens["surface"]};
}}

QWidget#reviewCard QTextBrowser:focus,
QWidget#reviewCard QTextEdit:focus {{
  border: 2px solid {tokens["focus"]};
}}

QWidget#reviewCard QPushButton,
QWidget#reviewCard QToolButton {{
  background-color: {tokens["surface_subtle"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 5px;
  padding: 6px 12px;
}}

QWidget#reviewCard QPushButton:hover,
QWidget#reviewCard QToolButton:hover {{
  background-color: {tokens["surface_hover"]};
  border-color: {tokens["focus"]};
}}

QWidget#reviewCard QPushButton:pressed,
QWidget#reviewCard QToolButton:pressed {{
  background-color: {tokens["surface_subtle"]};
}}

QWidget#reviewCard QPushButton:disabled,
QWidget#reviewCard QToolButton:disabled {{
  background-color: {tokens["disabled_surface"]};
  color: {tokens["disabled_text"]};
}}

QWidget#reviewCard QPushButton:focus,
QWidget#reviewCard QToolButton:focus {{
  border: 2px solid {tokens["focus"]};
}}

{role_styles}

QWidget#reviewCard QWidget[{STATUS_TONE_PROPERTY}="neutral"] {{
  color: {tokens["text_muted"]};
}}

QWidget#reviewCard QWidget[{STATUS_TONE_PROPERTY}="success"] {{
  color: {tokens["success"]};
}}

QWidget#reviewCard QWidget[{STATUS_TONE_PROPERTY}="danger"] {{
  color: {tokens["danger"]};
}}
""".strip()


def menu_stylesheet(font_family: object, font_size: object) -> str:
    """Return QSS restricted to popup menus and their readable item spacing."""
    font_declaration = font_css(font_family, font_size)
    tokens = LIGHT_TOKENS

    return f"""
QMenu {{
  background-color: {tokens["surface"]};
  color: {tokens["text"]};
  border: 1px solid {tokens["border"]};
  border-radius: 5px;
  {font_declaration}
}}

QMenu::item {{
  background-color: transparent;
  color: {tokens["text"]};
  padding: 6px 24px 6px 24px;
}}

QMenu::item:selected {{
  background-color: {tokens["surface_hover"]};
  color: {tokens["text"]};
}}

QMenu::item:disabled {{
  color: {tokens["disabled_text"]};
}}

QMenu::separator {{
  height: 1px;
  background-color: {tokens["border"]};
  margin: 4px 8px;
}}
""".strip()


def install_design_system(
    widget: QWidget, font_family: object, font_size: object
) -> None:
    """Apply the generated stylesheet only to the supplied widget boundary."""
    widget.setStyleSheet(stylesheet(font_family, font_size))


def _repolish(widget: QWidget) -> None:
    """Refresh a widget after a dynamic QSS property changes."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def apply_semantic_role(widget: QWidget, role: str) -> None:
    """Set a validated action role and immediately refresh its QSS state."""
    if not isinstance(role, str) or role not in VALID_ROLES:
        raise ValueError(f"Unsupported semantic role: {role!r}")

    widget.setProperty(ROLE_PROPERTY, role)
    _repolish(widget)


def apply_status_tone(widget: QWidget, tone: str) -> None:
    """Set a validated status tone and immediately refresh its QSS state."""
    if not isinstance(tone, str) or tone not in VALID_STATUS_TONES:
        raise ValueError(f"Unsupported status tone: {tone!r}")

    widget.setProperty(STATUS_TONE_PROPERTY, tone)
    _repolish(widget)
