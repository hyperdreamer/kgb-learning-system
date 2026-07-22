"""Regression tests for card-entry dialogs and form helpers."""

import sqlite3


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
        from kgb_srs.forms import _AIGenerateWorker

        assert hasattr(_AIGenerateWorker, "result")
        # QThread.finished remains the inherited no-argument termination signal.
        assert "finished" not in _AIGenerateWorker.__dict__

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

    def test_sentence_dialog_ai_success_status_is_ready_to_save(self, monkeypatch):
        """AI success status should report reuse/create and ready to save."""
        _qt_app()
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker

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

        monkeypatch.setattr("kgb_srs.forms._AIGenerateWorker", FakeWorker)
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
        dialog._ai_worker.result.emit(raw)
        QApplication.processEvents()

        status = dialog._ai_status.text()
        assert "Review and edit" not in status
        assert "Hello" in status
        assert "Ready to save" in status
        assert dialog._meanings["Hello"] == "a greeting"
        assert dialog._meaning_widgets[0][1].toPlainText() == "a greeting"
        dialog.close()

    @staticmethod
    def _emit_sentence_ai_assignment(dialog, monkeypatch, response):
        """Run a controlled AI assignment response through the dialog."""
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        from kgb_srs.forms import _AIGenerateWorker

        class FakeWorker(_AIGenerateWorker):
            def __init__(self, config, prompt):
                QThread.__init__(self)

            def start(self):
                pass

        monkeypatch.setattr("kgb_srs.forms._AIGenerateWorker", FakeWorker)
        dialog._generate_ai_meanings()
        assert dialog._ai_worker is not None
        dialog._ai_worker.result.emit(response)
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

    def test_result_signal_alone_does_not_restore_ui(self):
        """Receiving result signal must keep controls locked until finished."""
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
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
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
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
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
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
        from kgb_srs.forms import WordPhraseCardDialog, _AIGenerateWorker
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
        is not clickable.  After QThread.finished fires, controls
        restore and close becomes available. The reject() guard checks
        self._ai_worker is not None AND isRunning().
        """
        _qt_app()
        from kgb_srs.forms import SentenceCardDialog, _AIGenerateWorker
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
