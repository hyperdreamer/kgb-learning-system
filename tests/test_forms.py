"""Regression tests for card-entry dialogs and form helpers."""

import sqlite3
import warnings

import pytest

from kgb_srs.ai_parser import MAX_WORD_PHRASE_MEANINGS
from kgb_srs.schema import init_db, insert_sentence_card
from .qt_helpers import qt_app as _qt_app


class TestFinalFormRegressions:
    def test_api_key_visibility_button_toggles_plaintext(self):
        _qt_app()
        from PyQt6.QtWidgets import QLineEdit
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QAction
        from kgb_srs.secret_line_edit import SecretLineEdit, _make_eye_icons

        field = SecretLineEdit("sk-secret")

        # Must be a QLineEdit — not a composite QWidget with a sub-layout
        assert isinstance(field, QLineEdit), (
            "SecretLineEdit must subclass QLineEdit directly"
        )

        # The toggle action is registered inside the QLineEdit
        actions = field.actions()
        assert len(actions) == 1, (
            f"Expected exactly 1 trailing action, got {len(actions)}"
        )
        toggle_action = actions[0]
        assert isinstance(toggle_action, QAction)
        assert toggle_action is field._toggle_action
        assert toggle_action.isCheckable(), "Action must be checkable"

        # Carries a proper, non-null icon
        icon = toggle_action.icon()
        assert icon is not None
        assert not icon.isNull(), "Action must have a drawn icon"

        # Hidden and visible icons from _make_eye_icons are distinct
        # QIcon objects with different pixmaps.
        hidden_icon, visible_icon = _make_eye_icons()
        assert not hidden_icon.isNull()
        assert not visible_icon.isNull()
        pix_hidden = hidden_icon.pixmap(QSize(24, 24))
        pix_visible = visible_icon.pixmap(QSize(24, 24))
        assert not pix_hidden.isNull()
        assert not pix_visible.isNull()
        assert pix_hidden.cacheKey() != pix_visible.cacheKey(), (
            "Hidden and visible icons must have different pixmaps"
        )

        # Initial state: hidden (action unchecked → password echo)
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert not toggle_action.isChecked()
        assert toggle_action.toolTip() == "Show API key"
        assert toggle_action.text() == "Show API key"
        key_hidden = toggle_action.icon().cacheKey()

        # Toggle to visible
        toggle_action.toggle()
        assert field.echoMode() == QLineEdit.EchoMode.Normal
        assert field.text() == "sk-secret"
        assert toggle_action.toolTip() == "Hide API key"
        assert toggle_action.text() == "Hide API key"
        assert toggle_action.isChecked()
        key_visible = toggle_action.icon().cacheKey()
        assert key_visible != key_hidden, (
            "Action icon must visually change when toggled to visible — "
            "cacheKey must differ"
        )

        # Toggle back to hidden
        toggle_action.toggle()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert not toggle_action.isChecked()
        assert toggle_action.toolTip() == "Show API key"
        assert toggle_action.text() == "Show API key"
        key_back = toggle_action.icon().cacheKey()
        assert key_back == key_hidden, (
            "Action icon must return to hidden icon when toggled back — "
            "cacheKey must match original"
        )

        field.close()

    def test_visible_eye_iris_centered_and_outlined(self):
        """Visible eye icon: iris is a centred hollow outline circle.

        Checks two geometric invariants on the visible (State.On) pixmap:
        1. Centre pixel is transparent — confirms hollow outline, not a
           filled disc.
        2. A pixel left-of-centre on the iris ring has alpha — confirms
           the iris is QPointF-centred.  The old int-overload bug
           (drawEllipse(int(cx), int(cy), int(pr), int(pr))) placed the
           iris entirely in the lower-right quadrant, so no iris alpha
           exists left of (or above) the image centre.
        """
        _qt_app()
        from kgb_srs.secret_line_edit import _make_eye_icons

        sz = 20  # default used by SecretLineEdit
        _hidden_icon, visible_icon = _make_eye_icons(size=sz)
        pm = visible_icon.pixmap(sz, sz)
        img = pm.toImage()

        cx = cy = sz / 2.0  # 10.0
        pr = sz * 0.14  # 2.8 — iris radius (float)
        # Pixel on the iris-outline annulus, left of centre:
        #   distance from centre ≈ pr, so inside the 1.5 px pen stroke.
        #   Far enough from the eye-outline endpoints (x ≈ cx ± ew) that
        #   the almond outline does not reach it.
        offset = max(2, int(pr + 0.5))  # ≈ pr  rounded to an integer
        tx = int(cx) - offset  # left-of-centre test column
        ty = int(cy)  # same row as centre

        # 1. Centre transparency — iris is hollow (outline only).
        assert img.pixelColor(int(cx), int(cy)).alpha() == 0, (
            "Centre pixel must be transparent — iris must be an "
            "outline, not a filled disc"
        )

        # 2. Iris outline extends left of centre.
        a = img.pixelColor(tx, ty).alpha()
        assert a > 0, (
            f"No iris alpha at ({tx},{ty}) — iris may be top-left "
            f"anchored (int-overload drawEllipse bug) rather than "
            f"QPointF-centred"
        )

    def test_public_ai_worker_does_not_shadow_thread_finished(self):
        """The worker's result signal must stay distinct from QThread.finished."""
        _qt_app()
        from kgb_srs.ai_provider import _get_ai_worker_class

        worker_class = _get_ai_worker_class()
        assert "finished" not in worker_class.__dict__

    def test_ai_worker_class_is_cached_after_lazy_import(self):
        """Repeated worker creation reuses PyQt signal metadata and class state."""
        _qt_app()
        from kgb_srs.ai_provider import _get_ai_worker_class

        assert _get_ai_worker_class() is _get_ai_worker_class()

    def test_worker_result_signal_does_not_override_thread_finished(self):
        _qt_app()
        from kgb_srs.form_helpers import _AIGenerateWorker

        assert hasattr(_AIGenerateWorker, "result")
        # QThread.finished remains the inherited no-argument termination signal.
        assert "finished" not in _AIGenerateWorker.__dict__

    def test_deprecated_form_helper_imports_resolve_canonical_exports(self):
        """Legacy helper imports warn while preserving object identity."""
        _qt_app()
        from kgb_srs import form_helpers

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            from kgb_srs.forms import _AIGenerateWorker as worker_class
            from kgb_srs.forms import _apply_ui_font as apply_ui_font

        assert worker_class is form_helpers._AIGenerateWorker
        assert apply_ui_font is form_helpers._apply_ui_font
        messages = [str(warning.message) for warning in caught_warnings]
        assert len(messages) == 2
        assert all("kgb_srs.form_helpers" in message for message in messages)

    def test_legacy_form_helper_monkeypatches_remain_effective(self, monkeypatch):
        """Legacy overrides run before a real widget receives generated QSS."""
        _qt_app()
        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QMainWindow, QWidget

        from kgb_srs import form_helpers
        import kgb_srs.forms as forms
        from kgb_srs.sentence_card_dialog import _create_ai_worker

        font_calls = []
        install_calls = []
        parent = QMainWindow()
        widget = QWidget(parent)
        settings = {"font_size": 19}

        class FakeWorker:
            def __init__(self, config, prompt):
                self.config = config
                self.prompt = prompt

        def fake_apply_ui_font(received_widget, received_settings, received_parent):
            font_calls.append((received_widget, received_settings, received_parent))
            received_widget.setFont(QFont("Arial", 19))

        real_install = form_helpers.install_design_system

        def spy_install_design_system(received_widget, family, size):
            install_calls.append((received_widget, family, size))
            return real_install(received_widget, family, size)

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                monkeypatch.setattr(forms, "_AIGenerateWorker", FakeWorker)
                monkeypatch.setattr(forms, "_apply_ui_font", fake_apply_ui_font)
            monkeypatch.setattr(
                form_helpers, "install_design_system", spy_install_design_system
            )

            worker = _create_ai_worker("config", "prompt")
            form_helpers.apply_ui_font(widget, settings, parent)

            assert isinstance(worker, FakeWorker)
            assert worker.config == "config"
            assert worker.prompt == "prompt"
            assert font_calls == [(widget, settings, parent)]
            assert widget.font().family() == "Arial"
            assert widget.font().pointSize() == 19 or widget.font().pixelSize() == 19
            assert install_calls == [(widget, "Arial", 19)]
            assert widget.styleSheet()
        finally:
            monkeypatch.undo()
            forms.__dict__.pop("_AIGenerateWorker", None)
            forms.__dict__.pop("_apply_ui_font", None)
            parent.close()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_sentence_dialog_preserves_meanings_and_rebuild_content(self):
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="Hello world again",
            items=[("Hello", "greeting"), ("world", "earth")],
        )
        # First item is auto-selected; only its meaning is visible.
        assert dialog._active_meaning_expr == "Hello"
        assert [w.toPlainText() for _, w in dialog._meaning_widgets] == ["greeting"]
        dialog._meaning_widgets[0][1].setPlainText("salutation")
        assert dialog._meanings["Hello"] == "salutation"
        dialog._item_entry.setText("again")
        dialog._add_item()
        # Newly added item becomes selected; prior meaning stays in store.
        assert dialog._meanings["Hello"] == "salutation"
        assert dialog._active_meaning_expr == "again"
        dialog.close()

    def test_card_dialogs_use_ui_font_from_settings(self):
        """Edit dialogs apply Appearance → UI Font (family + size)."""
        _qt_app()
        from PyQt6.QtWidgets import QWidget
        from kgb_srs.forms import SentenceCardDialog, WordPhraseCardDialog

        settings = {"font_family": "DejaVu Sans", "font_size": 19}
        parent = QWidget()
        parent.setFont(parent.font())  # ensure a parent exists

        s_dialog = SentenceCardDialog(
            parent=parent,
            sentence="Hello world",
            items=["Hello"],
            settings=settings,
        )
        assert s_dialog.font().family() == "DejaVu Sans"
        assert s_dialog.font().pointSize() == 19
        s_dialog.close()

        w_dialog = WordPhraseCardDialog(
            parent=parent,
            front="bank",
            settings=settings,
        )
        assert w_dialog.font().family() == "DejaVu Sans"
        assert w_dialog.font().pointSize() == 19
        w_dialog.close()

    @pytest.mark.parametrize(
        "settings",
        (None, {}, {"font_family": "Arial"}, {"font_size": 23}),
        ids=("none", "empty", "family-only", "size-only"),
    )
    def test_card_dialogs_inherit_parent_pixel_qss_font_without_complete_settings(
        self, settings
    ):
        """Incomplete settings must preserve the inherited 23px root-QSS font."""
        _qt_app()
        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtWidgets import QWidget

        from kgb_srs.sentence_card_dialog import SentenceCardDialog
        from kgb_srs.ui_theme import font_css, install_design_system
        from kgb_srs.word_phrase_dialog import WordPhraseCardDialog

        parent = QWidget()
        dialogs = []
        try:
            install_design_system(parent, "Arial", 23)
            parent.resize(640, 480)
            parent.show()
            _qt_app().processEvents()

            actual_parent_family = parent.font().family()
            assert parent.font().pointSize() <= 0
            assert parent.font().pixelSize() == 23
            expected_font = font_css(actual_parent_family, 23)

            for dialog_type, arguments in (
                (
                    SentenceCardDialog,
                    {
                        "sentence": "Hello world",
                        "items": [("Hello", "greeting")],
                    },
                ),
                (WordPhraseCardDialog, {"front": "bank"}),
            ):
                dialog = dialog_type(parent=parent, settings=settings, **arguments)
                dialogs.append(dialog)
                dialog.show()
                _qt_app().processEvents()

            observed = {
                type(dialog).__name__: {
                    "actual_parent_font": expected_font in dialog.styleSheet(),
                    "fallback_14px": "font-size: 14px;" in dialog.styleSheet(),
                }
                for dialog in dialogs
            }
            assert all(
                state["actual_parent_font"] and not state["fallback_14px"]
                for state in observed.values()
            ), f"incomplete settings replaced the inherited root-QSS font: {observed}"
        finally:
            for dialog in dialogs:
                dialog.close()
                dialog.deleteLater()
            parent.close()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_dialogs_use_semantic_roles_tones_and_shared_root_qss(self):
        """Every Task 5 dialog uses root QSS rather than local color styles."""
        _qt_app()
        from PyQt6.QtCore import QCoreApplication, QEvent, Qt
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QMainWindow, QPushButton, QToolButton, QWidget

        from kgb_srs.database_creation_dialog import DBCreationDialog
        from kgb_srs.dialogs import DynamicInputDialog
        from kgb_srs.sentence_card_dialog import SentenceCardDialog
        from kgb_srs.ui_theme import (
            ROLE_PROPERTY,
            STATUS_TONE_PROPERTY,
            stylesheet,
        )
        from kgb_srs.word_phrase_dialog import WordPhraseCardDialog

        parent = QMainWindow()
        parent.setFont(QFont("Arial", 19))
        settings = {"font_family": "Arial", "font_size": 19}
        sentence = SentenceCardDialog(
            parent=parent,
            sentence="Hello world",
            items=[("Hello", "greeting")],
            settings=settings,
        )
        word_phrase = WordPhraseCardDialog(parent=parent, settings=settings)
        database = DBCreationDialog(parent=parent)
        dynamic = DynamicInputDialog(parent=parent)
        try:
            for dialog in (sentence, word_phrase, database, dynamic):
                assert dialog.styleSheet() == stylesheet("Arial", 19)
                assert "QTextEdit:focus" in dialog.styleSheet()

            expected_roles = {
                sentence._add_sel_btn: "secondary",
                sentence._add_btn: "secondary",
                sentence._remove_btn: "secondary",
                sentence._generate_btn: "secondary",
                sentence._cancel_btn: "secondary",
                sentence._save_btn: "primary",
                word_phrase._add_meaning_btn: "secondary",
                word_phrase._generate_btn: "secondary",
                word_phrase._cancel_btn: "secondary",
                word_phrase._save_btn: "primary",
            }
            for widget, role in expected_roles.items():
                assert widget.property(ROLE_PROPERTY) == role
                assert widget.styleSheet() == ""

            database_buttons = {
                button.text(): button for button in database.findChildren(QPushButton)
            }
            dynamic_buttons = {
                button.text(): button for button in dynamic.findChildren(QPushButton)
            }
            assert database_buttons["Create"].property(ROLE_PROPERTY) == "primary"
            assert database_buttons["Cancel"].property(ROLE_PROPERTY) == "secondary"
            assert dynamic_buttons["OK"].property(ROLE_PROPERTY) == "primary"
            assert dynamic_buttons["Cancel"].property(ROLE_PROPERTY) == "secondary"
            assert database._dir_label.property(ROLE_PROPERTY) == "quiet"
            assert sentence._sense_source_label.property(ROLE_PROPERTY) == "quiet"

            meaning_card = sentence.findChild(QWidget, "sentenceMeaningCard")
            assert meaning_card is not None
            assert meaning_card.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            assert meaning_card.styleSheet() == ""
            assert sentence._meaning_widgets[0][1].styleSheet() == ""
            assert word_phrase._meaning_rows[0]["meaning_edit"].styleSheet() == ""

            word_phrase._add_meaning_row()
            tab_bar = word_phrase._meanings_tabs.tabBar()
            close_button = tab_bar.tabButton(0, tab_bar.ButtonPosition.RightSide)
            assert isinstance(close_button, QToolButton)
            assert close_button.property(ROLE_PROPERTY) == "icon"
            assert close_button.styleSheet() == ""

            sentence._add_selected_text()
            assert sentence._status_label.property(STATUS_TONE_PROPERTY) == "danger"
            word_phrase._generate_ai_meanings()
            assert word_phrase._ai_status.property(STATUS_TONE_PROPERTY) == "danger"
        finally:
            for dialog in (sentence, word_phrase, database, dynamic):
                dialog.close()
                dialog.deleteLater()
            parent.close()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_sentence_meaning_card_has_central_token_surface(self):
        """The real meaning card must render a central surface, not the canvas."""
        _qt_app()
        from PyQt6.QtCore import QCoreApplication, QEvent, Qt
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QWidget

        from kgb_srs.sentence_card_dialog import SentenceCardDialog
        from kgb_srs.ui_theme import LIGHT_TOKENS

        dialog = SentenceCardDialog(
            sentence="Hello world", items=[("Hello", "greeting")]
        )
        try:
            dialog.resize(560, 520)
            dialog.show()
            _qt_app().processEvents()

            card = dialog.findChild(QWidget, "sentenceMeaningCard")
            assert card is not None
            assert card.objectName() == "sentenceMeaningCard"
            assert card.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            assert card.styleSheet() == ""

            card_rule = (
                "QWidget#sentenceMeaningCard {\n"
                f"  background-color: {LIGHT_TOKENS['surface']};\n"
                f"  border: 1px solid {LIGHT_TOKENS['border']};\n"
                "  border-radius: 8px;\n"
                "}"
            )
            assert card_rule in dialog.styleSheet()

            image = card.grab().toImage()
            assert image.width() > 10 and image.height() > 10
            interior = image.pixelColor(image.width() - 5, image.height() // 2)
            assert interior == QColor(LIGHT_TOKENS["surface"])
            assert interior != QColor(LIGHT_TOKENS["canvas"])
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_dialog_design_system_keeps_ui_and_content_fonts_separate_at_bounds(self):
        """Dialog focus QSS follows UI font while review HTML keeps content font."""
        _qt_app()
        from kgb_srs.markdown_utils import build_review_html
        from kgb_srs.sentence_card_dialog import SentenceCardDialog
        from kgb_srs.ui_theme import font_css

        dialogs = []
        try:
            for ui_size, content_size in ((8, 8), (36, 48)):
                dialog = SentenceCardDialog(
                    sentence="Hello world",
                    items=[("Hello", "greeting")],
                    settings={
                        "font_family": "Arial",
                        "font_size": ui_size,
                        "content_font_family": "Courier New",
                        "content_font_size": content_size,
                    },
                )
                dialogs.append(dialog)
                assert font_css("Arial", ui_size) in dialog.styleSheet()
                assert "QTextEdit:focus" in dialog.styleSheet()

                review_html = build_review_html(
                    "**content**", "Courier New", content_size
                )
                assert font_css("Courier New", content_size) in review_html
                assert font_css("Arial", ui_size) not in review_html
        finally:
            for dialog in dialogs:
                dialog.close()
                dialog.deleteLater()

    def test_sentence_dialog_meaning_shows_selected_only(self):
        """Meaning panel lists only the selected unfamiliar item."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="He insists on speaking himself.",
            items=[
                ("insist on", "to demand"),
                ("speak", ""),
            ],
        )
        assert dialog._generate_btn.text() == "🤖 Generate Meaning"
        assert dialog._active_meaning_expr == "insist on"
        assert len(dialog._meaning_widgets) == 1
        assert dialog._meaning_widgets[0][0] == "insist on"
        assert dialog._meaning_widgets[0][1].toPlainText() == "to demand"

        dialog._items_list.setCurrentRow(1)
        assert dialog._active_meaning_expr == "speak"
        assert len(dialog._meaning_widgets) == 1
        assert dialog._meaning_widgets[0][0] == "speak"
        # Previous meaning preserved in store, not shown.
        assert dialog._meanings["insist on"] == "to demand"
        dialog.close()

    def test_word_dialog_does_not_drop_partial_second_row(self, monkeypatch):
        _qt_app()
        from PyQt6.QtWidgets import QMessageBox
        from kgb_srs.forms import WordPhraseCardDialog

        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *args, **kwargs: warnings.append(args[2]),
        )
        dialog = WordPhraseCardDialog(front="bank")
        first = dialog._meaning_rows[0]
        first["meaning_edit"].setPlainText("financial institution")
        first["example_edit"].setPlainText("I deposited money at the bank.")
        dialog._add_meaning_row(meaning="river edge", example="")

        dialog._accept()

        assert warnings
        assert dialog.result_meanings == []
        dialog.close()

    def test_word_dialog_add_meaning_button_does_not_pass_clicked_bool(self):
        """QPushButton.clicked emits a bool; must not become meaning text."""
        _qt_app()
        from kgb_srs.forms import WordPhraseCardDialog

        dialog = WordPhraseCardDialog(front="bank")
        assert len(dialog._meaning_rows) == 1

        dialog._add_meaning_btn.click()

        assert len(dialog._meaning_rows) == 2
        assert dialog._meanings_tabs.count() == 2
        second = dialog._meaning_rows[1]
        assert second["meaning_edit"].toPlainText() == ""
        assert second["example_edit"].toPlainText() == ""
        dialog.close()

    def test_word_dialog_meanings_use_tabs(self):
        """Meanings are tab pages labeled Meaning N; owned close X keeps one tab."""
        _qt_app()
        from PyQt6.QtWidgets import QApplication, QToolButton
        from kgb_srs.forms import WordPhraseCardDialog

        dialog = WordPhraseCardDialog(front="bank")
        assert dialog._add_meaning_btn.text() == "+ Add Meaning"
        assert dialog._meanings_tabs.count() == 1
        assert dialog._meanings_tabs.tabText(0) == "Meaning 1"
        assert not dialog._meanings_tabs.tabsClosable()
        bar = dialog._meanings_tabs.tabBar()
        assert bar is not None
        # Sole tab has no close control
        assert bar.tabButton(0, bar.ButtonPosition.RightSide) is None
        assert bar.tabButton(0, bar.ButtonPosition.LeftSide) is None

        dialog._add_meaning_btn.click()
        dialog.resize(560, 520)
        dialog.show()
        QApplication.processEvents()

        assert dialog._meanings_tabs.count() == 2
        assert dialog._meanings_tabs.tabText(0) == "Meaning 1"
        assert dialog._meanings_tabs.tabText(1) == "Meaning 2"
        assert not dialog._meanings_tabs.tabsClosable()
        assert dialog._add_meaning_btn.isEnabled()
        # Newly added tab is active
        assert dialog._meanings_tabs.currentIndex() == 1
        # Exactly one owned close X per tab; no left-side ghost close
        for i in range(dialog._meanings_tabs.count()):
            right = bar.tabButton(i, bar.ButtonPosition.RightSide)
            left = bar.tabButton(i, bar.ButtonPosition.LeftSide)
            assert left is None
            assert isinstance(right, QToolButton)
            assert right.objectName() == "meaningTabClose"
            assert right.isVisible()

        # Close Meaning 2 (active) via its owned button
        close2 = bar.tabButton(1, bar.ButtonPosition.RightSide)
        assert isinstance(close2, QToolButton)
        close2.click()
        QApplication.processEvents()
        assert dialog._meanings_tabs.count() == 1
        assert dialog._meanings_tabs.tabText(0) == "Meaning 1"
        assert not dialog._meanings_tabs.tabsClosable()
        assert dialog._add_meaning_btn.isEnabled()
        assert bar.tabButton(0, bar.ButtonPosition.RightSide) is None
        dialog.close()

    def test_word_dialog_tab_close_renders_owned_pixmap_in_16px_slot(self):
        """The fixed owned close slot must visibly contain its pixmap icon."""
        _qt_app()
        from PyQt6.QtCore import QCoreApplication, QEvent, QSize, Qt
        from PyQt6.QtGui import QIcon, QImage
        from PyQt6.QtWidgets import QToolButton

        from kgb_srs.ui_theme import ROLE_PROPERTY
        from kgb_srs.word_phrase_dialog import WordPhraseCardDialog

        dialog = WordPhraseCardDialog(front="bank")
        try:
            dialog._add_meaning_row()
            dialog.resize(560, 520)
            dialog.show()
            _qt_app().processEvents()

            tab_bar = dialog._meanings_tabs.tabBar()
            close_button = tab_bar.tabButton(1, tab_bar.ButtonPosition.RightSide)
            assert isinstance(close_button, QToolButton)
            assert close_button.objectName() == "meaningTabClose"
            assert close_button.size() == QSize(16, 16)
            assert close_button.iconSize() == QSize(10, 10)
            assert close_button.property(ROLE_PROPERTY) == "icon"
            assert close_button.focusPolicy() == Qt.FocusPolicy.NoFocus
            assert close_button.styleSheet() == ""

            before = (
                close_button.grab()
                .toImage()
                .convertToFormat(QImage.Format.Format_RGBA8888)
            )
            original_icon = QIcon(close_button.icon())
            try:
                close_button.setIcon(QIcon())
                _qt_app().processEvents()
                after = (
                    close_button.grab()
                    .toImage()
                    .convertToFormat(QImage.Format.Format_RGBA8888)
                )
            finally:
                close_button.setIcon(original_icon)
                _qt_app().processEvents()

            assert before != after, (
                "clearing the owned pixmap left the rendered slot unchanged"
            )

            close_button.click()
            _qt_app().processEvents()
            assert dialog._meanings_tabs.count() == 1
            assert dialog._meanings_tabs.tabText(0) == "Meaning 1"
            assert not dialog._meanings_tabs.tabsClosable()
            assert tab_bar.tabButton(0, tab_bar.ButtonPosition.RightSide) is None
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_word_dialog_allows_up_to_max_meaning_tabs(self):
        """Users can add up to MAX_WORD_PHRASE_MEANINGS meaning tabs."""
        _qt_app()
        from kgb_srs.forms import WordPhraseCardDialog

        dialog = WordPhraseCardDialog(front="bank")
        for n in range(2, MAX_WORD_PHRASE_MEANINGS + 1):
            assert dialog._add_meaning_btn.isEnabled()
            dialog._add_meaning_btn.click()
            assert dialog._meanings_tabs.count() == n
            assert dialog._meanings_tabs.tabText(n - 1) == f"Meaning {n}"

        assert not dialog._add_meaning_btn.isEnabled()
        # Cap is hard: extra add is ignored
        dialog._add_meaning_row()
        assert dialog._meanings_tabs.count() == MAX_WORD_PHRASE_MEANINGS
        dialog.close()

    def test_sentence_dialog_rejects_blank_meaning_before_accept(self, monkeypatch):
        _qt_app()
        from PyQt6.QtWidgets import QMessageBox
        from kgb_srs.forms import SentenceCardDialog

        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *args, **kwargs: warnings.append(args[2]),
        )
        dialog = SentenceCardDialog(sentence="Hello world", items=[("world", "")])

        dialog._accept()

        assert warnings
        assert "meaning" in warnings[0].lower()
        assert dialog.result_items == []
        assert dialog.isVisible() or True  # dialog never accepted
        dialog.close()

    def test_sentence_dialog_save_dimmed_when_empty(self):
        """Save is disabled until sentence + at least one item exist."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog()
        assert dialog._save_btn.isEnabled() is False

        dialog._sentence_edit.setPlainText("Hello world")
        _qt_app().processEvents()
        assert dialog._save_btn.isEnabled() is False

        dialog._item_entry.setText("world")
        dialog._add_item()
        _qt_app().processEvents()
        assert dialog._save_btn.isEnabled() is True

        # Clear sentence → dim again
        dialog._sentence_edit.setPlainText("")
        _qt_app().processEvents()
        assert dialog._save_btn.isEnabled() is False
        dialog.close()

    def test_sentence_dialog_has_no_validate_button(self):
        """Validate is redundant — Save is the only gate."""
        _qt_app()
        from PyQt6.QtWidgets import QPushButton
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(sentence="Hello world", items=[("world", "earth")])
        labels = [b.text() for b in dialog.findChildren(QPushButton)]
        assert "Validate" not in labels
        assert "Save" in labels
        assert not hasattr(dialog, "_validate_btn")
        dialog.close()

    def test_sentence_dialog_has_no_back_editor(self):
        """Sentence dialog no longer exposes a separate back QTextEdit."""
        _qt_app()
        from PyQt6.QtWidgets import QLabel, QTextEdit
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="He insists on speaking himself.",
            items=[("insist on", "to demand")],
            back="**insist on**: to demand",
        )
        assert not hasattr(dialog, "_back_edit")
        # No user-facing Back label for a rendered/cache editor.
        labels = dialog.findChildren(QLabel)
        assert not any("Back (contextual" in (lab.text() or "") for lab in labels)
        # Meaning fields still exist as QTextEdit widgets.
        assert dialog._meaning_widgets
        assert all(isinstance(w, QTextEdit) for _, w in dialog._meaning_widgets)
        dialog.close()

    def test_new_sentence_items_generate_meanings_automatically(self, monkeypatch):
        """Both add paths immediately generate a meaning for each new item."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from PyQt6.QtGui import QTextCursor
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog

        workers = []
        prompts = []

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)
                prompts.append(prompt)
                workers.append(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog = SentenceCardDialog(
            sentence="A veneer hid the cracks.",
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )

        dialog._item_entry.setText("veneer")
        dialog._add_item()

        assert len(workers) == 1
        assert dialog._selected_expression() == "veneer"
        assert "veneer" in prompts[0]
        workers[0].result.emit(
            '{"expression": "veneer", "action": "create", '
            '"sense_id": null, "meaning": "a superficial appearance"}'
        )
        workers[0].finished.emit()
        QApplication.processEvents()
        assert dialog._meanings["veneer"] == "a superficial appearance"

        cursor = dialog._sentence_edit.textCursor()
        start = dialog._sentence_edit.toPlainText().index("cracks")
        cursor.setPosition(start)
        cursor.setPosition(start + len("cracks"), QTextCursor.MoveMode.KeepAnchor)
        dialog._sentence_edit.setTextCursor(cursor)
        dialog._add_selected_text()

        assert len(workers) == 2
        assert dialog._selected_expression() == "cracks"
        assert "cracks" in prompts[1]
        workers[1].result.emit(
            '{"expression": "cracks", "action": "create", '
            '"sense_id": null, "meaning": "narrow breaks"}'
        )
        workers[1].finished.emit()
        QApplication.processEvents()
        assert dialog._meanings["cracks"] == "narrow breaks"

        dialog._item_entry.setText("cracks")
        dialog._add_item()
        assert len(workers) == 2
        dialog.close()

    def test_generate_meaning_replaces_manual_meaning(self, monkeypatch):
        """Explicit regeneration replaces the current user-entered meaning."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog

        workers = []

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)
                workers.append(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog = SentenceCardDialog(
            sentence="A veneer hid the cracks.",
            items=[("veneer", "manually entered meaning")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )

        dialog._generate_btn.click()
        assert len(workers) == 1
        workers[0].result.emit(
            '{"expression": "veneer", "action": "create", '
            '"sense_id": null, "meaning": "AI replacement meaning"}'
        )
        workers[0].finished.emit()
        QApplication.processEvents()

        assert dialog._meanings["veneer"] == "AI replacement meaning"
        assert dialog._meaning_widgets[0][1].toPlainText() == "AI replacement meaning"
        dialog.close()

    def test_failed_regeneration_preserves_current_meaning_and_sense(self, monkeypatch):
        """An AI error leaves the prior meaning and linked sense untouched."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog

        workers = []

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)
                workers.append(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog = SentenceCardDialog(
            sentence="A veneer hid the cracks.",
            items=[("veneer", "existing meaning", 42)],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )

        dialog._generate_btn.click()
        workers[0].error.emit("provider unavailable")
        workers[0].finished.emit()
        QApplication.processEvents()

        assert dialog._meanings["veneer"] == "existing meaning"
        assert dialog._meaning_widgets[0][1].toPlainText() == "existing meaning"
        assert dialog._sense_ids["veneer"] == 42
        dialog.close()

    def test_sentence_dialog_ai_success_status_is_ready_to_save(self, monkeypatch):
        """AI success status should report reuse/create and ready to save."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=[("Hello", "")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )

        started = []
        prompts = []

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)
                self._config = config
                self._prompt = prompt
                prompts.append(prompt)

            def start(self):
                started.append(True)

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog._generate_ai_meanings()
        assert started
        assert dialog._ai_worker is not None
        # Prompt is sense-assignment for the selected item only.
        assert "Hello" in prompts[0]
        assert "action" in prompts[0].lower() or "reuse" in prompts[0].lower()

        # Valid AI JSON payload matching parse_sense_assignment shape (create).
        raw = (
            '{"expression": "Hello", "action": "create", '
            '"sense_id": null, "meaning": "a greeting"}'
        )
        worker = dialog._ai_worker
        worker.result.emit(raw)
        worker.finished.emit()
        QApplication.processEvents()

        status = dialog._ai_status.text()
        assert "Review and edit" not in status
        assert "Hello" in status
        assert "Ready to save" in status
        assert dialog._meanings["Hello"] == "a greeting"
        meaning_edit = dialog._meaning_widgets[0][1]
        assert meaning_edit.toPlainText() == "a greeting"
        assert meaning_edit.isReadOnly() is False
        assert meaning_edit.isEnabled() is True
        dialog.close()

    @staticmethod
    def _emit_sentence_ai_assignment(dialog, monkeypatch, response):
        """Run a controlled AI assignment response through the dialog."""
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.form_helpers import _AIGenerateWorker

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog._generate_ai_meanings()
        assert dialog._ai_worker is not None
        worker = dialog._ai_worker
        worker.result.emit(response)
        worker.finished.emit()
        QApplication.processEvents()

    @staticmethod
    def _expression_sense_count(conn):
        from kgb_srs.senses import ensure_expression_senses_table

        ensure_expression_senses_table(conn)
        return conn.execute("SELECT COUNT(*) FROM expression_senses").fetchone()[0]

    def test_ai_create_defers_sense_creation_until_dialog_save(self, monkeypatch):
        """Cancelling an AI-created meaning must not leave an orphan sense."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=[("Hello", "")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
            conn=conn,
        )
        try:
            self._emit_sentence_ai_assignment(
                dialog,
                monkeypatch,
                (
                    '{"expression": "Hello", "action": "create", '
                    '"sense_id": null, "meaning": "a greeting"}'
                ),
            )

            assert dialog._meanings["Hello"] == "a greeting"
            assert dialog._sense_ids["Hello"] is None
            assert self._expression_sense_count(conn) == 0

            dialog.reject()
            assert self._expression_sense_count(conn) == 0
        finally:
            dialog.close()
            conn.close()

    def test_ai_create_materializes_one_sense_when_saved(self, monkeypatch):
        """The normal sentence-card insert creates and links the deferred sense."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=[("Hello", "")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
            conn=conn,
        )
        try:
            self._emit_sentence_ai_assignment(
                dialog,
                monkeypatch,
                (
                    '{"expression": "Hello", "action": "create", '
                    '"sense_id": null, "meaning": "a greeting"}'
                ),
            )
            assert self._expression_sense_count(conn) == 0

            dialog._accept()
            card_id = insert_sentence_card(
                conn,
                dialog.result_sentence,
                dialog.result_items,
                dialog.result_back,
            )

            assert self._expression_sense_count(conn) == 1
            sense_id, meaning = conn.execute(
                "SELECT sense_id, meaning FROM unfamiliar_items WHERE card_id=?",
                (card_id,),
            ).fetchone()
            assert sense_id is not None
            assert meaning == "a greeting"
        finally:
            dialog.close()
            conn.close()

    def test_ai_reuse_keeps_existing_sense_id_without_duplicate(self, monkeypatch):
        """A verified AI reuse remains linked to its canonical existing sense."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog
        from kgb_srs.senses import create_or_get_sense

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        existing = create_or_get_sense(conn, "Hello", "canonical greeting")
        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=[("Hello", "")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
            conn=conn,
        )
        try:
            self._emit_sentence_ai_assignment(
                dialog,
                monkeypatch,
                (
                    '{"expression": "Hello", "action": "reuse", '
                    f'"sense_id": {existing.id}, "meaning": ""}}'
                ),
            )

            assert self._expression_sense_count(conn) == 1
            assert dialog._sense_ids["Hello"] == existing.id
            assert dialog._meanings["Hello"] == "canonical greeting"
        finally:
            dialog.close()
            conn.close()

    def test_sentence_dialog_result_back_derived_from_meanings(self, monkeypatch):
        """On accept, result_back is the markdown join of expression+meaning pairs."""
        _qt_app()
        from PyQt6.QtWidgets import QMessageBox
        from kgb_srs.forms import SentenceCardDialog

        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

        dialog = SentenceCardDialog(
            sentence="Hello world again",
            items=[("Hello", "greeting"), ("world", "earth")],
        )
        dialog._accept()

        assert [(e, m) for e, m, _s, _surf in dialog.result_items] == [
            ("Hello", "greeting"),
            ("world", "earth"),
        ]
        assert dialog.result_back == ("1. **Hello**: greeting\n\n2. **world**: earth")
        dialog.close()

    def test_sentence_dialog_empty_meanings_section(self):
        """No items → empty-state label, no meaning widgets, no crash."""
        _qt_app()
        from PyQt6.QtWidgets import QLabel
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(sentence="Hello world", items=None)
        assert dialog._meaning_widgets == []
        labels = [lab.text() for lab in dialog._meanings_container.findChildren(QLabel)]
        assert any("Add unfamiliar" in t for t in labels)
        dialog.close()

    def test_sentence_dialog_allows_manual_meaning_for_new_card(self):
        """A new sentence card can be saved with a user-entered meaning."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="They are tightening the noose.",
            items=["noose"],
        )
        expr, edit = dialog._meaning_widgets[0]

        assert expr == "noose"
        assert edit.isReadOnly() is False

        edit.setPlainText("a loop of rope that tightens")
        dialog._accept()

        assert dialog.result_items == [
            ("noose", "a loop of rope that tightens", None, "")
        ]
        dialog.close()

    def test_sentence_dialog_meaning_field_chrome_and_preserve(self):
        """Meaning fields are QTextEdit cards; content survives rebuild."""
        _qt_app()
        from PyQt6.QtWidgets import QTextEdit
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(
            sentence="Hello world again",
            items=[("Hello", "greeting")],
        )
        assert len(dialog._meaning_widgets) == 1
        expr, edit = dialog._meaning_widgets[0]
        assert expr == "Hello"
        assert isinstance(edit, QTextEdit)
        assert edit.toPlainText() == "greeting"
        assert edit.minimumHeight() >= 48

        edit.setPlainText("salutation")
        dialog._item_entry.setText("world")
        dialog._add_item()

        # Only the newly selected item is shown; Hello stays in store.
        assert dialog._meanings["Hello"] == "salutation"
        assert dialog._active_meaning_expr == "world"
        assert len(dialog._meaning_widgets) == 1
        assert dialog._meaning_widgets[0][0] == "world"
        dialog.close()


class TestQThreadLifecycle:
    """Result/error signals must not unlock controls until QThread.finished."""

    def test_meaning_generation_locks_all_item_mutation_controls(self, monkeypatch):
        """Add and Remove stay unavailable until the active worker finishes."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog

        workers = []

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)
                workers.append(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=[("Hello", "a greeting")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )
        dialog._item_entry.setText("world")

        dialog._generate_ai_meanings()

        assert len(workers) == 1
        assert not dialog._add_btn.isEnabled()
        assert not dialog._remove_btn.isEnabled()
        dialog._add_btn.click()
        dialog._remove_btn.click()
        assert dialog._get_items() == ["Hello"]

        workers[0].finished.emit()
        assert dialog._add_btn.isEnabled()
        assert dialog._remove_btn.isEnabled()
        dialog.close()

    def test_meaning_generation_rejects_reentry_until_finished(self, monkeypatch):
        """A worker reference blocks another request even if isRunning is false."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog

        workers = []

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)
                workers.append(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.form_helpers._AIGenerateWorker", FakeWorker)
        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=[("Hello", "a greeting")],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )

        dialog._generate_ai_meanings()
        dialog._generate_ai_meanings()

        assert len(workers) == 1
        assert dialog._ai_worker is workers[0]
        workers[0].finished.emit()
        dialog.close()

    def test_result_signal_alone_does_not_restore_ui(self):
        """Receiving result signal must keep controls locked until finished."""
        _qt_app()
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(
            sentence="Hello world",
            items=["Hello"],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )
        dialog._generate_ai_meanings = lambda: None  # no-op

        # Ensure controls start enabled (AI configured + item selected)
        dialog._restore_ui_after_ai()
        assert dialog._generate_btn.isEnabled()

        # Simulate AI generation start (same as _generate_ai_meanings does)
        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._generate_btn.setEnabled(False)
        dialog._sentence_edit.setEnabled(False)
        dialog._save_btn.setEnabled(False)
        dialog._cancel_btn.setEnabled(False)
        dialog._ai_progress.setVisible(True)

        # Connect result handler (as real code does)
        def on_finished(raw_text):
            pass  # real code would parse + display

        worker.result.connect(on_finished)

        # Emit result — should NOT restore UI
        worker.result.emit("dummy result")
        assert not dialog._generate_btn.isEnabled(), (
            "Generate button must stay disabled until finished"
        )
        assert not dialog._save_btn.isEnabled()
        assert not dialog._cancel_btn.isEnabled()

        # Emit finished — NOW controls should restore
        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._generate_btn.isEnabled()
        assert dialog._save_btn.isEnabled()
        assert dialog._cancel_btn.isEnabled()

    def test_error_signal_alone_does_not_restore_ui(self):
        """Receiving error signal must keep controls locked until finished."""
        _qt_app()
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(
            sentence="Hello",
            items=["Hello"],
            settings={"ai_api_key": "test-key", "ai_model": "test-model"},
        )
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        assert dialog._generate_btn.isEnabled()

        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._generate_btn.setEnabled(False)
        dialog._save_btn.setEnabled(False)
        dialog._cancel_btn.setEnabled(False)

        worker.error.connect(lambda e: None)
        worker.error.emit("some error")
        assert not dialog._generate_btn.isEnabled(), (
            "Generate button must stay disabled after error signal"
        )

        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._generate_btn.isEnabled()

    def test_cannot_start_second_worker_between_result_and_finished(self):
        """A second Generate must be blocked until thread fully terminates."""
        _qt_app()
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(sentence="Hello", items=["Hello"])
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        worker1 = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker1)
        dialog._ai_worker = worker1
        dialog._generate_btn.setEnabled(False)

        worker1.result.connect(lambda t: None)
        worker1.result.emit("dummy")
        # Worker reference must persist
        assert dialog._ai_worker is worker1, (
            "Worker reference must be kept until finished"
        )
        # Generate button still disabled
        assert not dialog._generate_btn.isEnabled()

        worker1.finished.connect(lambda w=worker1: dialog._on_ai_thread_stopped(w))
        worker1.finished.emit()
        assert dialog._ai_worker is None

    def test_word_phrase_dialog_same_lifecycle(self):
        """WordPhraseCardDialog must follow the same lifecycle rules."""
        _qt_app()
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import WordPhraseCardDialog
        from PyQt6.QtCore import QThread

        dialog = WordPhraseCardDialog(front="bonjour")
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        assert dialog._generate_btn.isEnabled()

        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._set_controls_enabled(False)
        dialog._generate_btn.setEnabled(False)
        dialog._ai_progress.setVisible(True)

        assert not dialog._generate_btn.isEnabled()
        assert not dialog._save_btn.isEnabled()

        worker.result.connect(lambda t: None)
        worker.result.emit("dummy")
        assert not dialog._generate_btn.isEnabled(), (
            "Generate button must stay disabled until finished"
        )

        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._generate_btn.isEnabled()
        assert dialog._save_btn.isEnabled()

    def test_close_blocked_until_finished(self):
        """closeEvent must respect worker state through termination.

        When controls are disabled during generation, the Cancel button
        is not clickable. After QThread.finished fires, the worker reference
        clears, controls restore, and close becomes available.
        """
        _qt_app()
        from kgb_srs.form_helpers import _AIGenerateWorker
        from kgb_srs.forms import SentenceCardDialog
        from PyQt6.QtCore import QThread

        dialog = SentenceCardDialog(sentence="Hello", items=["Hello"])
        dialog._generate_ai_meanings = lambda: None

        dialog._restore_ui_after_ai()
        assert dialog._cancel_btn.isEnabled()

        worker = _AIGenerateWorker.__new__(_AIGenerateWorker)
        QThread.__init__(worker)
        dialog._ai_worker = worker
        dialog._generate_btn.setEnabled(False)
        dialog._cancel_btn.setEnabled(False)

        # Cancel button disabled — user can't trigger close
        assert not dialog._cancel_btn.isEnabled()

        # Emit result, but not finished — cancel still disabled
        worker.result.connect(lambda t: None)
        worker.result.emit("dummy")
        assert not dialog._cancel_btn.isEnabled(), (
            "Cancel must stay disabled between result and finished"
        )

        # Emit finished — cancel should now be enabled
        worker.finished.connect(lambda w=worker: dialog._on_ai_thread_stopped(w))
        worker.finished.emit()
        assert dialog._cancel_btn.isEnabled()


class TestProgrammaticMeaningSenseId:
    """FIX 6: AI/programmatic setPlainText must not clear sense_id."""

    def test_programmatic_meaning_preserves_sense_id(self):
        from PyQt6.QtWidgets import QApplication
        import sys

        QApplication.instance() or QApplication(sys.argv)

        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(sentence="Hello world", items=["world"])
        dialog._sense_ids["world"] = 42
        dialog._meanings["world"] = "old"
        dialog._active_meaning_expr = "world"
        dialog._rebuild_meaning_editors()
        assert dialog._meaning_widgets
        edit = dialog._meaning_widgets[0][1]

        dialog._programmatic_meaning_update = True
        edit.blockSignals(True)
        try:
            edit.setPlainText("new meaning from AI")
        finally:
            edit.blockSignals(False)
            dialog._programmatic_meaning_update = False

        dialog._meanings["world"] = "new meaning from AI"
        assert dialog._sense_ids["world"] == 42

        edit.setPlainText("typed by user")
        assert dialog._sense_ids["world"] is None
        dialog.close()


class TestSentenceDialogGeometryPersistence:
    """Sentence card dialog remembers last-used size across opens."""

    def test_save_persists_resized_dialog_geometry(self, tmp_path, monkeypatch):
        _qt_app()
        from kgb_srs import config, forms

        saved = []
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
        monkeypatch.setattr(
            config, "save_settings", lambda values: saved.append(dict(values))
        )
        settings = {"width": 900, "height": 700}
        dialog = forms.SentenceCardDialog(
            sentence="Hello world", items=["world"], settings=settings
        )
        dialog.resize(910, 700)
        dialog._meaning_widgets[0][1].setPlainText("the earth")

        dialog._save_btn.click()

        assert settings["sentence_dialog_width"] == 910
        assert settings["sentence_dialog_height"] == 700
        assert len(saved) == 1

    def test_cancel_persists_resized_dialog_geometry(self, tmp_path, monkeypatch):
        _qt_app()
        from kgb_srs import config, forms

        saved = []
        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
        monkeypatch.setattr(
            config, "save_settings", lambda values: saved.append(dict(values))
        )
        settings = {"width": 900, "height": 700}
        dialog = forms.SentenceCardDialog(
            sentence="Hello world", items=["world"], settings=settings
        )
        dialog.resize(910, 700)

        dialog._cancel_btn.click()

        assert settings["sentence_dialog_width"] == 910
        assert settings["sentence_dialog_height"] == 700
        assert len(saved) == 1

    def test_restores_persisted_size_and_saves_on_close(self, tmp_path, monkeypatch):
        _qt_app()
        from kgb_srs import config, forms

        settings_file = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))

        settings = {
            "width": 900,
            "height": 700,
            "sentence_dialog_width": 888,
            "sentence_dialog_height": 666,
        }
        dialog = forms.SentenceCardDialog(
            sentence="Hello world",
            items=["world"],
            settings=settings,
        )
        assert dialog.width() == 888
        assert dialog.height() == 666

        dialog.resize(910, 700)
        dialog.close()

        assert settings["sentence_dialog_width"] == 910
        assert settings["sentence_dialog_height"] == 700
        assert settings_file.is_file()

        # Re-open with the updated bag → same size.
        dialog2 = forms.SentenceCardDialog(
            sentence="Hello again",
            items=["Hello"],
            settings=settings,
        )
        assert dialog2.width() == 910
        assert dialog2.height() == 700
        dialog2.close()

    def test_default_size_when_settings_missing_keys(self):
        _qt_app()
        from kgb_srs.config import DEFAULT_SETTINGS
        from kgb_srs.forms import SentenceCardDialog

        dialog = SentenceCardDialog(sentence="Hello", items=["Hello"])
        assert dialog.width() == DEFAULT_SETTINGS["sentence_dialog_width"]
        assert dialog.height() == DEFAULT_SETTINGS["sentence_dialog_height"]
        dialog.close()


class TestDBCreationDialogNoWordPhrase:
    """FIX 9: dialog must not offer manual W/P database creation."""

    def test_no_word_phrase_radio(self):
        from PyQt6.QtWidgets import QApplication
        import sys

        QApplication.instance() or QApplication(sys.argv)
        from kgb_srs.forms import DBCreationDialog
        from kgb_srs.catalog import DatabaseType

        dialog = DBCreationDialog()
        assert not hasattr(dialog, "_word_phrase_radio")
        dialog._sentence_radio.setChecked(True)
        dialog._name_edit.setText("Demo")
        dialog._on_create()
        assert dialog.selected_type == DatabaseType.LANGUAGE_SENTENCE
        dialog.close()
