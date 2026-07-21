"""Tests for interactive flashcard action-button styles and content fonts."""

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


class _MockApp:
    settings = {
        "font_family": "Arial",
        "font_size": 14,
        "content_font_family": "Georgia",
        "content_font_size": 22,
    }
    current_card = None

    def speak_text(self, *_args):
        pass

    def flip_card(self):
        pass

    def check_card_drop(self, *_args):
        pass


@pytest.fixture(scope="session")
def flashcard():
    from kgb_srs.graphics import FlashCardItem

    app = QApplication.instance() or QApplication([])
    item = FlashCardItem(_MockApp(), 200, 150, 350, 200)
    yield item
    del item
    app.processEvents()


def _background(stylesheet, selector):
    match = re.search(
        rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", stylesheet
    )
    assert match, f"Missing stylesheet selector: {selector}"
    color = re.search(r"background-color:\s*(#[0-9A-Fa-f]{6})", match.group(1))
    assert color, f"Missing background color in: {selector}"
    return color.group(1).upper()


@pytest.mark.parametrize(
    ("attribute", "object_name", "normal", "hover", "pressed"),
    [
        ("tts_btn", "ttsBtn", "#9C27B0", "#AB47BC", "#8E24AA"),
        ("flip_btn", "revealBtn", "#2196F3", "#42A5F5", "#1E88E5"),
    ],
)
def test_action_buttons_have_distinct_interaction_colors(
    flashcard, attribute, object_name, normal, hover, pressed
):
    button = getattr(flashcard, attribute)
    stylesheet = button.styleSheet()

    assert button.objectName() == object_name
    assert _background(stylesheet, f"QPushButton#{object_name}") == normal
    assert _background(stylesheet, f"QPushButton#{object_name}:hover") == hover
    assert _background(stylesheet, f"QPushButton#{object_name}:pressed") == pressed


@pytest.mark.parametrize(
    ("attribute", "object_name"),
    [("tts_btn", "ttsBtn"), ("flip_btn", "revealBtn")],
)
def test_action_buttons_keep_disabled_state_muted(flashcard, attribute, object_name):
    stylesheet = getattr(flashcard, attribute).styleSheet()
    selector = f"QPushButton#{object_name}:disabled"

    assert _background(stylesheet, selector) == "#CFD8DC"
    rule = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", stylesheet)
    assert rule is not None
    assert "color: #78909C" in rule.group(1)


def test_action_buttons_use_ui_font_not_content_font(flashcard):
    """Listen/Reveal buttons keep UI font_family/font_size in stylesheets."""
    for attr in ("tts_btn", "flip_btn"):
        ss = getattr(flashcard, attr).styleSheet()
        assert "font-family: 'Arial'" in ss
        assert "font-size: 14px" in ss
        assert "Georgia" not in ss
        assert "font-size: 22px" not in ss


def test_set_text_uses_content_font_not_ui_font_plus_four(monkeypatch):
    """FlashCardItem.set_text uses content_font_* directly (no UI size + 4)."""
    from kgb_srs import graphics as graphics_mod
    from kgb_srs.graphics import FlashCardItem

    app = QApplication.instance() or QApplication([])
    captured = {}

    def fake_build(markdown_text, font_family, font_size, include_mathjax=True):
        captured["markdown_text"] = markdown_text
        captured["font_family"] = font_family
        captured["font_size"] = font_size
        captured["include_mathjax"] = include_mathjax
        return "<html>ok</html>"

    monkeypatch.setattr(graphics_mod, "build_review_html", fake_build)

    mock = _MockApp()
    mock.settings = {
        "font_family": "Arial",
        "font_size": 14,
        "content_font_family": "Georgia",
        "content_font_size": 22,
    }
    item = FlashCardItem(mock, 200, 150, 350, 200)
    item.set_text("hello **world**", is_flipped=False, text_to_speak="hello")

    assert captured["font_family"] == "Georgia"
    assert captured["font_size"] == 22  # not 14+4=18
    assert captured["markdown_text"] == "hello **world**"
    del item
    app.processEvents()


def test_math_placeholder_collision_preserves_literal_and_math():
    from kgb_srs.markdown_utils import (
        markdown_to_html_fragment,
        markdown_to_plain_text,
    )

    source = "BARSKYMATHPLACEHOLDER0TOKEN plus $x$"
    html = markdown_to_html_fragment(source)

    assert "BARSKYMATHPLACEHOLDER0TOKEN" in html
    assert "$x$" in html
    assert markdown_to_plain_text(source) == "BARSKYMATHPLACEHOLDER0TOKEN plus x"
