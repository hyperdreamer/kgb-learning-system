"""Tests for interactive flashcard action-button styles."""

import re

import pytest
from PyQt6.QtWidgets import QApplication


class _MockApp:
    settings = {"font_family": "Arial", "font_size": 14}
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
