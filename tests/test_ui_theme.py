"""Focused behavior tests for the internal semantic light-theme module."""

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QProxyStyle, QPushButton, QWidget
from kgb_srs.ui_theme import (
    LIGHT_TOKENS,
    ROLE_PROPERTY,
    STATUS_TONE_PROPERTY,
    apply_semantic_role,
    apply_status_tone,
    font_css,
    install_design_system,
    menu_stylesheet,
    normalized_font_family,
    normalized_font_size,
    review_card_stylesheet,
    review_document_css,
    stylesheet,
)


@pytest.fixture(scope="session")
def qt_app():
    """Provide the real offscreen Qt application used by theme behavior tests."""
    return QApplication.instance() or QApplication([])


def _relative_luminance(color: str) -> float:
    """Calculate WCAG relative luminance from a hand-specified hex color."""
    channels = tuple(int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))

    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(first: str, second: str) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter, darker = sorted((first_luminance, second_luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_light_tokens_have_the_required_contrast_boundaries():
    """Changing an action token to an unreadable color must fail this test."""
    expected_tokens = {
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

    assert dict(LIGHT_TOKENS) == expected_tokens
    assert LIGHT_TOKENS["review_meaning"] == LIGHT_TOKENS["primary"]

    for action_token in (
        "primary",
        "primary_hover",
        "primary_pressed",
        "success",
        "success_hover",
        "success_pressed",
        "danger",
        "danger_hover",
        "danger_pressed",
    ):
        assert _contrast_ratio("#FFFFFF", LIGHT_TOKENS[action_token]) >= 4.5

    for surface in ("canvas", "surface"):
        assert _contrast_ratio(LIGHT_TOKENS["focus"], LIGHT_TOKENS[surface]) >= 3


@pytest.mark.parametrize(
    "invalid_family",
    [None, 14, "", "   ", "Inter\nSans", "Inter; Sans", "Inter { Sans", "/* Inter */"],
)
def test_font_family_validation_rejects_non_font_values(invalid_family):
    """A malformed setting must fall back instead of becoming QSS syntax."""
    assert normalized_font_family(invalid_family, "Fallback Sans") == "Fallback Sans"


def test_font_helpers_escape_safe_font_names_and_default_invalid_sizes():
    """A quote or slash in a valid family must not terminate its QSS declaration."""
    assert normalized_font_family("  O'Brien\\Sans  ") == "O'Brien\\Sans"
    assert normalized_font_size(17) == 17
    assert normalized_font_size(True, 12) == 12
    assert normalized_font_size(0, 12) == 12
    assert normalized_font_size("17", 12) == 12
    assert font_css("O'Brien\\Sans", 17) == (
        "font-family: 'O\\'Brien\\\\Sans'; font-size: 17px;"
    )


def test_root_qss_has_semantic_role_states_and_scoped_companions():
    """Removing an interaction state or widening card/menu scope must fail."""
    root_qss = stylesheet("Inter", 16)

    for role in ("primary", "secondary", "success", "danger"):
        for control in ("QPushButton", "QToolButton"):
            selector = f'{control}[{ROLE_PROPERTY}="{role}"]'
            for state in ("", ":hover", ":pressed", ":disabled", ":focus"):
                assert f"{selector}{state}" in root_qss

    for selector in (
        "QMainWindow",
        "QDialog",
        "QLabel",
        "QLineEdit",
        "QTextEdit",
        "QComboBox",
        "QSpinBox",
        "QCheckBox::indicator",
        "QRadioButton::indicator",
        "QTableView",
        "QHeaderView::section",
        "QListView",
        "QTabBar::tab",
        "QProgressBar",
        "QMenu",
        "QToolTip",
        "QTableView::item:selected",
        "QListView::item:selected",
        "QTabBar::tab:selected",
    ):
        assert selector in root_qss

    menu_qss = menu_stylesheet("Inter", 16)
    assert "QMenu::item {" in menu_qss
    assert "padding: 6px 24px 6px 24px;" in menu_qss
    assert "QMenu::item:selected" in menu_qss

    card_qss = review_card_stylesheet("Inter", 16)
    card_root = "QWidget#reviewCardRoot"
    assert f"{card_root} {{" in card_qss
    for selector in (
        f"{card_root} QLabel",
        f"{card_root} QTextBrowser",
        f"{card_root} QTextEdit",
        f"{card_root} QPushButton",
        f"{card_root} QToolButton",
    ):
        assert selector in card_qss

    for role in ("primary", "secondary", "success", "danger"):
        for control in ("QPushButton", "QToolButton"):
            selector = f'{card_root} {control}[{ROLE_PROPERTY}="{role}"]'
            for state in ("", ":hover", ":pressed", ":disabled", ":focus"):
                assert f"{selector}{state}" in card_qss

    assert font_css("Inter", 16) in card_qss
    assert re.search(r"QWidget#reviewCard(?=[\s,{:])", card_qss) is None
    assert "\nQLabel {" not in card_qss
    assert "\nQPushButton {" not in card_qss


def test_root_qss_has_narrow_task5_presentation_selectors():
    """Task 5 presentation fixes must remain root-scoped and narrowly owned."""
    root_qss = stylesheet("Inter", 16)
    card_qss = review_card_stylesheet("Inter", 16)
    popup_qss = menu_stylesheet("Inter", 16)

    gender_ids = ("ttsGenderAll", "ttsGenderMale", "ttsGenderFemale")
    checked_selectors = tuple(
        f"QPushButton#{object_name}:enabled:checked" for object_name in gender_ids
    )
    assert set(
        re.findall(r"QPushButton#([A-Za-z0-9_]+):enabled:checked", root_qss)
    ) == set(gender_ids)

    for suffix, background in (
        ("", "primary"),
        (":hover", "primary_hover"),
        (":pressed", "primary_pressed"),
    ):
        selector_group = ",\n".join(
            f"{selector}{suffix}" for selector in checked_selectors
        )
        rule = (
            f"{selector_group} {{\n"
            f"  background-color: {LIGHT_TOKENS[background]};\n"
            f"  color: {LIGHT_TOKENS['surface']};\n"
            f"  border: 1px solid {LIGHT_TOKENS[background]};\n"
            "}"
        )
        assert rule in root_qss

    disabled_rule = (
        f'QPushButton[{ROLE_PROPERTY}="secondary"]:disabled,\n'
        f'QToolButton[{ROLE_PROPERTY}="secondary"]:disabled {{\n'
        f"  background-color: {LIGHT_TOKENS['disabled_surface']};\n"
        f"  color: {LIGHT_TOKENS['disabled_text']};\n"
        f"  border-color: {LIGHT_TOKENS['border']};\n"
        "}"
    )
    assert disabled_rule in root_qss

    close_rule = "QToolButton#meaningTabClose {\n  padding: 0;\n  margin: 0;\n}"
    assert close_rule in root_qss

    card_rule = (
        "QWidget#sentenceMeaningCard {\n"
        f"  background-color: {LIGHT_TOKENS['surface']};\n"
        f"  border: 1px solid {LIGHT_TOKENS['border']};\n"
        "  border-radius: 8px;\n"
        "}"
    )
    assert card_rule in root_qss

    assert f'QPushButton[{ROLE_PROPERTY}="secondary"]:checked' not in root_qss
    assert (
        re.search(
            rf'QToolButton\[{ROLE_PROPERTY}="icon"\][^{{]*\{{[^}}]*'
            r"(?:padding|margin): 0;",
            root_qss,
            re.DOTALL,
        )
        is None
    )

    for selector in (
        *checked_selectors,
        "QToolButton#meaningTabClose",
        "QWidget#sentenceMeaningCard",
    ):
        assert selector not in card_qss
        assert selector not in popup_qss


def test_root_qss_quiet_label_role_uses_muted_token():
    """The quiet label role must resolve at the root without widening card QSS."""
    root_qss = stylesheet("Inter", 16)
    card_qss = review_card_stylesheet("Inter", 16)
    quiet_selector = f'QLabel[{ROLE_PROPERTY}="quiet"]'
    quiet_rule = f"{quiet_selector} {{\n  color: {LIGHT_TOKENS['text_muted']};\n}}"

    assert quiet_rule in root_qss
    assert quiet_selector not in card_qss
    assert "\nQLabel {" not in card_qss
    assert f'QLabel[{ROLE_PROPERTY}="primary"]' not in root_qss


def test_review_document_css_uses_only_tokens_and_the_content_font():
    """Review documents must use the content-font boundary and token palette."""
    css = review_document_css("Noto Serif", 23)

    assert font_css("Noto Serif", 23) in css
    assert "font-family: 'Arial'" not in css
    assert "font-size: 14px" not in css

    for selector in (
        "html, body",
        "body",
        "h1, h2, h3, h4, h5, h6",
        "blockquote",
        "hr",
        "pre",
        "code",
        "table",
        "th, td",
        "th",
        "img",
        "a",
    ):
        assert selector in css

    for token_name in (
        "text",
        "text_muted",
        "border",
        "surface_subtle",
        "surface_hover",
        "primary",
        "review_meaning",
    ):
        assert LIGHT_TOKENS[token_name] in css

    rendered_colors = {color.upper() for color in re.findall(r"#[0-9a-fA-F]{6}", css)}
    assert rendered_colors
    assert rendered_colors <= {color.upper() for color in LIGHT_TOKENS.values()}
    assert "rgba(" not in css.lower()
    for legacy_color in (
        "#222",
        "#555",
        "#bdbdbd",
        "#cccccc",
        "#dddddd",
        "#eeeeee",
        "#f3f3f3",
        "#f6f8fa",
        "#1565c0",
        "#d32f2f",
    ):
        assert legacy_color not in css.lower()


def test_review_document_css_reuses_font_css_validation():
    """Hostile content-font settings cannot escape the review CSS boundary."""
    hostile_family = "Arial'; } body { color: #000; /*"
    css = review_document_css(hostile_family, "23")

    assert font_css(hostile_family, "23") in css
    assert "body { color" not in css
    assert "#000" not in css


def test_qss_generators_only_embed_the_validated_font_declaration():
    """Hostile font input must not add a declaration or selector to any QSS output."""
    safe_declaration = font_css("O'Brien\\Sans", 17)
    for generator in (stylesheet, menu_stylesheet, review_card_stylesheet):
        assert safe_declaration in generator("O'Brien\\Sans", 17)

    hostile_family = "Arial'; } QLabel#injected { color: #000; /*"
    fallback_declaration = "font-family: 'Arial'; font-size: 14px;"
    for generator in (stylesheet, menu_stylesheet, review_card_stylesheet):
        generated = generator(hostile_family, "14")
        assert fallback_declaration in generated
        assert "injected" not in generated
        assert "#000" not in generated


class _TrackingStyle(QProxyStyle):
    """Record the real QStyle lifecycle calls made by a property helper."""

    def __init__(self):
        self.polished = []
        self.unpolished = []
        super().__init__()

    def polish(self, target):
        self.polished.append(target)
        return super().polish(target)

    def unpolish(self, target):
        self.unpolished.append(target)
        return super().unpolish(target)


class _StateProbeButton(QPushButton):
    """Count update calls while retaining the real QPushButton behavior."""

    def update(self, *args):
        self.update_calls = getattr(self, "update_calls", 0) + 1
        return super().update(*args)


def test_install_and_dynamic_properties_are_local_and_preserve_widget_state(qt_app):
    """Theme installation must stay local while property changes force a restyle."""
    root = QWidget()
    root.setFont(QFont("Courier New", 19))
    child = QPushButton("Existing action", root)
    before_font = QFont(root.font())
    before_children = tuple(root.findChildren(QWidget))
    assert before_children == (child,)

    install_design_system(root, "Inter", 16)

    assert root.styleSheet() == stylesheet("Inter", 16)
    assert root.font() == before_font
    assert tuple(root.findChildren(QWidget)) == before_children
    assert all(
        "dark" not in widget.objectName().lower()
        and "classic" not in widget.objectName().lower()
        for widget in root.findChildren(QWidget)
    )

    button = _StateProbeButton("Keep this text")
    button.setEnabled(False)
    button.setCheckable(True)
    button.setChecked(True)
    button.setAccessibleName("Persistent accessible action")
    tracking_style = _TrackingStyle()
    button.setStyle(tracking_style)
    tracking_style.polished.clear()
    tracking_style.unpolished.clear()
    updates_before = getattr(button, "update_calls", 0)

    apply_semantic_role(button, "success")
    apply_status_tone(button, "danger")

    assert button.property(ROLE_PROPERTY) == "success"
    assert button.property(STATUS_TONE_PROPERTY) == "danger"
    assert button.text() == "Keep this text"
    assert not button.isEnabled()
    assert button.isCheckable()
    assert button.isChecked()
    assert button.accessibleName() == "Persistent accessible action"
    assert tracking_style.unpolished[-2:] == [button, button]
    assert tracking_style.polished[-2:] == [button, button]
    assert button.update_calls >= updates_before + 2

    with pytest.raises(ValueError):
        apply_semantic_role(button, "unrecognized")
    with pytest.raises(ValueError):
        apply_status_tone(button, "unrecognized")

    assert button.property(ROLE_PROPERTY) == "success"
    assert button.property(STATUS_TONE_PROPERTY) == "danger"
