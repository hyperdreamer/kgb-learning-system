"""Regression tests for review controls and queue state."""

import datetime

import pytest

from kgb_srs.review_controller import ReviewHistoryEntry

from .qt_helpers import qt_app as _qt_app


@pytest.fixture(autouse=True)
def _dispose_top_level_widgets():
    """Keep root-local QSS from one Qt test out of the next test."""
    yield
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class TestReviewQueuePreservation:
    """Editing an unrelated card must not disrupt the current review card."""

    def test_edit_unrelated_preserves_current(self):
        """Simulate the logic: editing a different card should not change current."""
        # We can test the helper logic directly
        current_card = (1, "front1", "back1", 2)
        cards_due = [(1, "f1", "b1", 2), (2, "f2", "b2", 3)]

        # Scenario: editing card_id=2 (unrelated)
        card_id = 2
        fresh = (2, "f2_updated", "b2_updated", 1)

        # Apply the _refresh_current_card logic
        if current_card is not None and current_card[0] == card_id:
            current_card = fresh
        else:
            cards_due = [cf for cf in cards_due if cf[0] != card_id]
            cards_due.append(fresh)

        assert current_card == (1, "front1", "back1", 2), (
            "Current card should not change"
        )
        assert len(cards_due) == 2

    def test_edit_current_card_updates_it(self):
        """Editing the current card should refresh it."""
        current_card = (1, "front1", "back1", 2)

        card_id = 1
        fresh = (1, "front1_updated", "back1_updated", 1)

        if current_card is not None and current_card[0] == card_id:
            current_card = fresh

        assert current_card == (1, "front1_updated", "back1_updated", 1)


class TestReviewPresentationRegressions:
    def test_deleting_queued_card_removes_it_from_review_state(self):
        _qt_app()
        from kgb_srs.main_window import BarskyApp

        from types import SimpleNamespace

        window = SimpleNamespace(
            current_card=(1, "current", "back", 1),
            cards_due=[(2, "queued", "back", 2), (3, "other", "back", 1)],
            _daily_review_history=[
                ReviewHistoryEntry((2, "queued", "back", 2), "skipped"),
                ReviewHistoryEntry((4, "graded", "back", 3), "graded"),
            ],
            _daily_queue_snapshot=[(2, "queued", "back", 2), (3, "other", "back", 1)],
            _paused_cards_due=[(2, "queued", "back", 2)],
            _paused_daily_queue=[(2, "queued", "back", 2), (5, "paused", "back", 1)],
            _paused_review_history=[
                ReviewHistoryEntry((2, "queued", "back", 2), "skipped")
            ],
        )
        BarskyApp._remove_card_from_review_state(window, 2)
        assert [card[0] for card in window.cards_due] == [3]
        assert window.current_card[0] == 1
        assert [entry.card[0] for entry in window._daily_review_history] == [4]
        assert [card[0] for card in window._daily_queue_snapshot] == [3]
        assert window._paused_cards_due == []
        assert [card[0] for card in window._paused_daily_queue] == [5]
        assert window._paused_review_history == []

    def test_sentence_expression_labels_accept_structured_pairs(self):
        _qt_app()
        from kgb_srs.main_window import _expression_labels

        assert _expression_labels([("bonjour", "hello"), ("ami", "friend")]) == [
            "bonjour",
            "ami",
        ]

    def test_sentence_card_display_orders_and_labels_meanings_without_list_gutter(
        self, tmp_path
    ):
        """Review back uses flush manual labels in sentence order."""
        _qt_app()
        import sqlite3
        from types import SimpleNamespace
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.schema import init_db, insert_sentence_card

        db_path = tmp_path / "sentence_display.db"
        conn = sqlite3.connect(db_path)
        init_db(conn)
        # Insert exact before grievance so DB id order is reverse of sentence order.
        sentence = (
            "Revenge for a Grievance of a Hundred Generations May Still Be Exacted!"
        )
        card_id = insert_sentence_card(
            conn,
            sentence,
            [
                ("exact", "to demand and obtain (revenge) from someone"),
                (
                    "grievance",
                    "a real or imagined wrong or injustice that is the cause for revenge",
                ),
            ],
            back="",
        )
        win = SimpleNamespace(conn=conn)
        md = BarskyApp._build_sentence_card_display(
            win,
            card_id,
            sentence,
            "",
            flipped=True,
            metadata="**Box 1** | ID: `1`",
        )
        assert "**Grievance**" in md
        assert "**Exacted**" in md
        # Meanings ordered by sentence appearance, visibly labeled, and separate.
        # Escaping the label's period avoids a Markdown ordered-list gutter.
        assert "1\\. **grievance**:" in md
        assert "2\\. **exact**:" in md
        g_pos = md.index("1\\. **grievance**:")
        e_pos = md.index("2\\. **exact**:")
        assert g_pos < e_pos
        # Separate lines / blocks (blank line between labeled entries).
        between = md[g_pos:e_pos]
        assert "\n\n" in between
        conn.close()

    @staticmethod
    def _legacy_sentence_card():
        """Create a card whose cached back preserves its former add order."""
        import sqlite3

        from kgb_srs.schema import init_db, insert_sentence_card

        sentence = (
            "Revenge for a Grievance of a Hundred Generations May Still Be Exacted!"
        )
        legacy_back = (
            "1. **exact**: to demand and obtain (revenge) from someone\n\n"
            "2. **grievance**: a real or imagined wrong or injustice"
        )
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        card_id = insert_sentence_card(
            conn,
            sentence,
            [
                ("exact", "to demand and obtain (revenge) from someone"),
                ("grievance", "a real or imagined wrong or injustice"),
            ],
            back=legacy_back,
        )
        return conn, card_id, sentence, legacy_back

    def test_sentence_card_tts_uses_sentence_order_after_reveal(self):
        """TTS must follow the displayed sentence order, not a stale back cache."""
        _qt_app()
        from types import SimpleNamespace

        from kgb_srs.catalog import DatabaseType
        from kgb_srs.main_window import BarskyApp

        class CapturingCard:
            def set_text(self, display_text, is_flipped, speech_text):
                self.display_text = display_text
                self.is_flipped = is_flipped
                self.speech_text = speech_text

        conn, card_id, sentence, legacy_back = self._legacy_sentence_card()
        try:
            card = CapturingCard()
            window = SimpleNamespace(
                conn=conn,
                current_card=(card_id, sentence, legacy_back, 1),
                _db_type=DatabaseType.LANGUAGE_SENTENCE,
                card_ui=card,
            )
            window._build_sentence_card_display = (
                BarskyApp._build_sentence_card_display.__get__(window)
            )

            BarskyApp.flip_card(window)

            assert card.display_text.index(
                "1\\. **grievance**:"
            ) < card.display_text.index("2\\. **exact**:")
            assert card.speech_text.index("grievance:") < card.speech_text.index(
                "exact:"
            )
        finally:
            conn.close()

    def test_redrawn_sentence_card_tts_uses_sentence_order(self, monkeypatch):
        """A flipped redraw must retain the same sentence-ordered TTS input."""
        _qt_app()
        from types import SimpleNamespace

        import kgb_srs.review_controller as review_controller
        from kgb_srs.catalog import DatabaseType
        from kgb_srs.main_window import BarskyApp

        class CapturingCard:
            def __init__(self, *args):
                self.display_text = ""
                self.speech_text = ""

            def set_text(self, display_text, is_flipped, speech_text):
                self.display_text = display_text
                self.speech_text = speech_text

        class FakeScene:
            def width(self):
                return 900

            def height(self):
                return 700

            def addItem(self, item):
                self.item = item

        conn, card_id, sentence, legacy_back = self._legacy_sentence_card()
        try:
            window = SimpleNamespace(
                conn=conn,
                current_card=(card_id, sentence, legacy_back, 1),
                is_current_flipped=True,
                _db_type=DatabaseType.LANGUAGE_SENTENCE,
                scene=FakeScene(),
                _zone_y=600,
                _update_button_visibility=lambda: None,
            )
            window._build_sentence_card_display = (
                BarskyApp._build_sentence_card_display.__get__(window)
            )
            monkeypatch.setattr(review_controller, "FlashCardItem", CapturingCard)

            BarskyApp.draw_card_ui(window)

            assert window.card_ui.display_text.index(
                "1\\. **grievance**:"
            ) < window.card_ui.display_text.index("2\\. **exact**:")
            assert window.card_ui.speech_text.index(
                "grievance:"
            ) < window.card_ui.speech_text.index("exact:")
        finally:
            conn.close()


class TestReviewControls:
    """Button visibility (#1), close-preserves-queue (#2), resume semantics
    (#3), and delete behavior."""

    # -- shared helpers ---------------------------------------------------

    @staticmethod
    def _db(*ids):
        """Return in-memory conn with cards inserted (all due today)."""
        import sqlite3
        from kgb_srs.db import init_db

        conn = sqlite3.connect(":memory:")
        init_db(conn)
        today = datetime.date.today().isoformat()
        for cid in ids:
            conn.execute(
                "INSERT INTO cards (id, front, back, box, next_review) "
                "VALUES (?, ?, ?, 1, ?)",
                (cid, f"c{cid}", f"b{cid}", today),
            )
        conn.commit()
        return conn

    @staticmethod
    def _win(conn=None, card=None, due=(), mode="", paused_card=None, paused_mode=""):
        """Build BarskyApp with review state injected."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        if conn:
            w.conn = conn
        w.current_card = card
        w.cards_due = list(due)
        w.review_mode = mode
        w._paused_review_card = paused_card
        w._paused_review_mode = paused_mode
        return w

    @staticmethod
    def _mock_dialogs():
        """Patch QMessageBox to auto-confirm delete/acknowledge."""
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox

        return (
            patch(
                "kgb_srs.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("kgb_srs.main_window.QMessageBox.information"),
        )

    @staticmethod
    def _capture_review_context_emissions(window):
        """Record real context emissions with their state at emission time."""
        emissions = []
        set_review_context = window._set_review_context

        def capture(reviewed, remaining, visible):
            emissions.append(
                {
                    "context": (reviewed, remaining, visible),
                    "cards_due": tuple(window.cards_due),
                    "history": tuple(window._daily_review_history),
                    "current_card": window.current_card,
                    "current_card_transition": window._current_card_transition,
                }
            )
            set_review_context(reviewed, remaining, visible)

        window._set_review_context = capture
        return emissions

    # -- root design system and semantic chrome --------------------------

    def test_root_qss_has_semantic_button_state_selectors(self):
        """The root design system exposes token-backed button states by role."""
        _qt_app()
        from PyQt6.QtWidgets import QWidget

        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import LIGHT_TOKENS, ROLE_PROPERTY, stylesheet

        w = BarskyApp()
        try:
            w.settings = dict(w.settings)
            w.settings.update(font_family="Arial", font_size=14)
            w.apply_font_settings()

            root_qss = w.styleSheet()
            assert w.objectName() == "appRoot"
            assert w.findChild(QWidget, "appToolbar") is not None
            assert w.view.objectName() == "reviewCanvas"
            assert root_qss == stylesheet("Arial", 14)

            role_tokens = {
                "primary": ("primary", "primary_hover", "primary_pressed"),
                "secondary": ("surface", "surface_hover", "surface_subtle"),
                "success": ("success", "success_hover", "success_pressed"),
                "danger": ("danger", "danger_hover", "danger_pressed"),
            }
            for role, tokens in role_tokens.items():
                selector = f'QPushButton[{ROLE_PROPERTY}="{role}"]'
                assert selector in root_qss
                for state in ("hover", "pressed", "disabled", "focus"):
                    assert f"{selector}:{state}" in root_qss
                for token in tokens:
                    assert LIGHT_TOKENS[token] in root_qss

            assert LIGHT_TOKENS["disabled_surface"] in root_qss
            assert LIGHT_TOKENS["disabled_text"] in root_qss
            assert LIGHT_TOKENS["focus"] in root_qss
        finally:
            w.close()

    def test_semantic_button_roles_are_not_text_discriminated(self):
        """Actual chrome controls retain their semantic role when relabeled."""
        _qt_app()
        from PyQt6.QtWidgets import QLabel

        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import ROLE_PROPERTY

        w = BarskyApp()
        try:
            w.settings = dict(w.settings)
            w.settings.update(font_family="Arial", font_size=14)
            w.apply_font_settings()

            roles = {
                "new_db_btn": "primary",
                "start_btn": "primary",
                "db_btn": "secondary",
                "add_entry_btn": "secondary",
                "browse_btn": "secondary",
                "settings_btn": "secondary",
                "restart_review_btn": "secondary",
                "previous_review_btn": "secondary",
                "edit_review_btn": "secondary",
                "delete_entry_btn": "danger",
                "close_review_btn": "icon",
            }
            for index, (attribute, role) in enumerate(roles.items()):
                control = getattr(w, attribute)
                assert control.property(ROLE_PROPERTY) == role
                renamed = f"Renamed control {index}"
                control.setText(renamed)
                assert control.property(ROLE_PROPERTY) == role
                assert renamed not in w.styleSheet()

            status_label = w.findChild(QLabel, "reviewStatusLabel")
            assert status_label is not None
            assert status_label.property(ROLE_PROPERTY) == "quiet"
            assert f'QPushButton[{ROLE_PROPERTY}="primary"]' in w.styleSheet()
            assert f'QPushButton[{ROLE_PROPERTY}="secondary"]' in w.styleSheet()
        finally:
            w.close()

    def test_review_context_uses_root_quiet_label_treatment_without_local_qss(self):
        """The real status label inherits its muted quiet rule from root QSS."""
        _qt_app()
        from PyQt6.QtWidgets import QApplication, QLabel

        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import LIGHT_TOKENS, ROLE_PROPERTY

        w = BarskyApp()
        try:
            w.settings = dict(w.settings)
            w.settings.update(font_family="Arial", font_size=14)
            w.apply_font_settings()
            w.show()
            QApplication.processEvents()

            status_label = w.findChild(QLabel, "reviewStatusLabel")
            assert status_label is not None
            quiet_selector = f'QLabel[{ROLE_PROPERTY}="quiet"]'
            quiet_rule = (
                f"{quiet_selector} {{\n  color: {LIGHT_TOKENS['text_muted']};\n}}"
            )
            assert status_label.property(ROLE_PROPERTY) == "quiet"
            assert status_label.styleSheet() == ""
            assert quiet_rule in w.styleSheet()

            w._set_review_context(3, 7, True)
            assert status_label.text() == "Reviewed 3 · Remaining 7"
            assert status_label.isVisible()
            w._set_review_context(3, 7, False)
            assert status_label.text() == "Reviewed 3 · Remaining 7"
            assert not status_label.isVisible()
        finally:
            w.close()

    def test_apply_font_settings_propagates_validated_ui_font_at_root(self):
        """The active UI font and safe generated QSS share one root boundary."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import font_css, stylesheet

        w = BarskyApp()
        try:
            w.settings = dict(w.settings)
            w.settings.update(font_family="Courier New", font_size=19)
            w.apply_font_settings()

            assert w.font().family() == "Courier New"
            assert w.font().pointSize() == 19
            assert w.random_checkbox.font().family() == "Courier New"
            assert (
                w.random_checkbox.font().pointSize() == 19
                or w.random_checkbox.font().pixelSize() == 19
            )
            assert w.styleSheet() == stylesheet("Courier New", 19)
            assert font_css("Courier New", 19) in w.styleSheet()

            hostile_family = "Bad'; QPushButton { color: red; }"
            w.settings["font_family"] = hostile_family
            w.apply_font_settings()

            assert w.styleSheet() == stylesheet("Arial", 19)
            assert font_css("Arial", 19) in w.styleSheet()
            assert hostile_family not in w.styleSheet()
            assert "color: red" not in w.styleSheet()
        finally:
            w.close()

    def test_changing_font_size_changes_button_font_metrics(self):
        """Changing Settings font_size must change actual button font
        metrics / size hints."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        w._save_settings = lambda: None

        w.settings["font_size"] = 14
        w.apply_font_settings()
        small_hint = w.start_btn.sizeHint().height()
        small_font_height = w.start_btn.fontMetrics().height()

        w.settings["font_size"] = 28
        w.apply_font_settings()
        large_hint = w.start_btn.sizeHint().height()
        large_font_height = w.start_btn.fontMetrics().height()

        assert large_hint > small_hint, (
            f"Button sizeHint must grow with font_size: {large_hint} not > {small_hint}"
        )
        assert large_font_height > small_font_height, (
            f"Button font metrics must grow with font_size: "
            f"{large_font_height} not > {small_font_height}"
        )

        w.close()

    def test_drop_zone_html_includes_ui_font(self):
        """Correct/Incorrect drop zone HTML injects UI font-family and size."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        w.settings["font_family"] = "Courier New"
        w.settings["font_size"] = 19
        w.resize(900, 700)
        w.show()
        _qt_app().processEvents()
        w.redraw_canvas()
        _qt_app().processEvents()

        for zone_name in ("incorrect_zone", "correct_zone"):
            zone = getattr(w, zone_name)
            html = zone.text_item.toHtml()
            assert "Courier New" in html or "font-family" in html.lower(), (
                f"{zone_name} HTML must include UI font-family, got: {html[:200]!r}"
            )
            # Qt may rewrite style attributes; check either inline style or
            # that font-size 19 is present somewhere in the HTML.
            assert (
                "font-size: 19" in html
                or "font-size:19" in html
                or 'font-size="19' in html
                or "19pt" in html
                or "19px" in html
            ), f"{zone_name} HTML must include UI font-size 19, got: {html[:300]!r}"
            assert "Courier" in html, (
                f"{zone_name} HTML must mention Courier font family"
            )

        w.close()

    def test_browse_dialog_inherits_ui_font(self, monkeypatch):
        """Browse Cards dialog receives main window UI font via setFont."""
        _qt_app()
        import inspect

        from PyQt6.QtWidgets import QDialog
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        w._save_settings = lambda: None
        w.settings["font_family"] = "Arial"
        w.settings["font_size"] = 21
        w.apply_font_settings()

        # Source-level contract: browse_cards must set dialog font from self
        source = inspect.getsource(BarskyApp.browse_cards)
        assert "setFont" in source and "self.font()" in source, (
            "browse_cards must call dialog.setFont(self.font())"
        )

        class FakeCursor:
            def execute(self, *a, **k):
                return self

            def fetchall(self):
                return []

        class FakeConn:
            def cursor(self):
                return FakeCursor()

        w.conn = FakeConn()
        w.current_lang = "Test"
        w._db_type = None

        captured = {}

        def fake_exec(self):
            captured["font_size"] = self.font().pointSize()
            captured["font_pixel_size"] = self.font().pixelSize()
            captured["font_family"] = self.font().family()
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QDialog, "exec", fake_exec)
        w.browse_cards()

        assert (
            captured.get("font_size") == 21 or captured.get("font_pixel_size") == 21
        ), f"Browse dialog must inherit UI font size 21, got {captured}"
        assert captured.get("font_family") == "Arial"
        w.close()

    def test_toolbar_controls_inherit_root_font_and_have_no_child_qss(self):
        """Toolbar chrome inherits the root UI font without local stylesheets."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp
        from kgb_srs.ui_theme import ROLE_PROPERTY, stylesheet

        w = BarskyApp()
        try:
            w.settings = dict(w.settings)
            w.settings.update(font_family="Arial", font_size=17)
            w.apply_font_settings()

            expected_roles = {
                "db_btn": "secondary",
                "new_db_btn": "primary",
                "add_entry_btn": "secondary",
            }
            for attribute, role in expected_roles.items():
                button = getattr(w, attribute)
                assert button.property(ROLE_PROPERTY) == role
                assert button.font().family() == "Arial"
                assert (
                    button.font().pointSize() == 17 or button.font().pixelSize() == 17
                )
                assert button.styleSheet() == ""

            assert (
                w.random_checkbox.font().pointSize() == 17
                or w.random_checkbox.font().pixelSize() == 17
            )
            assert w.styleSheet() == stylesheet("Arial", 17)
        finally:
            w.close()

    def test_review_context_label_formats_without_mutating_controls(self):
        """Showing or hiding quiet context only changes the dedicated label."""
        _qt_app()
        from PyQt6.QtWidgets import QApplication, QLabel

        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        try:
            w.show()
            QApplication.processEvents()
            status_label = w.findChild(QLabel, "reviewStatusLabel")
            assert status_label is not None
            before = (
                w.start_btn.text(),
                w.start_btn.isEnabled(),
                w.close_review_btn.isEnabled(),
            )

            w._set_review_context(3, 7, True)
            assert status_label.text() == "Reviewed 3 · Remaining 7"
            assert status_label.isVisible()
            assert (
                w.start_btn.text(),
                w.start_btn.isEnabled(),
                w.close_review_btn.isEnabled(),
            ) == before

            w._set_review_context(3, 7, False)
            assert status_label.text() == "Reviewed 3 · Remaining 7"
            assert not status_label.isVisible()
            assert (
                w.start_btn.text(),
                w.start_btn.isEnabled(),
                w.close_review_btn.isEnabled(),
            ) == before

            w._set_review_context(3, 7, True)
            w._update_button_visibility()
            assert not status_label.isVisible()

            class ControllerOnlyFake:
                pass

            ControllerOnlyFake._update_review_context = BarskyApp._update_review_context
            ControllerOnlyFake()._update_review_context()
        finally:
            w.close()

    # -- finding #1: button visibility after DB load ----------------------

    def test_buttons_after_db_load(self):
        """IDLE state: Start enabled; Restart/Previous/Close disabled.

        force_seq_btn has been removed (merged into the primary button)."""
        import tempfile
        import os

        conn = self._db(1)
        tmp = tempfile.NamedTemporaryFile(suffix="_barsky.db", delete=False)
        tmp.close()
        try:
            from kgb_srs.db import init_db

            init_db(tmp.name).close()
            w = self._win(conn=conn)
            w.current_db_path = tmp.name
            w.current_lang = "Test"
            w.load_database(silent=True)

            assert w.start_btn.isEnabled()
            assert "Start Daily Review" in w.start_btn.text()
            assert not w.restart_review_btn.isEnabled()
            assert not w.previous_review_btn.isEnabled()
            assert not w.delete_entry_btn.isEnabled()
            assert not w.close_review_btn.isEnabled()
            # force_seq_btn must not exist
            assert not hasattr(w, "force_seq_btn")
        finally:
            os.unlink(tmp.name)
        conn.close()
        w.close()

    def test_delete_and_close_enabled_during_review(self):
        """Delete/Close enabled when card + active review mode exist."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w._update_button_visibility()

        assert w.delete_entry_btn.isEnabled()
        assert w.close_review_btn.isEnabled()
        conn.close()
        w.close()

    def test_buttons_disabled_after_close(self):
        """After close_review, Delete and Close are disabled."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w.close_review()

        assert not w.delete_entry_btn.isEnabled()
        assert not w.close_review_btn.isEnabled()
        conn.close()
        w.close()

    def test_wp_hides_add_and_delete_entry(self):
        """Word/phrase DBs hide Add/Delete Entry (projection-only)."""
        from kgb_srs.catalog import DatabaseType

        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w._db_type = DatabaseType.LANGUAGE_WORD_PHRASE
        w._update_button_visibility()

        assert not w.add_entry_btn.isVisible()
        assert not w.add_entry_btn.isEnabled()
        assert not w.delete_entry_btn.isVisible()
        assert not w.delete_entry_btn.isEnabled()

        # Guard remains even if called directly.
        q, i = self._mock_dialogs()
        with q, i as imock:
            w.delete_current_card()
            imock.assert_called()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM cards")
            assert cur.fetchone()[0] == 1
        conn.close()
        w.close()

    def test_start_selected_card_review_opens_one_card_session(self):
        """Browse → Review Selected starts a one-card daily session."""
        from PyQt6.QtWidgets import QApplication, QLabel

        conn = self._db(1, 2, 3)
        w = self._win(conn=conn)
        try:
            w.show()
            QApplication.processEvents()
            w._start_selected_card_review(2)

            assert w.review_mode == "daily"
            assert w.current_card is not None
            assert w.current_card[0] == 2
            assert w.current_card[1] == "c2"
            # Queue was the selected card only; show_next_card consumed it.
            assert w.cards_due == []
            assert [c[0] for c in w._daily_queue_snapshot] == [2]
            assert w.close_review_btn.isEnabled()
            status_label = w.findChild(QLabel, "reviewStatusLabel")
            assert status_label is not None
            assert status_label.isVisible()
            assert status_label.text() == "Reviewed 0 · Remaining 1"
        finally:
            conn.close()
            w.close()

    def test_daily_review_context_uses_display_only_formula_across_transitions(self):
        """Daily context tracks grade/queue state without changing review state."""
        from unittest.mock import patch

        from PyQt6.QtWidgets import QApplication, QLabel

        conn = self._db(1, 2)
        w = self._win(conn=conn)
        try:
            w.show()
            QApplication.processEvents()
            status_label = w.findChild(QLabel, "reviewStatusLabel")
            assert status_label is not None

            def assert_context(reviewed, remaining, visible):
                before = (
                    list(w.cards_due),
                    list(w._daily_review_history),
                    w.current_card,
                    w.review_mode,
                )
                assert w._review_context_counts() == (reviewed, remaining)
                w._update_review_context()
                assert status_label.text() == (
                    f"Reviewed {reviewed} · Remaining {remaining}"
                )
                assert status_label.isVisible() is visible
                assert (
                    list(w.cards_due),
                    list(w._daily_review_history),
                    w.current_card,
                    w.review_mode,
                ) == before

            w.start_review()
            assert_context(0, 2, True)

            w._advance_daily_queue()
            assert_context(0, 2, True)

            w.flip_card()
            w.process_answer(True)
            assert_context(1, 1, True)

            w._previous_daily_card()
            assert_context(1, 1, True)

            w._advance_daily_queue()
            assert_context(1, 1, True)

            w.flip_card()
            with patch("kgb_srs.review_controller.QMessageBox.information"):
                w.process_answer(True)
            assert_context(2, 0, True)

            w.close_review()
            assert_context(0, 0, False)
        finally:
            conn.close()
            w.close()

    def test_review_context_skip_refreshes_after_next_card_is_coherent(self):
        """Skip emits context only after its replacement card is selected."""
        conn = self._db(1, 2)
        w = self._win(conn=conn)
        try:
            w.start_review()
            source_card = w.current_card
            assert source_card is not None
            source_id = source_card[0]

            emissions = self._capture_review_context_emissions(w)
            emissions.clear()
            w._advance_daily_queue()

            assert [emission["context"] for emission in emissions] == [(0, 2, True)]
            emission = emissions[0]
            current_card = emission["current_card"]
            assert current_card is not None
            assert current_card[0] != source_id
            assert all(card[0] != current_card[0] for card in emission["cards_due"])
            assert [card[0] for card in emission["cards_due"]].count(source_id) == 1
            assert [
                (entry.card[0], entry.transition) for entry in emission["history"]
            ] == [(source_id, "skipped")]
        finally:
            conn.close()
            w.close()

    def test_review_context_grade_refreshes_after_next_card_is_coherent(self):
        """A fresh grade emits only after the next card becomes current."""
        conn = self._db(1, 2)
        w = self._win(conn=conn)
        try:
            w.start_review()
            source_card = w.current_card
            assert source_card is not None
            source_id = source_card[0]
            w.flip_card()

            emissions = self._capture_review_context_emissions(w)
            emissions.clear()
            w.process_answer(True)

            assert [emission["context"] for emission in emissions] == [(1, 1, True)]
            emission = emissions[0]
            current_card = emission["current_card"]
            assert current_card is not None
            assert current_card[0] != source_id
            assert emission["current_card_transition"] is None
            assert all(card[0] != current_card[0] for card in emission["cards_due"])
            assert [
                (entry.card[0], entry.transition) for entry in emission["history"]
            ] == [(source_id, "graded")]
        finally:
            conn.close()
            w.close()

    def test_review_context_restored_grade_refreshes_after_next_card_is_coherent(
        self,
    ):
        """A restored grade is not counted again while its successor is selected."""
        conn = self._db(1, 2)
        w = self._win(conn=conn)
        try:
            w.start_review()
            restored_card = w.current_card
            assert restored_card is not None
            restored_id = restored_card[0]
            w.flip_card()
            w.process_answer(True)
            w._previous_daily_card()
            assert w.current_card is not None
            assert w.current_card[0] == restored_id
            assert w._current_card_transition == "graded"

            emissions = self._capture_review_context_emissions(w)
            emissions.clear()
            w._advance_daily_queue()

            assert [emission["context"] for emission in emissions] == [(1, 1, True)]
            emission = emissions[0]
            current_card = emission["current_card"]
            assert current_card is not None
            assert current_card[0] != restored_id
            assert emission["current_card_transition"] is None
            assert all(card[0] != current_card[0] for card in emission["cards_due"])
            assert [
                (entry.card[0], entry.transition) for entry in emission["history"]
            ] == [(restored_id, "graded")]
        finally:
            conn.close()
            w.close()

    def test_review_context_completion_emits_only_cleared_state(self):
        """Completion never emits while the last graded source remains current."""
        from unittest.mock import patch

        conn = self._db(1, 2)
        w = self._win(conn=conn)
        try:
            w.start_review()
            first_card = w.current_card
            assert first_card is not None
            first_id = first_card[0]
            w.flip_card()
            w.process_answer(True)
            final_card = w.current_card
            assert final_card is not None
            final_id = final_card[0]
            w.flip_card()

            emissions = self._capture_review_context_emissions(w)
            emissions.clear()
            with patch("kgb_srs.review_controller.QMessageBox.information"):
                w.process_answer(True)

            contexts = [emission["context"] for emission in emissions]
            assert contexts == [(2, 0, True)]
            assert (2, 1, True) not in contexts
            emission = emissions[0]
            assert emission["current_card"] is None
            assert emission["current_card_transition"] is None
            assert emission["cards_due"] == ()
            assert [
                (entry.card[0], entry.transition) for entry in emission["history"]
            ] == [(first_id, "graded"), (final_id, "graded")]
        finally:
            conn.close()
            w.close()

    def test_start_selected_card_review_missing_card(self):
        """Missing card id does not start a review."""
        conn = self._db(1)
        w = self._win(conn=conn)
        q, i = self._mock_dialogs()
        with q, i as imock:
            w._start_selected_card_review(999)
            imock.assert_called()
        assert w.current_card is None
        assert w.review_mode == ""
        conn.close()
        w.close()

    def test_keyboard_shortcuts_are_installed(self):
        """Main window installs Alt-based review/chrome shortcuts."""
        from PyQt6.QtGui import QShortcut

        conn = self._db(1)
        w = self._win(conn=conn)
        shortcuts = w.findChildren(QShortcut)
        keys = {sc.key().toString() for sc in shortcuts}
        for expected in (
            "Alt+S",
            "Alt+R",
            "Alt+Left",
            "Alt+Right",
            "Alt+X",
            "Alt+B",
            "Alt+N",
            "Alt+D",
            "Alt+T",
            "Alt+P",
            "Alt+L",
        ):
            assert any(expected in k or k == expected for k in keys), (
                f"Missing shortcut {expected!r} in {sorted(keys)}"
            )
        conn.close()
        w.close()

    def test_shortcut_reveal_and_grade(self):
        """Alt+R reveals; Alt+Right grades correct only after flip."""
        from unittest.mock import MagicMock

        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w.is_current_flipped = False
        card_ui = MagicMock()
        w.card_ui = card_ui
        # Avoid real scene item removal when grading advances.
        w.scene = MagicMock()
        q, i = self._mock_dialogs()

        with q, i:
            w._shortcut_correct()  # ignored before flip
            cur = conn.cursor()
            cur.execute("SELECT box FROM cards WHERE id=1")
            assert cur.fetchone()[0] == 1

            w._shortcut_reveal()
            assert w.is_current_flipped is True
            card_ui.set_text.assert_called()

            w._shortcut_correct()
            cur.execute("SELECT box FROM cards WHERE id=1")
            assert cur.fetchone()[0] == 2
        conn.close()
        w.close()

    def test_shortcut_closes_completed_daily_review(self):
        """Alt+X closes an active daily session after its final grade."""
        conn = self._db(1)
        w = self._win(conn=conn, mode="daily")
        w._daily_review_history = [ReviewHistoryEntry((1, "c1", "b1", 2), "graded")]
        w._update_button_visibility()
        assert w.current_card is None
        assert w.close_review_btn.isEnabled()

        w._shortcut_close_review()

        assert w.review_mode == ""
        assert w._paused_review_mode == "daily"
        assert w._paused_review_history == [
            ReviewHistoryEntry((1, "c1", "b1", 2), "graded")
        ]
        assert "Resume Daily Review" in w.start_btn.text()
        conn.close()
        w.close()

    def test_shortcut_tooltips_use_alt(self):
        """Button tooltips document Alt shortcuts."""
        conn = self._db(1)
        w = self._win(conn=conn)
        assert "Alt+B" in w.browse_btn.toolTip()
        assert "Alt+S" in w.start_btn.toolTip()
        assert "Alt+X" in w.close_review_btn.toolTip()
        assert "Alt+T" in w.restart_review_btn.toolTip()
        assert "Alt+P" in w.previous_review_btn.toolTip()
        conn.close()
        w.close()

    # -- finding #2: close preserves queue ---------------------------------

    def test_close_preserves_cards_due(self):
        """cards_due content and order unchanged after close_review."""
        conn = self._db(1, 2, 3)
        due = [(2, "c2", "b2", 1), (3, "c3", "b3", 1)]
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), due=due, mode="daily")

        snapshot = list(w.cards_due)
        w.close_review()
        assert w.cards_due == snapshot, "cards_due must survive close unchanged"
        conn.close()
        w.close()

    # -- close semantics ---------------------------------------------------

    def test_close_stores_paused_card_and_mode(self):
        """close_review saves card + mode, clears current_card + review_mode."""
        conn = self._db(1, 2)
        w = self._win(
            conn=conn, card=(1, "c1", "b1", 1), due=[(2, "c2", "b2", 1)], mode="daily"
        )
        w.close_review()

        assert w._paused_review_card[0] == 1
        assert w._paused_review_mode == "daily"
        assert w.current_card is None
        assert w.review_mode == ""
        conn.close()
        w.close()

    def test_close_does_not_mutate_db(self):
        """close_review leaves the database unchanged."""
        conn = self._db(1, 2)
        w = self._win(
            conn=conn, card=(1, "c1", "b1", 1), due=[(2, "c2", "b2", 1)], mode="daily"
        )

        before = list(
            conn.execute(
                "SELECT id, box, next_review FROM cards ORDER BY id"
            ).fetchall()
        )
        w.close_review()
        after = list(
            conn.execute(
                "SELECT id, box, next_review FROM cards ORDER BY id"
            ).fetchall()
        )
        assert before == after
        conn.close()
        w.close()

    def test_close_noop_without_card(self):
        """close_review is safe when no card is shown."""
        w = self._win()
        w.close_review()
        assert w._paused_review_card is None
        w.close()

    # -- resume: daily after close ----------------------------------------

    def test_daily_resume_paused_first_preserved_queue(self):
        """After daily close, start_review shows paused card first,
        then the preserved remaining queue (no requery)."""
        conn = self._db(1, 2, 3)
        w = self._win(
            conn=conn, card=(2, "c2", "b2", 1), due=[(3, "c3", "b3", 1)], mode="daily"
        )
        w.close_review()
        w.start_review()

        assert w.current_card[0] == 2, "paused card must be first"
        assert w._paused_review_card is None
        assert [c[0] for c in w.cards_due] == [3], "preserved queue follows"
        conn.close()
        w.close()

    def test_daily_resume_no_duplicate_in_queue(self):
        """Paused card is de-duplicated from the resumed queue."""
        conn = self._db(1, 2, 3)
        w = self._win(
            conn=conn,
            card=(2, "c2", "b2", 1),
            due=[(2, "c2", "b2", 1), (3, "c3", "b3", 1)],
            mode="daily",
        )
        w.close_review()
        w.start_review()
        assert sum(1 for c in w.cards_due if c[0] == 2) == 0
        conn.close()
        w.close()

    def test_daily_resume_skips_deleted_paused(self):
        """If paused card was deleted, silently skip to next card."""
        conn = self._db(1, 2)
        w = self._win(
            conn=conn, card=(1, "c1", "b1", 1), due=[(2, "c2", "b2", 1)], mode="daily"
        )
        w.close_review()
        conn.execute("DELETE FROM cards WHERE id=1")
        conn.commit()
        w.start_review()
        assert w.current_card[0] != 1
        conn.close()
        w.close()

    def test_daily_resume_fresh_db_data(self):
        """Resumed card re-fetched from DB (sees external edits)."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
        w.close_review()
        conn.execute("UPDATE cards SET front='updated', box=2 WHERE id=1")
        conn.commit()
        w.start_review()
        assert w.current_card[1] == "updated"
        assert w.current_card[3] == 2
        conn.close()
        w.close()

    def test_refresh_missing_current_card_clears_stale_display(self):
        """Refreshing a deleted current card clears UI without advancing."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from kgb_srs.main_window import BarskyApp

        conn = self._db(2)
        card_ui = object()
        scene = MagicMock()
        draw_card_ui = MagicMock()
        w = SimpleNamespace(
            conn=conn,
            current_card=(1, "stale", "back", 1),
            cards_due=[(1, "stale", "back", 1), (2, "c2", "b2", 1)],
            is_current_flipped=True,
            card_ui=card_ui,
            scene=scene,
            draw_card_ui=draw_card_ui,
        )

        BarskyApp._refresh_current_card(w, 1)

        assert w.cards_due == [(2, "c2", "b2", 1)]
        assert w.current_card is None
        assert w.is_current_flipped is False
        assert w.card_ui is None
        scene.removeItem.assert_called_once_with(card_ui)
        draw_card_ui.assert_not_called()
        conn.close()

    def test_refresh_missing_queued_card_prunes_without_current(self):
        """Refreshing a deleted queued card removes it even with no current card."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from kgb_srs.main_window import BarskyApp

        conn = self._db(2)
        w = SimpleNamespace(
            conn=conn,
            current_card=None,
            cards_due=[(1, "stale", "back", 1), (2, "c2", "b2", 1)],
            is_current_flipped=True,
            card_ui=None,
            scene=MagicMock(),
            draw_card_ui=MagicMock(),
        )

        BarskyApp._refresh_current_card(w, 1)

        assert w.cards_due == [(2, "c2", "b2", 1)]
        assert w.current_card is None
        assert w.card_ui is None
        w.scene.removeItem.assert_not_called()
        w.draw_card_ui.assert_not_called()
        conn.close()

    def test_daily_start_without_pause_fresh_query(self):
        """No paused card → normal daily review with fresh DB query."""
        conn = self._db(1, 2)
        w = self._win(conn=conn)
        w.start_review()
        assert w.current_card is not None
        assert w.review_mode == "daily"
        conn.close()
        w.close()

    # -- DB load clears paused state --------------------------------------

    def test_db_load_clears_paused_state(self):
        """Loading a database resets paused review state."""
        import tempfile
        import os

        conn = self._db(1)
        # Create a real temp DB so load_database doesn't early-return
        tmp = tempfile.NamedTemporaryFile(suffix="_barsky.db", delete=False)
        tmp.close()
        try:
            from kgb_srs.db import init_db

            init_db(tmp.name).close()
            w = self._win(conn=conn, card=(1, "c1", "b1", 1), mode="daily")
            w.close_review()
            assert w._paused_review_card is not None

            w.current_db_path = tmp.name
            w.current_lang = "Test"
            w.load_database(silent=True)
            assert w._paused_review_card is None
            assert w._paused_review_mode == ""
        finally:
            os.unlink(tmp.name)
        conn.close()
        w.close()

    def test_missing_candidate_load_preserves_active_review_state(self, tmp_path):
        """A missing selection leaves the active review untouched."""
        conn = self._db(1)
        w = self._win(conn=conn)
        w.current_db_path = "active_barsky.db"
        w.current_lang = "Active"
        w.start_review()
        assert w.current_card is not None
        assert w.review_mode == "daily"
        card = w.current_card
        due = w.cards_due

        w.load_database(
            silent=True,
            db_path=str(tmp_path / "missing_barsky.db"),
            display="Missing",
        )

        assert w.conn is conn
        assert w.current_db_path == "active_barsky.db"
        assert w.current_lang == "Active"
        assert w.current_card is card
        assert w.cards_due is due
        assert w.review_mode == "daily"
        w.close()

    def test_create_sentence_db_survives_projection_failure(
        self, tmp_path, monkeypatch
    ):
        """Projection errors are non-modal and do not block source DB creation."""
        _qt_app()
        from PyQt6.QtWidgets import QDialog
        from kgb_srs.catalog import (
            DatabaseType,
            DB_DIR_LANGUAGE_SENTENCE,
            read_database_type,
        )
        from kgb_srs.main_window import BarskyApp

        db_root = tmp_path / "databases"
        name = "ProjectionFailure"

        class AcceptedCreationDialog:
            def __init__(self, *args, **kwargs):
                self.selected_type = DatabaseType.LANGUAGE_SENTENCE
                self.db_name = name

            def exec(self):
                return QDialog.DialogCode.Accepted

        def fail_projection(*args, **kwargs):
            raise RuntimeError("projection unavailable")

        monkeypatch.setattr(
            "kgb_srs.main_window.DBCreationDialog", AcceptedCreationDialog
        )
        monkeypatch.setattr(
            BarskyApp, "_ensure_all_word_phrase_projections", lambda self: None
        )
        monkeypatch.setattr(
            "kgb_srs.senses.ensure_linked_word_phrase_database", fail_projection
        )
        warning_calls = []
        monkeypatch.setattr(
            "kgb_srs.main_window.QMessageBox.warning",
            lambda *args: warning_calls.append(args),
        )
        monkeypatch.setattr(
            "kgb_srs.main_window.QMessageBox.information", lambda *args: None
        )

        w = BarskyApp()
        w.settings = dict(w.settings)
        w.settings["database_root"] = str(db_root)
        w._save_settings = lambda: None
        w.create_new_database()

        expected_path = db_root / DB_DIR_LANGUAGE_SENTENCE / f"{name}_barsky.db"
        assert expected_path.is_file()
        assert w.current_db_path == str(expected_path)
        assert w.conn is not None
        assert read_database_type(w.conn) == DatabaseType.LANGUAGE_SENTENCE
        assert warning_calls == []
        w.close()

    def test_temporary_settings_root_scopes_startup_without_default_database(
        self, tmp_path, monkeypatch
    ):
        """An explicit temporary settings file scopes all startup root work."""
        _qt_app()
        import json

        import kgb_srs.main_window as main_window

        settings_path = tmp_path / "settings.json"
        database_root = tmp_path / "isolated-databases"
        settings_path.write_text(
            json.dumps(
                {
                    "database_root": str(database_root),
                    "default_database": "",
                }
            ),
            encoding="utf-8",
        )

        original_load_settings = main_window.load_settings
        loaded_paths = []

        def tracked_load_settings(path=None):
            loaded_paths.append(path)
            return original_load_settings(path)

        original_ensure_root = main_window.ensure_database_root_structure
        created_roots = []

        def tracked_ensure_root(root):
            created_roots.append(root)
            return original_ensure_root(root)

        projection_roots = []
        default_database_searches = []
        monkeypatch.setattr(main_window, "load_settings", tracked_load_settings)
        monkeypatch.setattr(
            main_window, "ensure_database_root_structure", tracked_ensure_root
        )
        monkeypatch.setattr(
            "kgb_srs.senses.ensure_all_sentence_databases_linked",
            lambda root: projection_roots.append(root),
        )
        monkeypatch.setattr(
            main_window,
            "find_databases",
            lambda root: default_database_searches.append(root) or [],
        )

        w = main_window.BarskyApp(settings_file=settings_path)
        try:
            expected_root = str(database_root.resolve())
            assert loaded_paths == [str(settings_path.resolve())]
            assert created_roots == [expected_root]
            assert projection_roots == [expected_root]
            assert default_database_searches == []
            assert database_root.is_dir()
            assert w.current_db_path is None
            assert w.conn is None
        finally:
            w.close()

    # -- delete behavior --------------------------------------------------

    def test_delete_clears_paused_and_advances(self):
        """Deleting active card: DB row gone, paused cleared, next shown."""
        conn = self._db(1, 2)
        w = self._win(
            conn=conn,
            card=(1, "c1", "b1", 1),
            due=[(2, "c2", "b2", 1)],
            mode="daily",
            paused_card=(1, "c1", "b1", 1),
            paused_mode="daily",
        )

        p1, p2 = self._mock_dialogs()
        with p1, p2:
            w.delete_current_card()

        assert conn.execute("SELECT id FROM cards WHERE id=1").fetchone() is None
        assert w.current_card[0] == 2
        assert w._paused_review_card is None
        assert w._paused_review_mode == ""
        conn.close()
        w.close()

    def test_delete_last_card_disables_buttons(self):
        """Deleting the last card: no current card, buttons disabled."""
        conn = self._db(1)
        w = self._win(conn=conn, card=(1, "c1", "b1", 1), due=[], mode="daily")

        p1, p2 = self._mock_dialogs()
        with p1, p2:
            w.delete_current_card()

        assert w.current_card is None
        assert not w.delete_entry_btn.isEnabled()
        assert not w.close_review_btn.isEnabled()
        conn.close()
        w.close()

    def test_delete_removes_from_queue(self):
        """Deleted card removed from cards_due."""
        conn = self._db(1, 2)
        w = self._win(
            conn=conn,
            card=(1, "c1", "b1", 1),
            due=[(1, "c1", "b1", 1), (2, "c2", "b2", 1)],
            mode="daily",
        )

        p1, p2 = self._mock_dialogs()
        with p1, p2:
            w.delete_current_card()

        assert 1 not in [c[0] for c in w.cards_due]
        conn.close()
        w.close()

    def test_delete_card_by_id_helper(self):
        """_delete_card_by_id: DB row gone, review state + paused cleared."""
        conn = self._db(1, 2)
        w = self._win(
            conn=conn,
            card=(1, "c1", "b1", 1),
            due=[(1, "c1", "b1", 1), (2, "c2", "b2", 1)],
            mode="daily",
            paused_card=(1, "c1", "b1", 1),
            paused_mode="daily",
        )
        # In-memory helper DB is not a sentence catalog; avoid sense purge path.
        w._db_type = None
        w._save_settings = lambda: None

        returned = w._delete_card_by_id(1)

        # DB row deleted
        assert conn.execute("SELECT id FROM cards WHERE id=1").fetchone() is None
        # Card 2 still exists
        assert conn.execute("SELECT id FROM cards WHERE id=2").fetchone() is not None
        # Review state cleaned: not in cards_due, not current
        assert 1 not in [c[0] for c in w.cards_due]
        assert w.current_card is None
        # Paused state cleared
        assert w._paused_review_card is None
        assert w._paused_review_mode == ""
        # Returns the integer id
        assert returned == 1
        conn.close()
        w.close()

    def test_delete_sentence_card_purges_senses_and_resyncs_wp(self, tmp_path):
        """R2-1: sentence delete purges orphan senses and re-derives W/P."""
        from kgb_srs.catalog import DatabaseType, write_database_type
        from kgb_srs.db import init_db
        from kgb_srs.schema import insert_sentence_card
        from kgb_srs.senses import (
            ensure_linked_word_phrase_database,
            get_sense,
        )

        db_root = tmp_path / "db"
        sent_dir = db_root / "Language-based" / "Sentence-based"
        sent_dir.mkdir(parents=True)
        sentence_path = str(sent_dir / "English_barsky.db")

        conn = init_db(sentence_path)
        write_database_type(conn, DatabaseType.LANGUAGE_SENTENCE)
        card_id = insert_sentence_card(
            conn,
            "He insists on speaking himself.",
            [("insist on", "to demand firmly")],
        )
        sense_id = conn.execute(
            "SELECT sense_id FROM unfamiliar_items WHERE card_id=?",
            (card_id,),
        ).fetchone()[0]
        assert get_sense(conn, sense_id) is not None

        wp_path, stats = ensure_linked_word_phrase_database(
            conn, sentence_path, str(db_root), sync=True
        )
        assert stats is not None
        assert stats["expressions"] == 1
        wp = init_db(wp_path)
        try:
            fronts = {
                r[0].lower() for r in wp.execute("SELECT front FROM cards").fetchall()
            }
            assert fronts == {"insist on"}
        finally:
            wp.close()

        w = self._win(
            conn=conn,
            card=(card_id, "He insists on speaking himself.", "", 1),
            due=[(card_id, "He insists on speaking himself.", "", 1)],
            mode="daily",
        )
        w._db_type = DatabaseType.LANGUAGE_SENTENCE
        w.current_db_path = sentence_path
        w.settings = dict(w.settings)
        w.settings["database_root"] = str(db_root)
        # closeEvent saves settings; keep tests from polluting the real file.
        w._save_settings = lambda: None
        w._daily_review_history = [
            ReviewHistoryEntry(
                (card_id, "He insists on speaking himself.", "", 1), "graded"
            )
        ]
        w._daily_queue_snapshot = list(w.cards_due)
        w._paused_cards_due = list(w.cards_due)
        w._paused_daily_queue = list(w.cards_due)
        w._paused_review_history = list(w._daily_review_history)

        w._delete_card_by_id(card_id)

        assert (
            conn.execute("SELECT id FROM cards WHERE id=?", (card_id,)).fetchone()
            is None
        )
        assert get_sense(conn, sense_id) is None
        assert conn.execute("SELECT COUNT(*) FROM expression_senses").fetchone()[0] == 0
        assert w._daily_review_history == []
        assert w._daily_queue_snapshot == []
        assert w._paused_cards_due == []
        assert w._paused_daily_queue == []
        assert w._paused_review_history == []

        wp = init_db(wp_path)
        try:
            fronts = [r[0] for r in wp.execute("SELECT front FROM cards").fetchall()]
            assert fronts == []
        finally:
            wp.close()
        conn.close()
        w.close()

    def test_previous_daily_skips_deleted_history_entries(self):
        """R2-1: previous daily skips ghost history when card was deleted."""
        from kgb_srs.schema import ensure_unfamiliar_items_table

        conn = self._db(1, 2, 3)
        ensure_unfamiliar_items_table(conn)
        w = self._win(
            conn=conn,
            card=(3, "c3", "b3", 1),
            due=[],
            mode="daily",
        )
        w._db_type = None  # knowledge-style path: no expression fetch
        w._daily_review_history = [
            ReviewHistoryEntry((1, "c1", "b1", 2), "graded"),
            ReviewHistoryEntry((2, "c2", "b2", 2), "graded"),
        ]
        # Delete the most recent graded card out from under history.
        conn.execute("DELETE FROM cards WHERE id=2")
        conn.commit()

        # Avoid full graphics path; we only care about history/current selection.
        w.draw_card_ui = lambda: None
        w.card_ui = None

        w._previous_daily_card()

        assert w.current_card is not None
        assert w.current_card[0] == 1
        assert w.current_card[3] == 1  # re-fetched from DB
        assert [c[0] for c in w.cards_due] == [3]
        assert w._daily_review_history == []
        conn.close()
        w.close()

    # -- widget sanity ----------------------------------------------------

    def test_widget_labels_and_existence(self):
        """Verify delete_entry_btn label, close_review_btn type, no legacy."""
        _qt_app()
        from PyQt6.QtWidgets import QPushButton
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()

        assert w.delete_entry_btn.text().strip() == "Delete Entry"
        assert isinstance(w.close_review_btn, QPushButton)
        assert not hasattr(w, "delete_current_btn"), "legacy widget removed"
        w.close()

    # -- card geometry ------------------------------------------------------

    def _draw_card_at(self, w, scene_w, scene_h, zone_y):
        """Helper: resize scene, draw card, return (width, centre_x)."""
        w.scene.clear()
        w.card_ui = None
        w.scene.setSceneRect(0, 0, scene_w, scene_h)
        w._zone_y = zone_y
        w.current_card = (1, "front", "back", 1)
        w.draw_card_ui()
        card = w.card_ui
        assert card is not None, "card_ui not created"
        return card.rect().width(), card.pos().x()

    def test_review_card_occupies_90_percent_scene_width(self):
        """Card width ≈ 90 % of scene width, centred, contained, and wider
        scenes produce wider cards."""
        _qt_app()
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        try:
            cw_narrow, cx_narrow = self._draw_card_at(w, 800, 600, 500)
            cw_wide, cx_wide = self._draw_card_at(w, 1200, 800, 700)

            # approximate 90 % width
            assert cw_narrow == pytest.approx(720, abs=5), (
                f"Expected ~720 (90 % of 800), got {cw_narrow}"
            )
            assert cw_wide == pytest.approx(1080, abs=5), (
                f"Expected ~1080 (90 % of 1200), got {cw_wide}"
            )
            # wider scene → wider card
            assert cw_wide > cw_narrow, f"Card should grow: {cw_wide} not > {cw_narrow}"
            # centred
            assert cx_narrow == pytest.approx(400, abs=1), (
                f"Card not centred at 800/2, got {cx_narrow}"
            )
            assert cx_wide == pytest.approx(600, abs=1), (
                f"Card not centred at 1200/2, got {cx_wide}"
            )
            # contained within scene bounds
            for cw, cx, limit in [
                (cw_narrow, cx_narrow, 800),
                (cw_wide, cx_wide, 1200),
            ]:
                left = cx - cw / 2
                right = cx + cw / 2
                assert left >= 0, f"Card left edge outside scene ({left})"
                assert right <= limit, f"Card right edge outside scene ({right})"
        finally:
            w.close()

    # -- close button visual contract --------------------------------------

    def test_close_button_is_compact_x_overlay_on_canvas(self):
        """Compact '×' overlay at top-right of QGraphicsView with 6 px
        margin; clicking it closes the active review."""
        _qt_app()
        from PyQt6.QtWidgets import QPushButton, QApplication
        from PyQt6.QtCore import Qt
        from kgb_srs.main_window import BarskyApp

        w = BarskyApp()
        w.resize(900, 700)
        w.show()
        QApplication.processEvents()

        btn = w.close_review_btn

        # Visual contract
        assert isinstance(btn, QPushButton)
        assert btn.parent() is w.view
        assert btn.text().strip() == "×"
        assert btn.toolTip() == "Close review (Alt+X)"
        assert btn.accessibleName() == "Close review"
        assert btn.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert (btn.width(), btn.height()) == (28, 28)

        # 6 px top-right anchoring — after show
        assert btn.x() + btn.width() == w.view.width() - 6
        assert btn.y() == 6

        # 6 px top-right anchoring — after resize
        w.resize(600, 400)
        QApplication.processEvents()
        assert btn.x() + btn.width() == w.view.width() - 6
        assert btn.y() == 6

        # Signal wiring: clicking closes the review
        conn = self._db(1)
        w.conn = conn
        w.current_card = (1, "c1", "b1", 1)
        w.review_mode = "daily"
        w._update_button_visibility()
        assert btn.isEnabled()

        btn.click()

        assert not btn.isEnabled()
        assert w.current_card is None
        assert w.review_mode == ""

        conn.close()
        w.close()
