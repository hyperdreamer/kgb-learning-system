"""Focused safety tests for candidate database loading and adoption."""

import logging
import sqlite3

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from kgb_srs.catalog import DatabaseType, write_database_type
from kgb_srs.main_window import BarskyApp
from kgb_srs.schema import init_db


class _TrackedConnection:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class _Action:
    def __init__(self, path):
        self._path = path

    def data(self):
        return self._path


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    app = BarskyApp()
    yield app
    if app.conn is not None:
        app.conn.close()
    app.close()
    app.deleteLater()


def _set_active_session(window):
    window.current_db_path = "/existing/active_barsky.db"
    window.current_lang = "Knowledge-based/Active"
    window.db_btn.setText("📂 Active")
    window.current_card = object()
    window._current_card_transition = object()
    window.cards_due = [object()]
    window.is_current_flipped = True
    window.card_ui = object()
    window.review_mode = "daily"
    window._paused_review_card = object()
    window._paused_current_card_transition = object()
    window._paused_review_mode = "daily"
    window._daily_review_history = [object()]
    window._daily_queue_snapshot = [object()]
    window._paused_cards_due = [object()]
    window._paused_daily_queue = [object()]
    window._paused_review_history = [object()]


def _session_snapshot(window):
    return {
        name: getattr(window, name)
        for name in (
            "conn",
            "current_db_path",
            "current_lang",
            "_db_type",
            "current_card",
            "_current_card_transition",
            "cards_due",
            "is_current_flipped",
            "card_ui",
            "review_mode",
            "_paused_review_card",
            "_paused_current_card_transition",
            "_paused_review_mode",
            "_daily_review_history",
            "_daily_queue_snapshot",
            "_paused_cards_due",
            "_paused_daily_queue",
            "_paused_review_history",
        )
    } | {"button_text": window.db_btn.text()}


def _database(path, db_type=DatabaseType.KNOWLEDGE):
    conn = init_db(path)
    write_database_type(conn, db_type)
    conn.close()


def test_candidate_open_failure_closes_only_candidate_and_preserves_session(
    window, tmp_path, monkeypatch
):
    """Validation errors do not tear down the active review session."""
    import kgb_srs.main_window as main_window

    _set_active_session(window)
    old_conn = _TrackedConnection()
    candidate_conn = _TrackedConnection()
    window.conn = old_conn
    before = _session_snapshot(window)
    candidate_path = tmp_path / "broken_barsky.db"
    candidate_path.touch()

    monkeypatch.setattr(main_window, "init_db", lambda _path: candidate_conn)
    monkeypatch.setattr(
        main_window,
        "read_database_type",
        lambda _conn: (_ for _ in ()).throw(sqlite3.DatabaseError("corrupt")),
    )

    window.load_database(
        silent=True, db_path=str(candidate_path), display="Knowledge-based/Broken"
    )

    assert candidate_conn.close_calls == 1
    assert old_conn.close_calls == 0
    assert _session_snapshot(window) == before


def test_stale_menu_target_warns_without_initializing_or_replacing_active_session(
    window, monkeypatch
):
    """A stale menu action reports its missing candidate without changing sessions."""
    import kgb_srs.main_window as main_window

    _set_active_session(window)
    old_conn = _TrackedConnection()
    window.conn = old_conn
    before = _session_snapshot(window)
    warnings = []
    missing_path = "/missing/menu-target_barsky.db"
    monkeypatch.setattr(main_window, "find_databases", lambda _root: [])
    monkeypatch.setattr(
        main_window,
        "init_db",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not open")),
    )
    monkeypatch.setattr(
        main_window.QMessageBox, "warning", lambda *args: warnings.append(args)
    )

    window.select_database(_Action(missing_path))

    assert warnings == [(window, "Error", f"Database file not found:\n{missing_path}")]
    assert old_conn.close_calls == 0
    assert _session_snapshot(window) == before


def test_startup_default_uses_candidate_arguments_without_preassigning_state(
    qapp, tmp_path, monkeypatch
):
    """Startup delegates its default database to candidate loading unchanged."""
    import kgb_srs.main_window as main_window

    default_path = tmp_path / "default_barsky.db"
    default_path.touch()
    calls = []

    monkeypatch.setattr(
        main_window, "resolve_default_database", lambda _settings: str(default_path)
    )
    monkeypatch.setattr(
        main_window,
        "find_databases",
        lambda _root: [("Knowledge-based/Default", str(default_path))],
    )

    def capture_load(self, silent=False, *, db_path=None, display=None):
        calls.append(
            {
                "silent": silent,
                "db_path": db_path,
                "display": display,
                "current_db_path": self.current_db_path,
                "current_lang": self.current_lang,
                "button_text": self.db_btn.text(),
            }
        )

    monkeypatch.setattr(main_window.BarskyApp, "load_database", capture_load)
    app = BarskyApp()
    try:
        assert calls == [
            {
                "silent": True,
                "db_path": str(default_path),
                "display": "Knowledge-based/Default",
                "current_db_path": None,
                "current_lang": None,
                "button_text": "📂 Select Database",
            }
        ]
    finally:
        app.close()
        app.deleteLater()


def test_successful_candidate_adoption_closes_old_connection_once_and_resets_review(
    window, tmp_path
):
    """Only a validated candidate replaces the active connection and session."""
    _set_active_session(window)
    old_conn = _TrackedConnection()
    window.conn = old_conn
    candidate_path = tmp_path / "candidate_barsky.db"
    _database(candidate_path)

    window.load_database(
        silent=True, db_path=str(candidate_path), display="Knowledge-based/Candidate"
    )

    assert old_conn.close_calls == 1
    assert window.current_db_path == str(candidate_path)
    assert window.current_lang == "Knowledge-based/Candidate"
    assert window.db_btn.text() == "📂 Candidate"
    assert window.current_card is None
    assert window._current_card_transition is None
    assert window.cards_due == []
    assert window.review_mode == ""
    assert window._paused_review_card is None
    assert window._paused_current_card_transition is None
    assert window._paused_review_mode == ""
    assert window._daily_review_history == []
    assert window._daily_queue_snapshot == []
    assert window._paused_cards_due == []
    assert window._paused_daily_queue == []
    assert window._paused_review_history == []


def test_projection_ownership_conflict_does_not_block_sentence_database_load(
    window, tmp_path
):
    """A protected canonical target must not make its sentence DB unloadable."""
    root = tmp_path / "db"
    candidate_path = root / "Language-based" / "Sentence-based" / "English_barsky.db"
    candidate_path.parent.mkdir(parents=True)
    candidate = init_db(str(candidate_path))
    try:
        write_database_type(candidate, DatabaseType.LANGUAGE_SENTENCE)
        from kgb_srs.schema import ensure_sentence_schema

        ensure_sentence_schema(candidate)
    finally:
        candidate.close()
    target_path = root / "Language-based" / "Word-Phrase-based" / "English_barsky.db"
    target_path.parent.mkdir(parents=True)
    target = init_db(str(target_path))
    try:
        write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
        target.execute(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
            ("private", "must survive", 4, "2030-01-01"),
        )
        target.commit()
    finally:
        target.close()

    window.settings["database_root"] = str(root)
    window.load_database(
        silent=True,
        db_path=str(candidate_path),
        display="Language-based/Sentence-based/English",
    )

    assert window.current_db_path == str(candidate_path)
    assert window.conn is not None
    with sqlite3.connect(target_path) as target:
        assert target.execute("SELECT front FROM cards").fetchall() == [("private",)]
        assert (
            target.execute(
                "SELECT value FROM settings WHERE key LIKE 'projection_owner_%'"
            ).fetchall()
            == []
        )


def test_silent_markerless_projection_load_never_offers_adoption(
    window, tmp_path, monkeypatch
):
    """Startup silently adopts the sentence DB even when projection is markerless."""
    root = tmp_path / "db"
    candidate_path = root / "Language-based" / "Sentence-based" / "English_barsky.db"
    candidate_path.parent.mkdir(parents=True)
    candidate = init_db(str(candidate_path))
    try:
        write_database_type(candidate, DatabaseType.LANGUAGE_SENTENCE)
        from kgb_srs.schema import ensure_sentence_schema

        ensure_sentence_schema(candidate)
    finally:
        candidate.close()
    target_path = root / "Language-based" / "Word-Phrase-based" / "English_barsky.db"
    target_path.parent.mkdir(parents=True)
    target = init_db(str(target_path))
    try:
        write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    finally:
        target.close()

    window.settings["database_root"] = str(root)
    monkeypatch.setattr(
        window,
        "_offer_projection_adoption",
        lambda *_args: pytest.fail("silent loading must not offer projection adoption"),
    )

    window.load_database(
        silent=True,
        db_path=str(candidate_path),
        display="Language-based/Sentence-based/English",
    )

    assert window.current_db_path == str(candidate_path)


def test_interactive_markerless_projection_offer_runs_after_database_adoption(
    window, tmp_path, monkeypatch
):
    """A migration decision never delays making its sentence source usable."""
    root = tmp_path / "db"
    candidate_path = root / "Language-based" / "Sentence-based" / "English_barsky.db"
    candidate_path.parent.mkdir(parents=True)
    candidate = init_db(str(candidate_path))
    try:
        write_database_type(candidate, DatabaseType.LANGUAGE_SENTENCE)
        from kgb_srs.schema import ensure_sentence_schema

        ensure_sentence_schema(candidate)
    finally:
        candidate.close()
    target_path = root / "Language-based" / "Word-Phrase-based" / "English_barsky.db"
    target_path.parent.mkdir(parents=True)
    target = init_db(str(target_path))
    try:
        write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    finally:
        target.close()

    window.settings["database_root"] = str(root)
    offered_after_adoption = []

    def offer(_conn, _path, _conflict):
        offered_after_adoption.append(
            window.current_db_path == str(candidate_path) and window.conn is not None
        )
        return False

    monkeypatch.setattr(window, "_offer_projection_adoption", offer)
    window.load_database(
        silent=False,
        db_path=str(candidate_path),
        display="Language-based/Sentence-based/English",
    )

    assert offered_after_adoption == [True]


def test_projection_ownership_conflict_does_not_block_sentence_database_create(
    window, tmp_path, monkeypatch
):
    """Creation still adopts the new sentence DB when projection is protected."""
    import kgb_srs.main_window as main_window

    class AcceptedSentenceDialog:
        selected_type = DatabaseType.LANGUAGE_SENTENCE
        db_name = "English"

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    root = tmp_path / "db"
    target_path = root / "Language-based" / "Word-Phrase-based" / "English_barsky.db"
    target_path.parent.mkdir(parents=True)
    target = init_db(str(target_path))
    try:
        write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
        target.execute(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
            ("private", "must survive", 4, "2030-01-01"),
        )
        target.commit()
    finally:
        target.close()

    window.settings["database_root"] = str(root)
    monkeypatch.setattr(main_window, "DBCreationDialog", AcceptedSentenceDialog)
    window.create_new_database()

    source_path = root / "Language-based" / "Sentence-based" / "English_barsky.db"
    assert source_path.is_file()
    assert window.current_db_path == str(source_path)
    assert window.conn is not None
    with sqlite3.connect(target_path) as target:
        assert target.execute("SELECT front FROM cards").fetchall() == [("private",)]
        assert (
            target.execute(
                "SELECT value FROM settings WHERE key LIKE 'projection_owner_%'"
            ).fetchall()
            == []
        )


def test_projection_failure_does_not_block_candidate_adoption(
    window, tmp_path, monkeypatch, caplog
):
    """Projection maintenance remains best-effort while opening sentence databases."""
    import kgb_srs.senses as senses

    _set_active_session(window)
    window.conn = _TrackedConnection()
    candidate_path = tmp_path / "sentence_barsky.db"
    _database(candidate_path, DatabaseType.LANGUAGE_SENTENCE)
    monkeypatch.setattr(
        senses,
        "ensure_linked_word_phrase_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("projection down")
        ),
    )

    with caplog.at_level(logging.WARNING, logger="kgb_srs.main_window"):
        window.load_database(
            silent=True,
            db_path=str(candidate_path),
            display="Language-based/Sentence/Candidate",
        )

    assert window.current_db_path == str(candidate_path)
    assert window.conn is not None
    assert "projection down" in caplog.text


def test_created_database_candidate_failure_keeps_old_session_and_publishes_valid_file(
    window, tmp_path, monkeypatch
):
    """Creation publishes first, but an unopenable candidate never replaces the old DB."""
    import kgb_srs.main_window as main_window

    class _AcceptedDialog:
        selected_type = DatabaseType.KNOWLEDGE
        db_name = "New Candidate"

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            from PyQt6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    _set_active_session(window)
    old_conn = _TrackedConnection()
    window.conn = old_conn
    before = _session_snapshot(window)
    window.settings["database_root"] = str(tmp_path)
    monkeypatch.setattr(main_window, "DBCreationDialog", _AcceptedDialog)
    original_init = main_window.init_db
    monkeypatch.setattr(
        main_window,
        "init_db",
        lambda path: (
            (_ for _ in ()).throw(sqlite3.DatabaseError("candidate rejected"))
            if path.endswith("New Candidate_barsky.db")
            else original_init(path)
        ),
    )

    window.create_new_database()

    target = tmp_path / "Knowledge-based" / "New Candidate_barsky.db"
    assert target.exists()
    assert _session_snapshot(window) == before
    assert old_conn.close_calls == 0
    with sqlite3.connect(target) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'cards'"
        ).fetchone()


def test_create_collision_shows_exists_warning_and_preserves_active_session(
    window, tmp_path, monkeypatch
):
    """A publication collision is reported without disturbing the active database."""
    import kgb_srs.main_window as main_window

    class _AcceptedDialog:
        selected_type = DatabaseType.KNOWLEDGE
        db_name = "Contended"

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            from PyQt6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    _set_active_session(window)
    old_conn = _TrackedConnection()
    window.conn = old_conn
    before = _session_snapshot(window)
    window.settings["database_root"] = str(tmp_path)
    warnings = []
    monkeypatch.setattr(main_window, "DBCreationDialog", _AcceptedDialog)
    monkeypatch.setattr(
        main_window,
        "create_database_exclusively",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()),
    )
    monkeypatch.setattr(
        main_window.QMessageBox, "warning", lambda *args: warnings.append(args)
    )

    window.create_new_database()

    assert warnings[0][1] == "Exists"
    assert old_conn.close_calls == 0
    assert _session_snapshot(window) == before


def test_created_database_is_candidate_opened_and_adopted(
    window, tmp_path, monkeypatch
):
    """A newly published database uses the same safe adoption path as selection."""
    import kgb_srs.main_window as main_window

    class _AcceptedDialog:
        selected_type = DatabaseType.KNOWLEDGE
        db_name = "Published Candidate"

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            from PyQt6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    _set_active_session(window)
    old_conn = _TrackedConnection()
    window.conn = old_conn
    window.settings["database_root"] = str(tmp_path)
    monkeypatch.setattr(main_window, "DBCreationDialog", _AcceptedDialog)

    window.create_new_database()

    target = tmp_path / "Knowledge-based" / "Published Candidate_barsky.db"
    assert target.exists()
    assert old_conn.close_calls == 1
    assert window.current_db_path == str(target)
    assert window.current_lang == "Knowledge-based/Published Candidate"
    with sqlite3.connect(target) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'cards'"
        ).fetchone()
