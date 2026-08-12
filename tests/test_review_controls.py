"""Tests for review-control state machine in BarskyApp.

TDD suite — these tests are written BEFORE the implementation changes.
They MUST fail (RED) against the current codebase, then pass (GREEN)
after the review-control state machine is implemented.

State machine summary
---------------------
IDLE (no active review):
  - primary button:  "Start Daily Review" (or "Resume Daily Review" if paused)
  - Previous:        disabled / faded
  - Restart:          disabled / faded
  - Close:            unavailable / disabled
  - force_seq_btn:    REMOVED entirely

ACTIVE (daily review in progress):
  - primary button:  "Next"
  - Previous:        enabled
  - Restart:          enabled
  - Close:            available / enabled

Transitions:
  idle  --[Start/Resume]--> active
  active --[Close]--------> idle (paused session preserved)
  active --[queue empty]--> idle
  idle  --[Resume]--------> active (paused card first)
"""

import datetime
import os
import tempfile
import pytest

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton


from kgb_srs.main_window import BarskyApp
from kgb_srs.review_controller import ReviewHistoryEntry
from kgb_srs.schema import init_db, ensure_unfamiliar_items_table
from kgb_srs.catalog import DatabaseType, write_database_type


@pytest.fixture(autouse=True)
def _dismiss_message_boxes(monkeypatch):
    """Prevent modal dialogs from blocking the headless review-control tests."""
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_temp_sentence_db():
    """Create a temporary language-sentence database with known test cards.

    Returns (path, conn).  Caller is responsible for cleanup.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = init_db(path)
    write_database_type(conn, DatabaseType.LANGUAGE_SENTENCE)
    ensure_unfamiliar_items_table(conn)

    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()

    cards = [
        ("Hello world", "A greeting", 1, yesterday),  # due
        ("Goodbye world", "A farewell", 2, today),  # due
        ("Future card", "Not due yet", 1, future),  # NOT due
        ("Greetings earth", "Another greeting", 3, yesterday),  # due
    ]

    for front, back, box, next_review in cards:
        conn.execute(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
            (front, back, box, next_review),
        )
    conn.commit()

    return path, conn


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def qapp():
    """Ensure a QApplication exists for the test session."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def app_with_db(qapp):
    """Create a BarskyApp loaded with a temporary test database.

    Yields (app, db_path, conn).  Cleans up after the test.
    """
    db_path, conn = _make_temp_sentence_db()

    app = BarskyApp()
    app.current_db_path = db_path
    app.current_lang = "test"
    app.load_database(silent=True)

    yield app, db_path, conn

    # Cleanup
    if app.conn:
        app.conn.close()
    app.close()
    app.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    conn.close()
    os.unlink(db_path)


# ═══════════════════════════════════════════════════════════════════════
# RED tests — these MUST fail against the current codebase.
# ═══════════════════════════════════════════════════════════════════════


class TestForceSeqBtnRemoved:
    """The separate 'Next' forced-review button must not exist."""

    def test_force_seq_btn_does_not_exist(self, app_with_db):
        app, _, _ = app_with_db
        assert not hasattr(app, "force_seq_btn"), (
            "force_seq_btn must be removed from the UI entirely "
            "(its function is merged into the primary button)"
        )


class TestIdleStateButtons:
    """In IDLE state (DB loaded, no active review)."""

    def test_start_label_without_paused_session(self, app_with_db):
        """No paused session → primary button says 'Start Daily Review'."""
        app, _, _ = app_with_db
        app._paused_review_card = None
        app._paused_review_mode = ""
        app._update_button_visibility()
        assert app.review_mode == ""
        assert "Start Daily Review" in app.start_btn.text(), (
            "Primary button must show 'Start Daily Review' when idle "
            "with no paused session"
        )

    def test_start_label_with_paused_session(self, app_with_db):
        """Paused session exists → primary button says 'Resume Daily Review'."""
        app, _, _ = app_with_db
        app._paused_review_card = (1, "test", "back", 1)
        app._paused_review_mode = "daily"
        app._update_button_visibility()
        assert "Resume Daily Review" in app.start_btn.text(), (
            "Primary button must show 'Resume Daily Review' when a "
            "paused daily session exists"
        )

    def test_start_label_with_completed_paused_session(self, app_with_db):
        """Retained completed-session history is resumable."""
        app, _, _ = app_with_db
        app._paused_review_card = None
        app._paused_review_mode = "daily"
        app._paused_review_history = [
            ReviewHistoryEntry((1, "test", "back", 1), "graded")
        ]
        app._update_button_visibility()
        assert "Resume Daily Review" in app.start_btn.text()

    def test_restart_disabled_in_idle(self, app_with_db):
        app, _, _ = app_with_db
        assert app.review_mode == ""
        assert not app.restart_review_btn.isEnabled(), (
            "Restart must be disabled in idle state"
        )

    def test_previous_disabled_in_idle(self, app_with_db):
        app, _, _ = app_with_db
        assert app.review_mode == ""
        assert not app.previous_review_btn.isEnabled(), (
            "Previous must be disabled in idle state"
        )

    def test_close_disabled_in_idle(self, app_with_db):
        app, _, _ = app_with_db
        assert app.review_mode == ""
        assert not app.close_review_btn.isEnabled(), (
            "Close must be disabled in idle state"
        )

    def test_close_button_hidden_in_idle(self, app_with_db):
        """The floating close button must not show as a faint artifact."""
        app, _, _ = app_with_db
        assert app.review_mode == ""
        assert not app.close_review_btn.isVisibleTo(app.view), (
            "Close button must be hidden in idle state"
        )
        app.close_review_btn.show()  # defensive: ensure state is driven, not luck
        app._update_button_visibility()
        assert not app.close_review_btn.isVisibleTo(app.view)

    def test_start_enabled_in_idle(self, app_with_db):
        app, _, _ = app_with_db
        assert app.start_btn.isEnabled(), (
            "Primary button must be enabled in idle (DB loaded)"
        )


class TestActiveStateButtons:
    """In ACTIVE state (daily review in progress)."""

    def test_start_becomes_next_label(self, app_with_db):
        """During daily review, the primary button label changes to 'Next'."""
        app, _, _ = app_with_db
        app.start_review()
        assert app.review_mode == "daily"
        assert app.current_card is not None
        assert "Next" in app.start_btn.text(), (
            "Primary button must show 'Next' during active daily review"
        )

    def test_restart_enabled_in_active(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        assert app.review_mode == "daily"
        assert app.restart_review_btn.isEnabled(), (
            "Restart must be enabled during active daily review"
        )

    def test_previous_enabled_in_active(self, app_with_db):
        """Previous enables after Next or grade (session path non-empty)."""
        app, _, _ = app_with_db
        app.start_review()
        assert app.review_mode == "daily"
        first_id = app.current_card[0]
        assert not app.previous_review_btn.isEnabled(), (
            "Previous must stay disabled on the first card (no prior step)"
        )
        # Next alone must enable Previous (reverse of Next — no grade needed).
        app._advance_daily_queue()
        assert app.previous_review_btn.isEnabled(), (
            "Previous must enable after Next advances the session path"
        )
        assert app.current_card is not None
        assert app.current_card[0] != first_id or len(app._daily_review_history) >= 1
        app._previous_daily_card()
        assert app.current_card is not None
        assert app.current_card[0] == first_id

    def test_close_enabled_in_active(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        assert app.review_mode == "daily"
        assert app.close_review_btn.isEnabled(), (
            "Close must be enabled during active daily review"
        )

    def test_close_button_visible_in_active(self, app_with_db):
        """The floating close button is visible exactly while review is active."""
        app, _, _ = app_with_db
        app.start_review()
        assert app.review_mode == "daily"
        assert app.close_review_btn.isVisibleTo(app.view), (
            "Close button must be visible during active daily review"
        )
        app.close_review()
        assert app.review_mode == ""
        assert not app.close_review_btn.isVisibleTo(app.view), (
            "Close button must hide again when the review is closed"
        )


class TestSentenceReviewEditButton:
    """Sentence reviewers can edit the card currently being reviewed."""

    def test_click_opens_current_sentence_card_for_editing(
        self, app_with_db, monkeypatch
    ):
        app, _, _ = app_with_db
        app.start_review()
        card_id = app.current_card[0]
        edited_card_ids = []
        monkeypatch.setattr(
            app,
            "_add_sentence_card",
            lambda *, edit_card_id: edited_card_ids.append(edit_card_id),
        )

        assert app.edit_review_btn.text().strip() == "Edit"
        assert app.edit_review_btn.isEnabled()
        app.edit_review_btn.click()

        assert edited_card_ids == [card_id]

    def test_is_available_only_while_a_sentence_card_is_under_review(self, app_with_db):
        app, _, _ = app_with_db

        assert app.edit_review_btn.isHidden()
        assert not app.edit_review_btn.isEnabled()

        app.start_review()

        assert not app.edit_review_btn.isHidden()
        assert app.edit_review_btn.isEnabled()

        app.close_review()

        assert app.edit_review_btn.isHidden()
        assert not app.edit_review_btn.isEnabled()


class TestStartReviewDispatch:
    """Primary button dispatches correctly based on state."""

    def test_click_in_idle_starts_daily_review(self, app_with_db):
        """Clicking primary button in idle starts a daily review."""
        app, _, _ = app_with_db
        assert app.review_mode == ""
        app._on_primary_button_clicked()
        assert app.review_mode == "daily", (
            "Clicking primary button in idle must start daily review"
        )
        assert "Next" in app.start_btn.text()

    def test_click_in_active_advances_daily_queue(self, app_with_db):
        """Clicking 'Next' during active review advances within the daily queue."""
        app, _, _ = app_with_db
        app.start_review()
        first_card_id = app.current_card[0]

        # Click Next (skip current card) — call dispatch directly
        app._on_primary_button_clicked()

        assert app.review_mode == "daily", "Must remain in daily review mode after Next"
        assert "Next" in app.start_btn.text(), (
            "Button must still show 'Next' during active review"
        )
        # The skipped card should now be at the end of the queue
        queued_ids = [c[0] for c in app.cards_due]
        assert first_card_id in queued_ids, (
            "Skipped card must return to the queue so it can be reviewed later"
        )


class TestCloseTransition:
    """Closing a review preserves state and returns to idle."""

    def test_close_saves_paused_card(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        paused_card = app.current_card
        assert paused_card is not None

        app.close_review()

        assert app.review_mode == "", "Review mode cleared after close"
        assert app.current_card is None, "No current card after close"
        assert app._paused_review_card is not None, "Paused card saved"
        assert app._paused_review_card[0] == paused_card[0], (
            "Paused card ID must match the card that was showing"
        )
        assert app._paused_review_mode == "daily", "Paused mode must be 'daily'"

    def test_close_returns_to_idle_with_resume_label(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        app.close_review()

        assert "Resume Daily Review" in app.start_btn.text(), (
            "After close, label must be 'Resume Daily Review' (paused session)"
        )
        assert not app.restart_review_btn.isEnabled()
        assert not app.previous_review_btn.isEnabled()
        assert not app.close_review_btn.isEnabled()

    def test_start_label_without_paused_after_clear(self, app_with_db):
        """When paused state is cleared, label returns to 'Start Daily Review'."""
        app, _, _ = app_with_db
        app._paused_review_card = None
        app._paused_review_mode = ""
        app._update_button_visibility()
        assert "Start Daily Review" in app.start_btn.text()


class TestResumeTransition:
    """Resuming a paused session restores state correctly."""

    def test_resume_shows_paused_card(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        paused_id = app.current_card[0]
        app.close_review()

        # Resume
        app.start_review()

        assert app.review_mode == "daily"
        assert app.current_card is not None
        assert app.current_card[0] == paused_id, (
            "Resumed card must be the same card that was paused"
        )
        assert "Next" in app.start_btn.text(), (
            "After resume, button must return to 'Next'"
        )

    def test_resume_clears_paused_state(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        app.close_review()
        assert app._paused_review_card is not None

        app.start_review()

        assert app._paused_review_card is None, "Paused card cleared"
        assert app._paused_review_mode == "", "Paused mode cleared"

    def test_resume_then_close_again(self, app_with_db):
        """Multiple close/resume cycles should preserve state each time."""
        app, _, _ = app_with_db
        app.start_review()
        first_id = app.current_card[0]

        # Cycle 1: close + resume
        app.close_review()
        app.start_review()
        assert app.current_card[0] == first_id

        # Grade one card
        app.is_current_flipped = True
        app.process_answer(correct=True)
        second_id = app.current_card[0] if app.current_card else None

        if second_id is not None:
            app.close_review()
            app.start_review()
            assert app.current_card[0] == second_id, (
                "Second resume must show the card that was active when paused"
            )


class TestQueueCompletion:
    """When the daily queue is exhausted, keep session active for Previous."""

    def test_all_cards_reviewed_keeps_session_for_previous(self, app_with_db):
        app, db_path, conn = app_with_db

        today = datetime.date.today().isoformat()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cards WHERE next_review <= ?", (today,))
        due_count = cur.fetchone()[0]
        assert due_count > 0, "Test DB must have due cards"

        app.start_review()

        # Grade every card
        for _ in range(due_count):
            if app.current_card is None:
                break
            app.is_current_flipped = True
            app.process_answer(correct=True)

        # Queue empty, but daily mode + history stay so Previous can restore.
        assert app.current_card is None
        assert app.review_mode == "daily", (
            "Review mode must stay daily after finishing so Previous works"
        )
        assert app._daily_review_history, "History must survive final grade"
        assert app.previous_review_btn.isEnabled()
        assert "Next" in app.start_btn.text()
        # Explicit Close preserves the completed session for resumption.
        app.close_review()
        assert app.review_mode == ""
        assert "Resume Daily Review" in app.start_btn.text()


class TestRestartDailyReview:
    """Restart resets the current daily session."""

    def test_restart_resets_queue_and_clears_history(self, app_with_db):
        app, db_path, conn = app_with_db
        app.start_review()

        # Save the first card ID for later verification.
        app.current_card[0]

        # Grade a card (it moves to history, queue shrinks).
        app.is_current_flipped = True
        app.process_answer(correct=True)
        assert app.review_mode == "daily"
        assert len(app._daily_review_history) >= 1

        # Restart — call the method directly (button .click() is async in Qt).
        app._restart_daily_review()

        assert app.review_mode == "daily", "Must remain in daily mode after restart"
        assert app.current_card is not None, "Must show a card after restart"
        assert len(app._daily_review_history) == 0, (
            "Review history must be cleared on restart"
        )
        assert "Next" in app.start_btn.text(), "Button must show 'Next'"

    def test_restart_only_works_during_active_review(self, app_with_db):
        """Restart button is disabled in idle; calling it directly is a no-op."""
        app, _, _ = app_with_db
        # In idle state, restart should be disabled
        assert not app.restart_review_btn.isEnabled()
        # Call directly — should be a no-op, no crash, no state change.
        app._restart_daily_review()
        assert app.review_mode == "", "No review should be started from idle restart"


class TestNoDatabaseLoaded:
    """All review controls are disabled when no database is open."""

    def test_buttons_disabled_without_db(self, qapp):
        app = BarskyApp()
        assert app.conn is None

        assert not app.start_btn.isEnabled(), "Start disabled without DB"
        assert not app.restart_review_btn.isEnabled(), "Restart disabled without DB"
        assert not app.previous_review_btn.isEnabled(), "Previous disabled without DB"
        assert not app.close_review_btn.isEnabled(), "Close disabled without DB"

        app.close()
        app.deleteLater()


class TestDatabaseLoadFailure:
    def test_corrupt_candidate_load_preserves_active_review_state(
        self, app_with_db, tmp_path
    ):
        app, active_path, _ = app_with_db
        app.start_review()
        assert app.current_card is not None
        assert app.card_ui is not None
        active_conn = app.conn
        active_card = app.current_card
        active_queue = app.cards_due
        active_card_ui = app.card_ui

        corrupt_db = tmp_path / "corrupt.db"
        corrupt_db.write_bytes(b"not a sqlite database")
        app.load_database(
            silent=True, db_path=str(corrupt_db), display="Knowledge-based/Corrupt"
        )

        assert app.conn is active_conn
        assert app.current_db_path == active_path
        assert app.current_card is active_card
        assert app.cards_due is active_queue
        assert app.review_mode == "daily"
        assert app.card_ui is active_card_ui
        assert app.start_btn.isEnabled()
        assert app.random_checkbox.isEnabled()
        assert app.all_cards_checkbox.isEnabled()

        # The old connection remains usable after candidate validation fails.
        assert app.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] > 0


class TestCloseEventSettingsFailure:
    def test_settings_save_oserror_does_not_interrupt_cleanup(self, qapp, caplog):
        app = BarskyApp()
        cleanup_calls = []
        close_requests = []

        def fail_save():
            raise OSError("disk full")

        class Worker:
            running = True

            def isRunning(self):
                return self.running

        class Event:
            accepted = False
            ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        app._save_settings = fail_save
        app._cleanup_tts_temp = lambda: cleanup_calls.append(True)
        worker = Worker()
        app.tts_worker = worker
        event = Event()
        app.closeEvent(event)

        assert event.ignored
        assert app.tts_worker is worker
        assert cleanup_calls == []

        app.close = lambda: close_requests.append(True)
        worker.running = False
        app._on_tts_worker_finished(worker)

        assert app.tts_worker is None
        assert close_requests == [True]

        final_event = Event()
        app.closeEvent(final_event)
        assert final_event.accepted
        assert cleanup_calls == [True]
        assert "Could not save settings: disk full" in caplog.text

        app._save_settings = lambda: None
        app.close()
        app.deleteLater()

    def test_active_tts_close_defers_until_finished_and_silences_late_audio(
        self, qapp, tmp_path, monkeypatch
    ):
        """A close keeps its worker alive, but never presents late TTS output."""
        from PyQt6.QtCore import QObject, pyqtSignal
        import kgb_srs.main_window as main_window
        import kgb_srs.tts as tts

        late_audio = tmp_path / "barsky_tts_late_close.mp3"
        late_audio.write_bytes(b"late")

        class Worker(QObject):
            audio_ready = pyqtSignal(str)
            error = pyqtSignal(str)
            finished = pyqtSignal()
            instance = None

            def __init__(self, *_args):
                super().__init__()
                self.running = True
                Worker.instance = self

            def start(self):
                return None

            def isRunning(self):
                return self.running

            def deleteLater(self):
                return None

        class Player:
            set_source_calls = 0
            play_calls = 0

            def setSource(self, *_args):
                self.set_source_calls += 1

            def play(self):
                self.play_calls += 1

        class Event:
            accepted = False
            ignored = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                self.ignored = True

        monkeypatch.setattr(main_window, "TTSWorker", Worker)
        unlinked = []
        monkeypatch.setattr(
            tts,
            "unlink_tts_temp",
            lambda path: unlinked.append(path) or None,
        )

        app = BarskyApp()
        app._save_settings = lambda: None
        app.player = Player()
        close_requests = []
        app.close = lambda: close_requests.append(True)
        cleanup_calls = []
        app._cleanup_tts_temp = lambda: cleanup_calls.append(True)
        btn = QPushButton("Listen")

        app.speak_text("hello", btn)
        cleanup_calls.clear()  # setup cleanup is unrelated to window closing
        worker = Worker.instance
        event = Event()
        app.closeEvent(event)

        assert event.ignored
        assert app.tts_worker is worker  # no early identity drop / no wait()

        worker.audio_ready.emit(str(late_audio))
        worker.error.emit("late failure")
        assert unlinked == [str(late_audio)]
        assert app.player.set_source_calls == 0
        assert app.player.play_calls == 0

        worker.running = False
        worker.finished.emit()
        worker.finished.emit()  # stale/duplicate completion cannot re-close
        assert app.tts_worker is None
        assert close_requests == [True]

        final_event = Event()
        app.closeEvent(final_event)
        assert final_event.accepted
        assert cleanup_calls == [True]
        btn.deleteLater()
        app.deleteLater()


class TestDailyReviewHistoryTracking:
    """The daily review session must track viewed cards for Previous navigation."""

    def test_history_attribute_exists(self, app_with_db):
        """_daily_review_history attribute must exist on the app."""
        app, _, _ = app_with_db
        assert hasattr(app, "_daily_review_history"), (
            "BarskyApp must have a _daily_review_history attribute for "
            "tracking visited cards in the daily session"
        )

    def test_history_starts_empty(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        assert app._daily_review_history == [], (
            "Session path must be empty when daily review starts"
        )

    def test_next_adds_to_session_path(self, app_with_db):
        """Next (skip) must push the current card onto the session path."""
        app, _, _ = app_with_db
        app.start_review()
        first = app.current_card
        app._advance_daily_queue()
        assert len(app._daily_review_history) == 1
        assert app._daily_review_history[0].card[0] == first[0]
        assert app.previous_review_btn.isEnabled()

    def test_grading_adds_to_history(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        graded_card = app.current_card

        app.is_current_flipped = True
        app.process_answer(correct=True)

        assert len(app._daily_review_history) == 1, (
            "Grading a card must add it to the session path"
        )
        assert app._daily_review_history[0].card[0] == graded_card[0], (
            "History must contain the graded card"
        )


class TestReviewNavigationProvenance:
    """Previous restores whether a card was skipped or already graded."""

    def test_grade_previous_next_does_not_requeue_restored_graded_card(
        self, app_with_db
    ):
        app, _, conn = app_with_db
        app.start_review()
        graded_id = app.current_card[0]
        box_before = app.current_card[3]

        app.is_current_flipped = True
        app.process_answer(correct=True)
        next_id = app.current_card[0]
        app._previous_daily_card()
        assert app.current_card[0] == graded_id

        app._advance_daily_queue()

        assert app.current_card[0] == next_id
        assert all(card[0] != graded_id for card in app.cards_due)
        assert conn.execute(
            "SELECT box FROM cards WHERE id = ?", (graded_id,)
        ).fetchone()[0] == min(box_before + 1, 5)

    def test_grade_last_previous_next_does_not_show_graded_card_again(
        self, app_with_db
    ):
        app, _, _ = app_with_db
        app.start_review()
        graded_id = app.current_card[0]
        app.cards_due = []

        app.is_current_flipped = True
        app.process_answer(correct=True)
        assert app.current_card is None

        app._previous_daily_card()
        assert app.current_card[0] == graded_id

        app._advance_daily_queue()

        assert app.current_card is None
        assert all(card[0] != graded_id for card in app.cards_due)

    def test_skip_previous_next_requeues_restored_ungraded_card(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        skipped_id = app.current_card[0]

        app._advance_daily_queue()
        next_id = app.current_card[0]
        app._previous_daily_card()
        assert app.current_card[0] == skipped_id

        app._advance_daily_queue()

        assert app.current_card[0] == next_id
        assert app.cards_due[-1][0] == skipped_id

    def test_pause_resume_preserves_restored_graded_card_provenance(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        graded_id = app.current_card[0]

        app.is_current_flipped = True
        app.process_answer(correct=True)
        next_id = app.current_card[0]
        app._previous_daily_card()
        assert app.current_card[0] == graded_id

        app.close_review()
        app.start_review()
        assert app.current_card[0] == graded_id

        app._advance_daily_queue()

        assert app.current_card[0] == next_id
        assert all(card[0] != graded_id for card in app.cards_due)


class TestQueueSnapshot:
    """A snapshot of the original due queue must be saved for Restart."""

    def test_snapshot_attribute_exists(self, app_with_db):
        app, _, _ = app_with_db
        assert hasattr(app, "_daily_queue_snapshot"), (
            "BarskyApp must have a _daily_queue_snapshot attribute"
        )

    def test_snapshot_captures_full_queue_on_start(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        snapshot = app._daily_queue_snapshot
        remaining = app.cards_due

        # Snapshot should have at least as many cards as the current queue
        assert len(snapshot) >= len(remaining), (
            "Snapshot must capture the full original due queue"
        )


class TestProcessAnswerFreshBox:
    """FIX 3/4/11: grade from DB box, require flip, store fresh history."""

    def test_requires_flip_before_grading(self, app_with_db):
        app, _, conn = app_with_db
        app.start_review()
        assert app.current_card is not None
        card_id = app.current_card[0]
        cur = conn.cursor()
        cur.execute("SELECT box FROM cards WHERE id=?", (card_id,))
        box_before = cur.fetchone()[0]
        app.is_current_flipped = False

        app.process_answer(correct=True)

        cur.execute("SELECT box FROM cards WHERE id=?", (card_id,))
        assert cur.fetchone()[0] == box_before
        assert app.current_card[0] == card_id
        assert app._daily_review_history == []

    def test_grades_from_db_box_not_stale_tuple(self, app_with_db):
        app, _, conn = app_with_db
        app.start_review()
        card_id, front, back, _ = app.current_card

        # DB is already at a higher box than the in-memory tuple.
        conn.execute(
            "UPDATE cards SET box=3, next_review=? WHERE id=?",
            (__import__("datetime").date.today().isoformat(), card_id),
        )
        conn.commit()
        app.current_card = (card_id, front, back, 1)  # stale memory
        app.is_current_flipped = True

        app.process_answer(correct=True)

        cur = conn.cursor()
        cur.execute("SELECT box FROM cards WHERE id=?", (card_id,))
        assert cur.fetchone()[0] == 4
        assert app._daily_review_history[-1] == ReviewHistoryEntry(
            (card_id, front, back, 4), "graded"
        )

    def test_previous_refetches_box_from_db(self, app_with_db):
        app, _, conn = app_with_db
        app.start_review()
        app.is_current_flipped = True
        app.process_answer(correct=True)
        assert app._daily_review_history

        # Mutate DB box after history was written with fresh tuple.
        graded_id = app._daily_review_history[-1].card[0]
        conn.execute("UPDATE cards SET box=5 WHERE id=?", (graded_id,))
        conn.commit()

        app._previous_daily_card()
        assert app.current_card is not None
        assert app.current_card[0] == graded_id
        assert app.current_card[3] == 5

    def test_previous_works_after_final_grade(self, app_with_db):
        """Grading the last due card must not wipe history / kill Previous."""
        app, _, conn = app_with_db
        app.start_review()
        graded_ids = []
        # Grade every due card until the queue is empty.
        while app.current_card is not None and app.review_mode == "daily":
            graded_ids.append(app.current_card[0])
            app.is_current_flipped = True
            app.process_answer(correct=True)
            # Guard against infinite loop if something breaks.
            if len(graded_ids) > 20:
                break

        assert graded_ids, "Expected at least one graded card"
        assert app.review_mode == "daily", (
            "Session must stay active after finishing the queue"
        )
        assert app._daily_review_history, (
            "History must survive the final grade so Previous works"
        )
        assert app.previous_review_btn.isEnabled()

        last_graded = graded_ids[-1]
        app._previous_daily_card()
        assert app.current_card is not None
        assert app.current_card[0] == last_graded

    def test_previous_is_reverse_of_next(self, app_with_db):
        """Next then Previous returns to the same card (sequence reverse)."""
        app, _, _ = app_with_db
        app.start_review()
        first = app.current_card
        assert first is not None
        app._on_primary_button_clicked()  # Next
        second = app.current_card
        assert second is not None
        assert app.previous_review_btn.isEnabled()
        app._previous_daily_card()
        assert app.current_card is not None
        assert app.current_card[0] == first[0]
        # One more Next should get back to the card we left.
        app._on_primary_button_clicked()
        assert app.current_card is not None
        assert app.current_card[0] == second[0]

    def test_previous_disabled_before_any_grade(self, app_with_db):
        """On the first card, Previous is disabled (no prior step yet)."""
        app, _, _ = app_with_db
        app.start_review()
        assert app.review_mode == "daily"
        assert app._daily_review_history == []
        assert not app.previous_review_btn.isEnabled()
        # Click path is a no-op when path is empty.
        before = app.current_card
        app._previous_daily_card()
        assert app.current_card == before


class TestResumeRestoresPausedQueue:
    """FIX 10: resume must restore _paused_cards_due explicitly."""

    def test_resume_restores_paused_cards_due(self, app_with_db):
        app, _, _ = app_with_db
        app.start_review()
        assert app.current_card is not None
        remaining = list(app.cards_due)
        app.close_review()
        assert app._paused_cards_due == remaining

        # Simulate accidental wipe of in-memory queue after close.
        app.cards_due = []
        app.start_review()
        # Current card is re-inserted at front from paused card; remaining
        # should come from the restored paused queue (minus current).
        assert app.review_mode == "daily"
        assert app.current_card is not None
        # After show_next_card, current is popped; remaining queue length
        # should match original remaining (possibly reordered only by resume).
        assert len(app.cards_due) == len(remaining)
        assert app._paused_cards_due == []


class TestAllCardsReviewMode:
    """All cards checkbox: review every entry, not only due today."""

    def test_default_due_only_excludes_future(self, app_with_db):
        app, _, conn = app_with_db
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        assert total == 4  # 3 due + 1 future from fixture

        app.all_cards_checkbox.setChecked(False)
        app.start_review()

        seen = set()
        if app.current_card is not None:
            seen.add(app.current_card[0])
        for card in app.cards_due:
            seen.add(card[0])
        # Snapshot has the full session queue at start (before first pop).
        snap_ids = {c[0] for c in app._daily_queue_snapshot}
        assert len(snap_ids) == 3
        assert len(seen) == 3

        future_id = conn.execute(
            "SELECT id FROM cards WHERE front = ?", ("Future card",)
        ).fetchone()[0]
        assert future_id not in snap_ids

    def test_all_cards_includes_future(self, app_with_db):
        app, _, conn = app_with_db
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

        app.all_cards_checkbox.setChecked(True)
        app.start_review()

        snap_ids = {c[0] for c in app._daily_queue_snapshot}
        assert len(snap_ids) == total

        future_id = conn.execute(
            "SELECT id FROM cards WHERE front = ?", ("Future card",)
        ).fetchone()[0]
        assert future_id in snap_ids

    def test_all_cards_restart_rebuilds_queue(self, app_with_db):
        """Toggling All cards then Restart re-reads the queue."""
        app, _, conn = app_with_db
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

        app.all_cards_checkbox.setChecked(False)
        app.start_review()
        assert len(app._daily_queue_snapshot) == 3

        app.all_cards_checkbox.setChecked(True)
        app._restart_daily_review()
        assert len(app._daily_queue_snapshot) == total

    def test_all_cards_mode_is_restored_after_reloading_database(self, app_with_db):
        """The selected All cards mode belongs to the active database."""
        app, db_path, _ = app_with_db

        app.all_cards_checkbox.setChecked(True)

        saved_mode = app.conn.execute(
            "SELECT value FROM settings WHERE key = 'all_cards_review'"
        ).fetchone()
        assert saved_mode == ("1",)

        app.load_database(silent=True, db_path=db_path, display="test")

        assert app.all_cards_checkbox.isChecked()

    def test_checkbox_enabled_after_db_load(self, app_with_db):
        app, _, _ = app_with_db
        assert app.all_cards_checkbox.isEnabled()
        assert not app.all_cards_checkbox.isChecked()


class TestReviewGradeGestureLifecycle:
    """Real-window coverage for explicit grading and nonvisual drag state."""

    @staticmethod
    def _start_visible_review(app):
        from PyQt6.QtWidgets import QApplication

        app.resize(900, 700)
        app.show()
        QApplication.processEvents()
        app.start_review()
        app.redraw_canvas()
        QApplication.processEvents()
        assert app.current_card is not None
        assert app.card_ui is not None

    @staticmethod
    def _assert_unrevealed_geometry(app):
        assert app.current_card is not None
        assert app.is_current_flipped is False
        assert app._grade_gesture_regions == {}
        assert app._review_card_bottom == pytest.approx(app.scene.sceneRect().bottom())
        assert app._review_card_home == pytest.approx(
            (app.card_ui.pos().x(), app.card_ui.pos().y())
        )

    @staticmethod
    def _assert_revealed_geometry(app):
        scene_rect = app.scene.sceneRect()
        regions = app._grade_gesture_regions
        assert app.current_card is not None
        assert app.is_current_flipped is True
        assert set(regions) == {"incorrect", "correct"}
        assert all(scene_rect.contains(region) for region in regions.values())
        lane_top = min(region.top() for region in regions.values())
        assert app._review_card_bottom == pytest.approx(lane_top - 20)
        assert app.card_ui.sceneBoundingRect().bottom() <= (app._review_card_bottom + 1)
        assert app._review_card_home == pytest.approx(
            (app.card_ui.pos().x(), app.card_ui.pos().y())
        )

    def test_explicit_grade_buttons_keep_guard_shortcuts_and_review_semantics(
        self, app_with_db
    ):
        """Buttons are accessible primary affordances without bypassing guards."""
        from PyQt6.QtGui import QKeySequence, QShortcut

        from kgb_srs.ui_theme import ROLE_PROPERTY

        app, _, conn = app_with_db
        self._start_visible_review(app)
        card_id = app.current_card[0]
        card = app.card_ui
        box_before = conn.execute(
            "SELECT box FROM cards WHERE id = ?", (card_id,)
        ).fetchone()[0]

        expected_buttons = {
            "incorrect_btn": (
                "incorrectBtn",
                "Incorrect",
                "danger",
                "Alt+Left",
                "Alt+1",
            ),
            "correct_btn": ("correctBtn", "Correct", "success", "Alt+Right", "Alt+2"),
        }
        for attribute, (
            name,
            label,
            role,
            first_key,
            second_key,
        ) in expected_buttons.items():
            button = getattr(card, attribute)
            assert button.objectName() == name
            assert button.text() == label
            assert button.property(ROLE_PROPERTY) == role
            assert label.lower() in button.accessibleName().lower()
            assert first_key in button.toolTip()
            assert second_key in button.toolTip()
            assert first_key in button.accessibleDescription()
            assert second_key in button.accessibleDescription()
            assert button.isHidden()

        shortcut_keys = {
            shortcut.key().toString() for shortcut in app.findChildren(QShortcut)
        }
        assert {
            QKeySequence("Alt+Left").toString(),
            QKeySequence("Alt+1").toString(),
        } <= shortcut_keys
        assert {
            QKeySequence("Alt+Right").toString(),
            QKeySequence("Alt+2").toString(),
        } <= shortcut_keys

        card.correct_btn.click()
        app._shortcut_incorrect()
        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == box_before
        )
        assert app.current_card[0] == card_id
        assert app._daily_review_history == []

        app.flip_card()
        card = app.card_ui
        self._assert_revealed_geometry(app)
        assert not card.incorrect_btn.isHidden()
        assert not card.correct_btn.isHidden()
        card.correct_btn.click()

        assert conn.execute(
            "SELECT box FROM cards WHERE id = ?", (card_id,)
        ).fetchone()[0] == min(box_before + 1, 5)
        assert app._daily_review_history[-1].card[0] == card_id
        self._assert_unrevealed_geometry(app)

        shortcut_card_id = app.current_card[0]
        shortcut_box = conn.execute(
            "SELECT box FROM cards WHERE id = ?", (shortcut_card_id,)
        ).fetchone()[0]
        app.flip_card()
        app._shortcut_incorrect()
        assert conn.execute(
            "SELECT box FROM cards WHERE id = ?", (shortcut_card_id,)
        ).fetchone()[0] == (3 if shortcut_box >= 3 else 1)
        assert app._daily_review_history[-1].card[0] == shortcut_card_id

    def test_nonvisual_drag_routes_only_revealed_regions_and_snaps_outside_home(
        self, app_with_db
    ):
        """Drag uses in-memory rectangles and preserves the queued grading path."""
        from PyQt6.QtCore import QPointF
        from PyQt6.QtTest import QTest

        app, _, conn = app_with_db
        self._start_visible_review(app)
        card_id = app.current_card[0]
        box_before = conn.execute(
            "SELECT box FROM cards WHERE id = ?", (card_id,)
        ).fetchone()[0]
        card = app.card_ui

        card.setPos(QPointF(app.scene.sceneRect().center().x(), 0))
        app.check_card_drop(card)
        assert (card.pos().x(), card.pos().y()) == pytest.approx(
            (app.scene.sceneRect().center().x(), 0)
        )
        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == box_before
        )

        app.flip_card()
        card = app.card_ui
        home = app._review_card_home
        card.setPos(QPointF(app.scene.sceneRect().center().x(), 0))
        app.check_card_drop(card)
        assert (card.pos().x(), card.pos().y()) == pytest.approx(home)
        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == box_before
        )

        assert all(
            item.__class__.__name__ != "DropZoneItem" for item in app.scene.items()
        )
        card.setPos(app._grade_gesture_regions["correct"].center())
        app.check_card_drop(card)
        QTest.qWait(10)

        assert conn.execute(
            "SELECT box FROM cards WHERE id = ?", (card_id,)
        ).fetchone()[0] == min(box_before + 1, 5)
        assert app._daily_review_history[-1].card[0] == card_id
        self._assert_unrevealed_geometry(app)

    def test_incorrect_button_uses_false_grade_db_history_and_next_geometry(
        self, app_with_db
    ):
        """Visible Incorrect uses the false-grade path and advances cleanly."""
        app, _, conn = app_with_db
        self._start_visible_review(app)
        card_id, front, back, _ = app.current_card
        conn.execute("UPDATE cards SET box = 4 WHERE id = ?", (card_id,))
        conn.commit()
        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == 4
        )

        app.flip_card()
        self._assert_revealed_geometry(app)
        assert not app.card_ui.incorrect_btn.isHidden()
        app.card_ui.incorrect_btn.click()

        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == 3
        )
        assert app._daily_review_history[-1] == ReviewHistoryEntry(
            (card_id, front, back, 3), "graded"
        )
        assert app.current_card is not None
        assert app.current_card[0] != card_id
        self._assert_unrevealed_geometry(app)

    def test_incorrect_drag_region_queues_false_grade_db_history_and_next_geometry(
        self, app_with_db
    ):
        """The incorrect gesture region queues the false-grade path."""
        from PyQt6.QtTest import QTest

        app, _, conn = app_with_db
        self._start_visible_review(app)
        card_id, front, back, _ = app.current_card
        conn.execute("UPDATE cards SET box = 4 WHERE id = ?", (card_id,))
        conn.commit()
        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == 4
        )

        app.flip_card()
        self._assert_revealed_geometry(app)
        card = app.card_ui
        incorrect_region = app._grade_gesture_regions["incorrect"]
        card.setPos(incorrect_region.center())
        assert incorrect_region.contains(card.scenePos())
        app.check_card_drop(card)

        # The drop handler queues grading through its zero-delay timer.
        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == 4
        )
        assert app._daily_review_history == []
        assert app.current_card is not None
        assert app.current_card[0] == card_id

        QTest.qWait(10)

        assert (
            conn.execute("SELECT box FROM cards WHERE id = ?", (card_id,)).fetchone()[0]
            == 3
        )
        assert app._daily_review_history[-1] == ReviewHistoryEntry(
            (card_id, front, back, 3), "graded"
        )
        assert app.current_card is not None
        assert app.current_card[0] != card_id
        self._assert_unrevealed_geometry(app)

    def test_grade_next_and_previous_recreate_unrevealed_geometry(self, app_with_db):
        """Grade/Next and Previous never leak revealed gesture geometry."""
        app, _, _ = app_with_db
        self._start_visible_review(app)
        first_id = app.current_card[0]

        app.flip_card()
        self._assert_revealed_geometry(app)
        app._advance_daily_queue()
        self._assert_unrevealed_geometry(app)

        app._previous_daily_card()
        assert app.current_card[0] == first_id
        self._assert_unrevealed_geometry(app)

        app.flip_card()
        app.card_ui.incorrect_btn.click()
        self._assert_unrevealed_geometry(app)

    def test_browse_restart_resume_and_resize_refresh_gesture_lifecycle(
        self, app_with_db
    ):
        """Every direct redraw route recomputes fresh full or reduced geometry."""
        from PyQt6.QtWidgets import QApplication

        app, _, _ = app_with_db
        self._start_visible_review(app)
        selected_id = app.cards_due[0][0]

        app.flip_card()
        self._assert_revealed_geometry(app)
        app._start_selected_card_review(selected_id)
        assert app.current_card[0] == selected_id
        self._assert_unrevealed_geometry(app)

        app.flip_card()
        app._restart_daily_review()
        self._assert_unrevealed_geometry(app)

        app.flip_card()
        app.close_review()
        assert app.current_card is None
        assert app._grade_gesture_regions == {}
        assert app._review_card_bottom == pytest.approx(app.scene.sceneRect().bottom())
        app.start_review()
        self._assert_unrevealed_geometry(app)

        app.resize(1100, 760)
        QApplication.processEvents()
        self._assert_unrevealed_geometry(app)

        app.flip_card()
        old_regions = app._grade_gesture_regions
        app.resize(760, 520)
        QApplication.processEvents()
        self._assert_revealed_geometry(app)
        assert app._grade_gesture_regions is not old_regions

    def test_empty_start_and_reset_paths_clear_stale_gesture_geometry(
        self, app_with_db
    ):
        """No-card and database-session transitions delegate cleanup to one seam."""
        import sqlite3

        from PyQt6.QtCore import QRectF

        from kgb_srs.catalog import DatabaseType

        app, _, conn = app_with_db
        self._start_visible_review(app)
        app.flip_card()
        self._assert_revealed_geometry(app)

        app.close_review()
        app.cards_due = []
        app._paused_cards_due = []
        app._paused_daily_queue = []
        app._paused_review_history = []
        app._paused_review_card = None
        app._paused_review_mode = ""
        conn.execute("UPDATE cards SET next_review = '2999-01-01'")
        conn.commit()
        app.start_review()
        assert app.current_card is None
        assert app._grade_gesture_regions == {}
        assert app._review_card_bottom == pytest.approx(app.scene.sceneRect().bottom())

        def seed_stale_geometry():
            app.current_card = (999, "stale", "back", 1)
            app.is_current_flipped = True
            app._grade_gesture_regions = {
                "incorrect": QRectF(0, 500, 200, 80),
                "correct": QRectF(300, 500, 200, 80),
            }
            app._review_card_bottom = 480.0
            app._review_card_home = (300.0, 200.0)

        seed_stale_geometry()
        app._reset_review_session()
        assert app.current_card is None
        assert app._grade_gesture_regions == {}
        assert app._review_card_bottom == pytest.approx(app.scene.sceneRect().bottom())

        seed_stale_geometry()
        app._clear_database_state()
        assert app.conn is None
        assert app.current_card is None
        assert app._grade_gesture_regions == {}
        assert app._review_card_bottom == pytest.approx(app.scene.sceneRect().bottom())

        replacement_conn = sqlite3.connect(":memory:")
        seed_stale_geometry()
        app._adopt_database(
            replacement_conn,
            "replacement.db",
            "Temporary",
            DatabaseType.KNOWLEDGE,
            False,
            False,
        )
        assert app.conn is replacement_conn
        assert app.current_card is None
        assert app._grade_gesture_regions == {}
        assert app._review_card_bottom == pytest.approx(app.scene.sceneRect().bottom())
