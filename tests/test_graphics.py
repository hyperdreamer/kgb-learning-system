"""Tests for interactive flashcard action-button styles and content fonts."""

import logging
import os

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

    def process_answer(self, *_args):
        pass


@pytest.fixture(scope="session")
def flashcard():
    from kgb_srs.graphics import FlashCardItem

    app = QApplication.instance() or QApplication([])
    item = FlashCardItem(_MockApp(), 200, 150, 350, 200)
    yield item
    del item
    app.processEvents()


def test_reveal_button_defers_flip_until_clicked_dispatch_finishes():
    """Reveal must not destroy its proxy while Qt is dispatching the click."""
    from kgb_srs.graphics import FlashCardItem

    class FlipRecordingApp(_MockApp):
        def __init__(self):
            self.events = []

        def flip_card(self):
            self.events.append("flip")

    app = QApplication.instance() or QApplication([])
    mock = FlipRecordingApp()
    item = FlashCardItem(mock, 200, 150, 350, 200)
    item.flip_btn.clicked.connect(lambda: mock.events.append("clicked"))

    item.flip_btn.click()

    assert mock.events == ["clicked"]
    app.processEvents()
    assert mock.events == ["clicked", "flip"]

    del item
    app.processEvents()


def test_review_card_actions_use_semantic_roles_and_container_state_qss(flashcard):
    """The real card owns the canonical semantic QSS boundary."""
    from kgb_srs.ui_theme import ROLE_PROPERTY, review_card_stylesheet

    assert flashcard.container.objectName() == "reviewCardRoot"
    assert flashcard.container.styleSheet() == review_card_stylesheet("Arial", 14)

    expected_roles = {
        "tts_btn": "secondary",
        "flip_btn": "primary",
        "incorrect_btn": "danger",
        "correct_btn": "success",
    }
    for attribute, role in expected_roles.items():
        button = getattr(flashcard, attribute)
        assert button.property(ROLE_PROPERTY) == role
        assert button.styleSheet() == ""

    flashcard.set_text("front", is_flipped=False)
    assert not flashcard.tts_btn.isHidden()
    assert not flashcard.flip_btn.isHidden()
    assert flashcard.incorrect_btn.isHidden()
    assert flashcard.correct_btn.isHidden()

    flashcard.set_text("front\n\n---\n\nback", is_flipped=True)
    assert not flashcard.tts_btn.isHidden()
    assert flashcard.flip_btn.isHidden()
    assert not flashcard.incorrect_btn.isHidden()
    assert not flashcard.correct_btn.isHidden()


def test_review_card_container_qss_has_disabled_semantic_states(flashcard):
    """Disabled action styling remains root-scoped and token-backed."""
    from kgb_srs.ui_theme import LIGHT_TOKENS, ROLE_PROPERTY

    card_qss = flashcard.container.styleSheet()
    card_root = "QWidget#reviewCardRoot"
    for role in ("primary", "secondary", "success", "danger"):
        for control in ("QPushButton", "QToolButton"):
            selector = f'{card_root} {control}[{ROLE_PROPERTY}="{role}"]:disabled'
            assert selector in card_qss

    assert LIGHT_TOKENS["disabled_surface"] in card_qss
    assert LIGHT_TOKENS["disabled_text"] in card_qss
    for attribute in ("tts_btn", "flip_btn", "incorrect_btn", "correct_btn"):
        assert getattr(flashcard, attribute).styleSheet() == ""


def test_review_card_qss_uses_validated_ui_font_not_content_font(flashcard):
    """The container uses only the validated UI-font declaration."""
    from kgb_srs.ui_theme import font_css

    card_qss = flashcard.container.styleSheet()
    assert font_css("Arial", 14) in card_qss
    assert "Georgia" not in card_qss
    assert "font-size: 22px" not in card_qss
    for attribute in ("tts_btn", "flip_btn", "incorrect_btn", "correct_btn"):
        assert getattr(flashcard, attribute).styleSheet() == ""


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
    assert captured["include_mathjax"] is False
    del item
    app.processEvents()


def test_set_text_renders_unfamiliar_sentence_terms_in_bold(flashcard):
    """Markdown highlights remain bold in the proxy-safe review renderer."""
    from PyQt6.QtGui import QFont

    display_text = (
        "Revenge for a **Grievance** of a Hundred Generations "
        "May Still Be **Exacted**!\n\nHe **insists on** speaking."
    )

    flashcard.set_text(display_text, is_flipped=False)

    document = flashcard.text_widget.document()
    for unfamiliar_term in ("Grievance", "Exacted", "insists on"):
        cursor = document.find(unfamiliar_term)
        assert not cursor.isNull()
        assert cursor.charFormat().fontWeight() >= QFont.Weight.Bold.value


def test_sentence_review_bold_words_and_phrases_trigger_targeted_tts():
    """Clickable bold review targets speak only the activated word or phrase."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtTest import QTest

    from kgb_srs.catalog import DatabaseType
    from kgb_srs.graphics import FlashCardItem

    class SpeechCapturingApp(_MockApp):
        _db_type = DatabaseType.LANGUAGE_SENTENCE

        def __init__(self):
            self.spoken = []

        def speak_text(self, text, button):
            self.spoken.append((text, button))

    app = QApplication.instance() or QApplication([])
    mock = SpeechCapturingApp()
    item = FlashCardItem(mock, 300, 200, 500, 320)
    item.set_text(
        "**Box 1** | ID: `22`\n\n"
        "They aren't building **roads**; they are **tightening the noose**!\n\n"
        "---\n\n**noose**: a loop tied at one end of a rope\n\n"
        "**Box 1** | ID: 22",
        is_flipped=True,
    )

    document = item.text_widget.document()
    metadata = document.find("Box 1")
    word = document.find("roads")
    phrase = document.find("tightening the noose")
    definition_term = document.find("noose", phrase.selectionEnd())
    metadata_shaped_body_text = document.find("Box 1", metadata.selectionEnd())

    assert not metadata.charFormat().isAnchor()
    for cursor in (word, phrase, definition_term, metadata_shaped_body_text):
        assert not cursor.isNull()
        assert cursor.charFormat().isAnchor()
        assert cursor.charFormat().anchorHref().startswith("#barsky-tts-")

    click_cursor = QTextCursor(document)
    click_cursor.setPosition(phrase.selectionStart() + 1)
    item.text_widget.show()
    app.processEvents()
    QTest.mouseClick(
        item.text_widget.viewport(),
        Qt.MouseButton.LeftButton,
        pos=item.text_widget.cursorRect(click_cursor).center(),
    )
    app.processEvents()

    assert mock.spoken == [("tightening the noose", item.tts_btn)]

    del item
    app.processEvents()


def test_bold_text_outside_sentence_review_is_not_repurposed_for_tts():
    """Click-to-speak remains scoped to sentence-based review cards."""
    from kgb_srs.catalog import DatabaseType
    from kgb_srs.graphics import FlashCardItem

    app = QApplication.instance() or QApplication([])
    mock = _MockApp()
    mock._db_type = DatabaseType.KNOWLEDGE
    item = FlashCardItem(mock, 200, 150, 350, 200)
    item.set_text("A **bold reference**", is_flipped=False)

    bold_reference = item.text_widget.document().find("bold reference")

    assert not bold_reference.isNull()
    assert not bold_reference.charFormat().isAnchor()

    del item
    app.processEvents()


def test_word_phrase_single_meaning_is_unindented_and_uses_review_meaning_token(
    flashcard,
):
    """A single word/phrase meaning is flush and uses its non-grade token."""
    from kgb_srs.senses import Sense, build_word_phrase_back_from_senses
    from kgb_srs.ui_theme import LIGHT_TOKENS

    sense = Sense(
        id=1,
        expression="bolt",
        meaning="a metal fastener",
        expression_norm="bolt",
        meaning_norm="a metal fastener",
    )
    back = build_word_phrase_back_from_senses(
        [sense], {sense.id: ["A bolt secures the door."]}
    )
    flashcard.set_text(f"**bolt**\n\n---\n\n{back}", is_flipped=True)

    blocks = {}
    block = flashcard.text_widget.document().begin()
    while block.isValid():
        blocks[block.text().strip()] = block
        block = block.next()

    meaning_block = blocks["a metal fastener"]
    example_block = blocks["A bolt secures the door."]
    assert meaning_block.textList() is None
    assert meaning_block.blockFormat().leftMargin() == 0
    meaning_cursor = flashcard.text_widget.document().find("a metal fastener")
    meaning_color = meaning_cursor.charFormat().foreground().color().name().upper()
    assert meaning_color == LIGHT_TOKENS["review_meaning"]
    assert meaning_color != LIGHT_TOKENS["danger"]
    assert (
        example_block.blockFormat().leftMargin()
        > meaning_block.blockFormat().leftMargin()
    )


def test_word_phrase_example_is_italic_without_losing_surface_bold(flashcard):
    """Example text is italic while its highlighted learning target stays bold."""
    from PyQt6.QtGui import QFont
    from kgb_srs.senses import Sense, build_word_phrase_back_from_senses

    sense = Sense(
        id=1,
        expression="bolt",
        meaning="a metal fastener",
        expression_norm="bolt",
        meaning_norm="a metal fastener",
    )
    back = build_word_phrase_back_from_senses(
        [sense], {sense.id: ["A bolt secures the door."]}
    )
    flashcard.set_text(f"**bolt**\n\n---\n\n{back}", is_flipped=True)

    document = flashcard.text_widget.document()
    example_cursor = document.find("A bolt secures")

    assert not example_cursor.isNull()
    assert example_cursor.charFormat().fontItalic()

    target_cursor = document.find("bolt", example_cursor.selectionStart())
    assert not target_cursor.isNull()
    assert target_cursor.charFormat().fontItalic()
    assert target_cursor.charFormat().fontWeight() >= QFont.Weight.Bold.value


def test_word_phrase_examples_keep_the_same_indent_for_each_sense(flashcard):
    """Manual multi-sense labels leave meanings flush and examples aligned."""
    from kgb_srs.senses import Sense, build_word_phrase_back_from_senses

    senses = [
        Sense(
            id=1,
            expression="bank",
            meaning="a financial institution",
            expression_norm="bank",
            meaning_norm="a financial institution",
        ),
        Sense(
            id=2,
            expression="bank",
            meaning="the side of a river",
            expression_norm="bank",
            meaning_norm="the side of a river",
        ),
    ]
    back = build_word_phrase_back_from_senses(
        senses,
        {
            senses[0].id: ["I visited the bank."],
            senses[1].id: ["The bank slopes steeply."],
        },
    )
    flashcard.set_text(f"**bank**\n\n---\n\n{back}", is_flipped=True)

    blocks = {}
    block = flashcard.text_widget.document().begin()
    while block.isValid():
        blocks[block.text().strip()] = block
        block = block.next()

    first_meaning = blocks["1. a financial institution"]
    second_meaning = blocks["2. the side of a river"]
    first_example = blocks["I visited the bank."]
    second_example = blocks["The bank slopes steeply."]
    first_example_margin = first_example.blockFormat().leftMargin()
    second_example_margin = second_example.blockFormat().leftMargin()

    assert first_meaning.textList() is None
    assert second_meaning.textList() is None
    assert first_meaning.blockFormat().leftMargin() == 0
    assert second_meaning.blockFormat().leftMargin() == 0
    assert first_example_margin > first_meaning.blockFormat().leftMargin()
    assert second_example_margin > second_meaning.blockFormat().leftMargin()
    assert first_example_margin == second_example_margin


def test_review_card_uses_proxy_safe_text_renderer_when_webengine_is_available(
    monkeypatch,
):
    """An optional WebEngine install must not blank a proxy-embedded card."""
    from PyQt6.QtWidgets import QTextBrowser
    from kgb_srs import graphics as graphics_mod
    from kgb_srs.graphics import FlashCardItem

    class WebEngineMustNotBeEmbedded:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError(
                "QWebEngineView must not be embedded in QGraphicsProxyWidget"
            )

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(graphics_mod, "HAS_WEBENGINE", True)
    monkeypatch.setattr(graphics_mod, "QWebEngineView", WebEngineMustNotBeEmbedded)

    item = FlashCardItem(_MockApp(), 200, 150, 350, 200)
    item.set_text("Visible **review content**", is_flipped=False)

    assert isinstance(item.text_widget, QTextBrowser)
    assert item.text_widget.isReadOnly()
    assert item.text_widget.openLinks() is False
    assert item.text_widget.openExternalLinks() is False
    assert item.text_widget.toPlainText().strip() == "Visible review content"

    del item
    app.processEvents()


def test_math_placeholder_collision_preserves_literal_and_math():
    from kgb_srs.markdown_utils import (
        MATH_PLACEHOLDER_PREFIX,
        markdown_to_html_fragment,
        markdown_to_plain_text,
    )

    source = f"{MATH_PLACEHOLDER_PREFIX}0PLACEHOLDER plus $x$"
    html = markdown_to_html_fragment(source)

    assert f"{MATH_PLACEHOLDER_PREFIX}0PLACEHOLDER" in html
    assert "$x$" in html
    assert markdown_to_plain_text(source) == (
        f"{MATH_PLACEHOLDER_PREFIX}0PLACEHOLDER plus x"
    )


def test_webengine_background_failure_is_logged_and_nonfatal(caplog):
    from kgb_srs.graphics import _set_transparent_web_view_background

    class _Page:
        def setBackgroundColor(self, _color):
            raise RuntimeError("background unavailable")

    class _View:
        def page(self):
            return _Page()

    with caplog.at_level(logging.WARNING, logger="kgb_srs.graphics"):
        assert _set_transparent_web_view_background(_View()) is None

    assert "background unavailable" in caplog.text


def test_review_html_embeds_owned_token_css_with_the_content_font():
    """Review HTML must embed the safe document seam rather than local CSS."""
    from kgb_srs.markdown_utils import build_review_html
    from kgb_srs.ui_theme import LIGHT_TOKENS, font_css, review_document_css

    document = build_review_html(
        "Safe **review content**", "Noto Serif", 23, include_mathjax=False
    )

    assert review_document_css("Noto Serif", 23) in document
    assert font_css("Noto Serif", 23) in document
    assert "font-family: 'Arial'" not in document
    assert "font-size: 14px" not in document
    assert LIGHT_TOKENS["review_meaning"] in document
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
        assert legacy_color not in document.lower()


def test_sanitize_review_html_allows_only_legacy_meaning_marker_as_fixed_token():
    """Only the exact compatibility marker may retain a tokenized color."""
    from kgb_srs.markdown_utils import sanitize_review_html_fragment
    from kgb_srs.ui_theme import LIGHT_TOKENS

    fragment = sanitize_review_html_fragment(
        '<span style="color: #D32F2F">meaning</span>'
        '<span style="color: #3949AB">primary</span>'
        '<span style="color: #B42318">danger</span>'
        '<span style="color: #123456">arbitrary</span>'
    )

    assert fragment == (
        f'<span style="color: {LIGHT_TOKENS["review_meaning"]};">meaning</span>'
        "<span>primary</span><span>danger</span><span>arbitrary</span>"
    )
    assert LIGHT_TOKENS["danger"] not in fragment


def test_review_html_is_offline_and_sanitizes_resource_markup():
    from kgb_srs.markdown_utils import (
        build_review_html,
        sanitize_review_html_fragment,
    )

    fragment = sanitize_review_html_fragment(
        '<p><a href="https://example.test/path">safe</a>'
        '<a href="javascript:alert(1)">bad</a>'
        '<img src="file:///etc/passwd" onerror="alert(1)">'
        '<script src="https://cdn.example.test/x.js"></script></p>'
    )
    document = build_review_html("Math: $x^2$", "Arial", 18, include_mathjax=True)

    assert 'href="https://example.test/path"' in fragment
    assert "javascript:" not in fragment
    assert "file:///etc/passwd" not in fragment
    assert "onerror" not in fragment
    assert "<script" not in fragment.lower()
    assert "cdn.jsdelivr.net" not in document
    assert "<script" not in document.lower()
    assert "$x^2$" in document  # safe, visible offline math fallback


def test_review_html_preserves_only_safe_emphasis_inline_styles():
    """Markdown emphasis survives while unrelated CSS remains stripped."""
    from kgb_srs.markdown_utils import sanitize_review_html_fragment

    fragment = sanitize_review_html_fragment(
        '<span style="color: red; font-weight: 700; font-style: italic; '
        'background: url(https://bad.test/style)">target</span>'
    )

    assert fragment == (
        '<span style="font-weight: 700; font-style: italic;">target</span>'
    )


@pytest.mark.parametrize(
    "url, allowed",
    [
        ("https://example.test/a", True),
        ("http://example.test/a", True),
        ("file:///etc/passwd", False),
        ("data:text/html,hello", False),
        ("javascript:alert(1)", False),
        ("qrc:/internal", False),
        ("mailto:test@example.test", False),
        ("ftp://example.test/a", False),
        ("/relative/path", False),
    ],
)
def test_review_card_navigation_policy_routes_only_http_links(url, allowed):
    from kgb_srs.graphics import ReviewCardNavigationPolicy, route_review_card_link

    opened = []
    result = route_review_card_link(
        url, lambda qurl: opened.append(qurl.toString()) or True
    )

    assert ReviewCardNavigationPolicy.should_open_externally(url) is allowed
    assert ReviewCardNavigationPolicy.allows_embedded_navigation(url) is False
    assert result is allowed
    assert opened == ([url] if allowed else [])


def test_review_web_view_disables_local_content_access():
    from kgb_srs import graphics

    if graphics.QWebEngineSettings is None:
        pytest.skip("PyQt WebEngine is not installed")

    class Settings:
        def __init__(self):
            self.attributes = {}

        def setAttribute(self, attribute, value):
            self.attributes[attribute] = value

    class View:
        def __init__(self):
            self.web_settings = Settings()

        def settings(self):
            return self.web_settings

    view = View()
    graphics.configure_review_web_view(view)
    attrs = graphics.QWebEngineSettings.WebAttribute

    assert view.web_settings.attributes[attrs.LocalContentCanAccessRemoteUrls] is False
    assert view.web_settings.attributes[attrs.LocalContentCanAccessFileUrls] is False
    assert view.web_settings.attributes[attrs.JavascriptEnabled] is False
