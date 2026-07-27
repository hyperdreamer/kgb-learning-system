"""Integrated acceptance coverage for the light-only PyQt design system."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PyQt6.QtGui import QColor, QPalette, QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

from kgb_srs.browse_dialog import BrowseCardsDialog
from kgb_srs.catalog import DatabaseType, write_database_type
from kgb_srs.config import (
    CANONICAL_DB_SUBDIRS,
    DEFAULT_SETTINGS,
    get_database_root,
)
from kgb_srs.database_creation_dialog import DBCreationDialog
from kgb_srs.dialogs import DynamicInputDialog
from kgb_srs.main_window import BarskyApp
from kgb_srs.schema import ensure_sentence_schema, init_db
from kgb_srs.sentence_card_dialog import SentenceCardDialog
from kgb_srs.settings_dialog import SettingsDialog
from kgb_srs.ui_theme import LIGHT_TOKENS, ROLE_PROPERTY
from kgb_srs.word_phrase_dialog import WordPhraseCardDialog


@pytest.fixture(scope="session")
def qapp():
    """Provide the real headless QApplication shared by acceptance tests."""
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _keep_acceptance_tests_modal_and_network_safe(monkeypatch):
    """Keep real widgets non-modal without starting external worker activity."""
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(SettingsDialog, "_start_voice_worker", lambda self: None)


@pytest.fixture(autouse=True)
def _dispose_top_level_widgets():
    """Defer-delete top-level roots so their QSS cannot leak between tests."""
    yield
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _temporary_settings_file(
    tmp_path: Path,
    *,
    database_root: Path,
    ui_font_size: int = 14,
    content_font_size: int = 18,
) -> Path:
    """Write an explicit temporary config without touching user settings."""
    settings = dict(DEFAULT_SETTINGS)
    settings["ai_providers"] = {
        name: dict(profile)
        for name, profile in DEFAULT_SETTINGS["ai_providers"].items()
    }
    settings.update(
        {
            "database_root": str(database_root),
            "default_database": "",
            "font_family": "Arial",
            "font_size": ui_font_size,
            "content_font_family": "Courier New",
            "content_font_size": content_font_size,
        }
    )
    settings_file = tmp_path / "acceptance-settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    return settings_file


def _new_window(
    tmp_path: Path,
    *,
    ui_font_size: int = 14,
    content_font_size: int = 18,
) -> tuple[BarskyApp, Path, Path]:
    """Create a window bound only to a test-owned config and database root."""
    database_root = tmp_path / "database-root"
    settings_file = _temporary_settings_file(
        tmp_path,
        database_root=database_root,
        ui_font_size=ui_font_size,
        content_font_size=content_font_size,
    )
    return BarskyApp(settings_file=str(settings_file)), database_root, settings_file


def _database_path(root: Path, database_type: DatabaseType, name: str) -> Path:
    subdir = {
        DatabaseType.LANGUAGE_SENTENCE: (
            "Language-based",
            "Sentence-based",
        ),
        DatabaseType.LANGUAGE_WORD_PHRASE: (
            "Language-based",
            "Word-Phrase-based",
        ),
        DatabaseType.KNOWLEDGE: ("Knowledge-based",),
    }[database_type]
    return root.joinpath(*subdir, f"{name}.db")


def _create_database(
    root: Path,
    database_type: DatabaseType,
    name: str,
    cards: list[tuple[str, str, int]] | None = None,
) -> Path:
    """Create a deterministic, due-card test database under *root*."""
    path = _database_path(root, database_type, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = init_db(str(path))
    try:
        write_database_type(connection, database_type)
        if database_type == DatabaseType.LANGUAGE_SENTENCE:
            ensure_sentence_schema(connection)

        due_date = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        for front, back, box in cards or [
            ("first card", "first answer", 1),
            ("second card", "second answer", 1),
            ("third card", "third answer", 1),
            ("fourth card", "fourth answer", 1),
        ]:
            connection.execute(
                "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
                (front, back, box, due_date),
            )
        connection.execute(
            "UPDATE settings SET value = '0' WHERE key = 'random_review'"
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _load_database(
    window: BarskyApp,
    path: Path,
    database_type: DatabaseType,
) -> None:
    """Use the real load path and ensure its typed database became active."""
    window.load_database(
        silent=True,
        db_path=str(path),
        display=path.stem,
        offer_projection_adoption=False,
    )
    assert window.conn is not None
    assert window._db_type == database_type


def _show_window(window: BarskyApp, qapp) -> None:
    """Give the real graphics view a viewport-sized scene before review tests."""
    window.resize(900, 700)
    window.show()
    qapp.processEvents()
    window.redraw_canvas()
    qapp.processEvents()


def _dispose_widget(widget: QWidget) -> None:
    """Close and defer-delete a test-owned root or dialog deterministically."""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _font_size(widget: QWidget) -> int:
    """Return a logical point size or QSS-resolved pixel size."""
    font = widget.font()
    return font.pointSize() if font.pointSize() > 0 else font.pixelSize()


def _button_with_text(root: QWidget, text: str) -> QPushButton:
    matches = [
        button
        for button in root.findChildren(QPushButton)
        if button.text().strip() == text
    ]
    assert len(matches) == 1, f"expected one {text!r} button, found {len(matches)}"
    return matches[0]


def _button_frame_width(button: QPushButton) -> int:
    option = QStyleOptionButton()
    option.initFrom(button)
    return button.style().pixelMetric(
        QStyle.PixelMetric.PM_DefaultFrameWidth,
        option,
        button,
    )


def _assert_visible_focus(
    qapp,
    focused_button: QPushButton,
    alternate_button: QPushButton,
) -> None:
    """Assert the installed QSS makes a focused button visibly thicker."""
    alternate_button.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    unfocused_width = _button_frame_width(focused_button)

    focused_button.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()

    assert focused_button.hasFocus()
    assert _button_frame_width(focused_button) > unfocused_width


def _assert_light_surface(
    qapp,
    root: QWidget,
    focus_button: QPushButton,
    alternate_button: QPushButton,
    *,
    background_token: str,
) -> None:
    """Verify an actual root has the light palette and focus behavior."""
    palette = root.palette()
    assert root.styleSheet()
    assert palette.color(QPalette.ColorRole.Window) == QColor(
        LIGHT_TOKENS[background_token]
    )
    assert palette.color(QPalette.ColorRole.WindowText) == QColor(LIGHT_TOKENS["text"])
    _assert_visible_focus(qapp, focus_button, alternate_button)


def _assert_unrevealed_geometry(window: BarskyApp) -> None:
    assert window.current_card is not None
    assert window.is_current_flipped is False
    assert window._grade_gesture_regions == {}
    assert window._review_card_bottom == pytest.approx(
        window.scene.sceneRect().bottom()
    )


def _assert_revealed_geometry(window: BarskyApp) -> None:
    scene_rect = window.scene.sceneRect()
    regions = window._grade_gesture_regions
    assert window.current_card is not None
    assert window.is_current_flipped is True
    assert set(regions) == {"incorrect", "correct"}
    assert all(
        scene_rect.contains(region) and not region.isEmpty()
        for region in regions.values()
    )
    lane_top = min(region.top() for region in regions.values())
    assert window._review_card_bottom == pytest.approx(lane_top - 20)
    assert window.card_ui.sceneBoundingRect().bottom() <= window._review_card_bottom + 1


def _assert_empty_geometry(window: BarskyApp) -> None:
    assert window.current_card is None
    assert window._grade_gesture_regions == {}
    assert window._review_card_bottom == pytest.approx(
        window.scene.sceneRect().bottom()
    )


def _select_card_id(dialog: BrowseCardsDialog, card_id: int) -> None:
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, 0)
        if item is not None and int(item.text()) == card_id:
            dialog.table.selectRow(row)
            return
    raise AssertionError(f"browse dialog did not list card {card_id}")


def _assert_no_persistent_grade_target(window: BarskyApp) -> None:
    """Only the movable card and its proxy may be scene items during review."""
    card = window.card_ui
    assert card is not None
    assert all(item is card or item is card.proxy for item in window.scene.items())


def _relative_luminance(color: str) -> float:
    values = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linearize(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(value) for value in values)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(first: str, second: str) -> float:
    low, high = sorted((_relative_luminance(first), _relative_luminance(second)))
    return (high + 0.05) / (low + 0.05)


def test_major_surfaces_share_light_palette_roles_and_visible_focus(tmp_path, qapp):
    """A missing root installation would leave one major surface unthemed."""
    window, database_root, _settings_file = _new_window(tmp_path)
    dialogs: list[QWidget] = []
    try:
        sentence_path = _create_database(
            database_root,
            DatabaseType.LANGUAGE_SENTENCE,
            "surface-coverage",
        )
        _load_database(window, sentence_path, DatabaseType.LANGUAGE_SENTENCE)
        _show_window(window, qapp)
        window.start_review()
        qapp.processEvents()
        card = window.card_ui
        assert card is not None

        _assert_light_surface(
            qapp,
            window,
            window.start_btn,
            window.db_btn,
            background_token="canvas",
        )
        _assert_light_surface(
            qapp,
            card.container,
            card.flip_btn,
            card.tts_btn,
            background_token="surface",
        )

        browse = BrowseCardsDialog(window)
        settings = SettingsDialog(window.settings, window)
        sentence = SentenceCardDialog(
            window,
            sentence="I study a sentence.",
            items=[("study", "learn")],
            settings=window.settings,
        )
        word_phrase = WordPhraseCardDialog(
            window,
            front="study",
            settings=window.settings,
        )
        database_creation = DBCreationDialog(window, base_dir=str(database_root))
        dynamic_input = DynamicInputDialog(window)
        dialogs = [
            browse,
            settings,
            sentence,
            word_phrase,
            database_creation,
            dynamic_input,
        ]
        for dialog, focus_button, alternate_button in (
            (browse, browse.review_btn, browse.edit_btn),
            (settings, settings.save_button, settings.cancel_button),
            (sentence, sentence._save_btn, sentence._cancel_btn),
            (word_phrase, word_phrase._save_btn, word_phrase._cancel_btn),
            (
                database_creation,
                _button_with_text(database_creation, "Create"),
                _button_with_text(database_creation, "Cancel"),
            ),
            (
                dynamic_input,
                _button_with_text(dynamic_input, "OK"),
                _button_with_text(dynamic_input, "Cancel"),
            ),
        ):
            dialog.show()
            qapp.processEvents()
            _assert_light_surface(
                qapp,
                dialog,
                focus_button,
                alternate_button,
                background_token="canvas",
            )
            dialog.hide()

        assert window.start_btn.property(ROLE_PROPERTY) == "primary"
        assert card.flip_btn.property(ROLE_PROPERTY) == "primary"
        assert browse.review_btn.property(ROLE_PROPERTY) == "primary"
        assert settings.save_button.property(ROLE_PROPERTY) == "primary"
        assert sentence._save_btn.property(ROLE_PROPERTY) == "primary"
        assert word_phrase._save_btn.property(ROLE_PROPERTY) == "primary"
        assert (
            _button_with_text(database_creation, "Create").property(ROLE_PROPERTY)
            == "primary"
        )
        assert (
            _button_with_text(dynamic_input, "OK").property(ROLE_PROPERTY) == "primary"
        )
    finally:
        for dialog in dialogs:
            _dispose_widget(dialog)
        _dispose_widget(window)


def test_daily_and_browse_review_context_follow_the_all_daily_formula(tmp_path, qapp):
    """A selected-card review must retain daily context rather than invent a mode."""
    window, database_root, _settings_file = _new_window(tmp_path)
    try:
        sentence_path = _create_database(
            database_root,
            DatabaseType.LANGUAGE_SENTENCE,
            "review-context",
            cards=[
                ("one", "first", 1),
                ("two", "second", 1),
                ("three", "third", 1),
            ],
        )
        _load_database(window, sentence_path, DatabaseType.LANGUAGE_SENTENCE)
        _show_window(window, qapp)

        assert window.review_status_label.isHidden()
        window.start_review()
        assert window.review_mode == "daily"
        assert window.review_status_label.isVisible()
        assert window.review_status_label.text() == "Reviewed 0 · Remaining 3"

        window.flip_card()
        window.card_ui.correct_btn.click()
        assert window.review_status_label.text() == "Reviewed 1 · Remaining 2"

        selected_id = window.cards_due[-1][0]
        browse = BrowseCardsDialog(window)
        try:
            _select_card_id(browse, selected_id)
            browse.review_selected()
        finally:
            _dispose_widget(browse)

        assert window.review_mode == "daily"
        assert window.current_card[0] == selected_id
        assert window.review_status_label.isVisible()
        assert window.review_status_label.text() == "Reviewed 0 · Remaining 1"

        window.close_review()
        assert window.review_status_label.isHidden()
    finally:
        _dispose_widget(window)


def test_review_affordances_keep_explicit_grades_shortcuts_and_nonvisual_drag(
    tmp_path, qapp
):
    """Grading must be explicit after reveal while drag remains nonvisual only."""
    window, database_root, _settings_file = _new_window(tmp_path)
    try:
        sentence_path = _create_database(
            database_root,
            DatabaseType.LANGUAGE_SENTENCE,
            "review-affordances",
        )
        _load_database(window, sentence_path, DatabaseType.LANGUAGE_SENTENCE)
        _show_window(window, qapp)
        window.start_review()

        first_id = window.current_card[0]
        first_box = window.conn.execute(
            "SELECT box FROM cards WHERE id = ?", (first_id,)
        ).fetchone()[0]
        card = window.card_ui
        assert card is not None
        assert not card.tts_btn.isHidden()
        assert not card.flip_btn.isHidden()
        assert card.flip_btn.property(ROLE_PROPERTY) == "primary"
        assert card.btn_layout.stretch(card.btn_layout.indexOf(card.flip_btn)) == 1
        assert card.incorrect_btn.isHidden()
        assert card.correct_btn.isHidden()
        assert window._grade_gesture_regions == {}
        _assert_no_persistent_grade_target(window)

        # Hidden post-reveal actions and grade shortcuts retain the authoritative guard.
        card.correct_btn.click()
        window._shortcut_incorrect()
        assert window.current_card[0] == first_id
        assert (
            window.conn.execute(
                "SELECT box FROM cards WHERE id = ?", (first_id,)
            ).fetchone()[0]
            == first_box
        )

        # Use real shortcut objects rather than invoking a synthetic key binding.
        from PyQt6.QtGui import QKeySequence, QShortcut

        shortcut_keys = {
            shortcut.key().toString() for shortcut in window.findChildren(QShortcut)
        }
        assert {
            QKeySequence("Alt+Left").toString(),
            QKeySequence("Alt+1").toString(),
            QKeySequence("Alt+Right").toString(),
            QKeySequence("Alt+2").toString(),
        } <= shortcut_keys

        window.flip_card()
        card = window.card_ui
        _assert_revealed_geometry(window)
        _assert_no_persistent_grade_target(window)
        for button, role, first_shortcut, second_shortcut in (
            (card.incorrect_btn, "danger", "Alt+Left", "Alt+1"),
            (card.correct_btn, "success", "Alt+Right", "Alt+2"),
        ):
            assert not button.isHidden()
            assert button.property(ROLE_PROPERTY) == role
            assert button.text().casefold() in button.accessibleName().casefold()
            assert first_shortcut in button.toolTip()
            assert second_shortcut in button.toolTip()
            assert first_shortcut in button.accessibleDescription()
            assert second_shortcut in button.accessibleDescription()

        card.correct_btn.click()
        assert (
            window.conn.execute(
                "SELECT box FROM cards WHERE id = ?", (first_id,)
            ).fetchone()[0]
            == first_box + 1
        )
        _assert_unrevealed_geometry(window)

        second_id = window.current_card[0]
        window.conn.execute("UPDATE cards SET box = 4 WHERE id = ?", (second_id,))
        window.conn.commit()
        window.flip_card()
        window._shortcut_incorrect()
        assert (
            window.conn.execute(
                "SELECT box FROM cards WHERE id = ?", (second_id,)
            ).fetchone()[0]
            == 3
        )
        _assert_unrevealed_geometry(window)

        third_id = window.current_card[0]
        third_box = window.conn.execute(
            "SELECT box FROM cards WHERE id = ?", (third_id,)
        ).fetchone()[0]
        window.flip_card()
        card = window.card_ui
        correct_region = window._grade_gesture_regions["correct"]
        card.setPos(QPointF(correct_region.center()))
        window.check_card_drop(card)
        QTest.qWait(10)
        assert window.conn.execute(
            "SELECT box FROM cards WHERE id = ?", (third_id,)
        ).fetchone()[0] == min(third_box + 1, 5)
        assert window._daily_review_history[-1].transition == "graded"
        _assert_unrevealed_geometry(window)
    finally:
        _dispose_widget(window)


def test_review_geometry_lifecycle_clears_or_rebuilds_on_every_major_transition(
    tmp_path,
    qapp,
):
    """Stale revealed geometry would make a later review card accidentally gradable."""
    window, database_root, _settings_file = _new_window(tmp_path)
    try:
        sentence_path = _create_database(
            database_root,
            DatabaseType.LANGUAGE_SENTENCE,
            "geometry-lifecycle",
        )
        _load_database(window, sentence_path, DatabaseType.LANGUAGE_SENTENCE)
        _show_window(window, qapp)
        window.start_review()
        _assert_unrevealed_geometry(window)

        window.flip_card()
        _assert_revealed_geometry(window)
        window.process_answer(True)
        _assert_unrevealed_geometry(window)

        previous_id = window.current_card[0]
        window.flip_card()
        window._advance_daily_queue()
        _assert_unrevealed_geometry(window)
        window._previous_daily_card()
        assert window.current_card[0] == previous_id
        _assert_unrevealed_geometry(window)

        selected_id = window.cards_due[-1][0]
        browse = BrowseCardsDialog(window)
        try:
            _select_card_id(browse, selected_id)
            browse.review_selected()
        finally:
            _dispose_widget(browse)
        assert window.current_card[0] == selected_id
        _assert_unrevealed_geometry(window)

        window.flip_card()
        window._restart_daily_review()
        _assert_unrevealed_geometry(window)

        window.flip_card()
        window.close_review()
        _assert_empty_geometry(window)
        window.start_review()
        _assert_unrevealed_geometry(window)

        window.resize(1050, 760)
        qapp.processEvents()
        _assert_unrevealed_geometry(window)

        window.flip_card()
        old_regions = window._grade_gesture_regions
        window.resize(760, 520)
        qapp.processEvents()
        _assert_revealed_geometry(window)
        assert window._grade_gesture_regions is not old_regions

        window._reset_review_session()
        _assert_empty_geometry(window)

        window._clear_database_state()
        assert window.conn is None
        _assert_empty_geometry(window)

        replacement_path = _create_database(
            database_root,
            DatabaseType.KNOWLEDGE,
            "replacement",
            cards=[("replacement", "answer", 1)],
        )
        replacement_connection = init_db(str(replacement_path))
        window._adopt_database(
            replacement_connection,
            str(replacement_path),
            "replacement",
            DatabaseType.KNOWLEDGE,
            False,
            False,
        )
        assert window.conn is replacement_connection
        _assert_empty_geometry(window)
    finally:
        _dispose_widget(window)


def test_semantic_actions_have_distinct_live_states_and_accessible_contrast(
    tmp_path,
    qapp,
):
    """Role loss would collapse correct, incorrect, disabled, or focus feedback."""
    for foreground in ("text", "primary", "success", "danger"):
        assert _contrast_ratio(LIGHT_TOKENS[foreground], LIGHT_TOKENS["surface"]) >= 4.5
    assert _contrast_ratio(LIGHT_TOKENS["focus"], LIGHT_TOKENS["canvas"]) >= 3
    assert _contrast_ratio(LIGHT_TOKENS["focus"], LIGHT_TOKENS["surface"]) >= 3

    window, database_root, _settings_file = _new_window(tmp_path)
    try:
        sentence_path = _create_database(
            database_root,
            DatabaseType.LANGUAGE_SENTENCE,
            "semantic-states",
        )
        _load_database(window, sentence_path, DatabaseType.LANGUAGE_SENTENCE)
        _show_window(window, qapp)

        assert not window.delete_entry_btn.isEnabled()
        assert window.delete_entry_btn.property(ROLE_PROPERTY) == "danger"
        assert window.delete_entry_btn.palette().color(
            QPalette.ColorRole.Button
        ) == QColor(LIGHT_TOKENS["disabled_surface"])
        assert window.delete_entry_btn.palette().color(
            QPalette.ColorRole.ButtonText
        ) == QColor(LIGHT_TOKENS["disabled_text"])

        window.start_review()
        window.flip_card()
        card = window.card_ui
        assert card.correct_btn.property(ROLE_PROPERTY) == "success"
        assert card.incorrect_btn.property(ROLE_PROPERTY) == "danger"
        assert window.delete_entry_btn.isEnabled()
        assert card.correct_btn.palette().color(QPalette.ColorRole.Button) == QColor(
            LIGHT_TOKENS["success"]
        )
        assert card.incorrect_btn.palette().color(QPalette.ColorRole.Button) == QColor(
            LIGHT_TOKENS["danger"]
        )
        assert window.delete_entry_btn.palette().color(
            QPalette.ColorRole.Button
        ) == QColor(LIGHT_TOKENS["danger"])

        QTest.mouseMove(card.correct_btn, card.correct_btn.rect().center())
        qapp.processEvents()
        assert card.correct_btn.underMouse()
        QTest.mousePress(
            card.correct_btn,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            card.correct_btn.rect().center(),
        )
        assert card.correct_btn.isDown()
        QTest.mouseRelease(
            card.correct_btn,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            card.correct_btn.rect().center(),
        )
        _assert_unrevealed_geometry(window)
        _assert_visible_focus(qapp, window.start_btn, window.db_btn)
    finally:
        _dispose_widget(window)


def test_sentence_knowledge_and_word_phrase_modes_preserve_controls_and_browse(
    tmp_path,
    qapp,
):
    """A presentation migration must not make the derived dictionary writable."""
    window, database_root, _settings_file = _new_window(tmp_path)
    try:
        database_paths = {
            database_type: _create_database(
                database_root,
                database_type,
                database_type.value.replace(" ", "-").lower(),
                cards=[("mode card", "mode answer", 1)],
            )
            for database_type in (
                DatabaseType.LANGUAGE_SENTENCE,
                DatabaseType.KNOWLEDGE,
                DatabaseType.LANGUAGE_WORD_PHRASE,
            )
        }
        _show_window(window, qapp)

        for database_type, path in database_paths.items():
            _load_database(window, path, database_type)
            dialog = BrowseCardsDialog(window)
            try:
                assert dialog.table.rowCount() == 1
                _select_card_id(dialog, 1)
                assert dialog.review_btn.isEnabled()
                if database_type == DatabaseType.LANGUAGE_WORD_PHRASE:
                    assert not dialog.edit_btn.isEnabled()
                    assert not dialog.delete_btn.isEnabled()
                else:
                    assert dialog.edit_btn.isEnabled()
                    assert dialog.delete_btn.isEnabled()

                dialog.review_selected()
            finally:
                _dispose_widget(dialog)

            assert window.review_mode == "daily"
            assert window.current_card is not None
            if database_type == DatabaseType.LANGUAGE_WORD_PHRASE:
                assert window.add_entry_btn.isHidden()
                assert not window.add_entry_btn.isEnabled()
                assert window.delete_entry_btn.isHidden()
                assert not window.delete_entry_btn.isEnabled()
            else:
                assert not window.add_entry_btn.isHidden()
                assert window.add_entry_btn.isEnabled()
                assert not window.delete_entry_btn.isHidden()
                assert window.delete_entry_btn.isEnabled()
    finally:
        _dispose_widget(window)


@pytest.mark.parametrize(
    ("ui_font_size", "content_font_size"),
    [(8, 8), (36, 48)],
)
def test_font_endpoints_keep_ui_chrome_and_review_content_separate(
    tmp_path,
    qapp,
    ui_font_size,
    content_font_size,
):
    """Changing content size must not leak into chrome or dialog typography."""
    window, database_root, _settings_file = _new_window(
        tmp_path,
        ui_font_size=ui_font_size,
        content_font_size=content_font_size,
    )
    dialog = None
    try:
        sentence_path = _create_database(
            database_root,
            DatabaseType.LANGUAGE_SENTENCE,
            "font-endpoints",
            cards=[("font front", "font back", 1)],
        )
        _load_database(window, sentence_path, DatabaseType.LANGUAGE_SENTENCE)
        _show_window(window, qapp)
        window.start_review()
        card = window.card_ui
        dialog = SentenceCardDialog(
            window,
            sentence="A font test.",
            items=[("font", "typeface")],
            settings=window.settings,
        )
        dialog.show()
        qapp.processEvents()

        assert _font_size(window) == ui_font_size
        assert _font_size(card.container) == ui_font_size
        assert _font_size(dialog) == ui_font_size

        cursor = QTextCursor(card.text_widget.document())
        cursor.setPosition(0)
        character_font = cursor.charFormat().font()
        assert "courier new" in {
            family.casefold() for family in cursor.charFormat().fontFamilies()
        }
        assert character_font.pixelSize() == content_font_size
        assert (
            character_font.family().casefold()
            != card.container.font().family().casefold()
        )
        if content_font_size != ui_font_size:
            assert character_font.pixelSize() != _font_size(window)
    finally:
        if dialog is not None:
            _dispose_widget(dialog)
        _dispose_widget(window)


def test_settings_expose_font_endpoints_without_dark_or_classic_appearance(
    tmp_path, qapp
):
    """The light-only release must not stage a hidden alternate appearance."""
    window, _database_root, _settings_file = _new_window(tmp_path)
    dialog = None
    try:
        _show_window(window, qapp)
        dialog = SettingsDialog(window.settings, window)
        dialog.show()
        qapp.processEvents()

        assert (dialog.font_size_input.minimum(), dialog.font_size_input.maximum()) == (
            8,
            36,
        )
        assert (
            dialog.content_font_size_input.minimum(),
            dialog.content_font_size_input.maximum(),
        ) == (8, 48)

        exposed_text = []
        for button_type in (QPushButton, QCheckBox, QRadioButton):
            exposed_text.extend(
                button.text() for button in dialog.findChildren(button_type)
            )
        category_list = dialog.findChild(QListWidget, "settingsCategoryList")
        assert category_list is not None
        exposed_text.extend(
            category_list.item(index).text() for index in range(category_list.count())
        )
        for combo in dialog.findChildren(QComboBox):
            if combo.objectName() not in {
                "fontFamilyInput",
                "contentFontFamilyInput",
            }:
                exposed_text.extend(
                    combo.itemText(index) for index in range(combo.count())
                )
        exposed_text.extend(
            line_edit.text() for line_edit in dialog.findChildren(QLineEdit)
        )
        assert not any(
            term in text.casefold()
            for text in exposed_text
            for term in ("dark", "classic")
        )

        staged = dialog._collect_staged_settings()
        assert not any(
            term in key.casefold()
            for key in staged
            for term in ("theme", "classic", "dark")
        )
    finally:
        if dialog is not None:
            _dispose_widget(dialog)
        _dispose_widget(window)


def test_temporary_config_startup_stays_inside_its_empty_database_root(tmp_path):
    """Startup must not open a repository or user database when default is empty."""
    database_root = tmp_path / "isolated-root"
    settings_file = _temporary_settings_file(
        tmp_path,
        database_root=database_root,
    )
    window = BarskyApp(settings_file=str(settings_file))
    try:
        assert Path(window.settings_file).resolve() == settings_file.resolve()
        assert (
            Path(get_database_root(window.settings)).resolve()
            == database_root.resolve()
        )
        assert window.settings["default_database"] == ""
        assert window.current_db_path is None
        assert window.conn is None
        assert all(
            (database_root / relative).is_dir() for relative in CANONICAL_DB_SUBDIRS
        )
    finally:
        _dispose_widget(window)
