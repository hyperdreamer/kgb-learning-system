"""Focused tests for main-window helpers that do not require a window."""

import sqlite3

import pytest


def test_open_and_infer_type_closes_connection_when_type_read_fails(monkeypatch):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    conn = Connection()
    monkeypatch.setattr(main_window.sqlite3, "connect", lambda _path: conn)
    monkeypatch.setattr(
        main_window,
        "read_database_type",
        lambda _conn: (_ for _ in ()).throw(sqlite3.DatabaseError("bad DB")),
    )
    inferred = object()
    monkeypatch.setattr(main_window, "infer_database_type", lambda _path: inferred)

    assert main_window._open_and_infer_type("broken.db") is inferred
    assert conn.closed


class _TrackedConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeButton:
    def __init__(self, text=""):
        self.text = text

    def setText(self, text):
        self.text = text


class _NewDatabaseWindow:
    def __init__(self, database_root):
        self.settings = {"database_root": str(database_root)}
        self.db_btn = _FakeButton("📂 Existing Database")
        self.current_db_path = "/existing/database_barsky.db"
        self.current_lang = "Knowledge-based/Existing Database"

    def load_database(self, silent=False):
        raise AssertionError(f"load_database unexpectedly called: {silent=}")


def _accepted_creation_dialog(db_type):
    class AcceptedCreationDialog:
        def __init__(self, *args, **kwargs):
            self.selected_type = db_type
            self.db_name = "CloseFailure"

        def exec(self):
            from PyQt6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    return AcceptedCreationDialog


def test_create_new_database_closes_connection_when_type_write_fails(
    tmp_path, monkeypatch
):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window
    from kgb_srs.catalog import DatabaseType

    conn = _TrackedConnection()
    failure = RuntimeError("metadata write failed")
    monkeypatch.setattr(
        main_window,
        "DBCreationDialog",
        _accepted_creation_dialog(DatabaseType.LANGUAGE_WORD_PHRASE),
    )
    monkeypatch.setattr(main_window, "init_db", lambda _path: conn)
    monkeypatch.setattr(
        main_window,
        "write_database_type",
        lambda _conn, _type: (_ for _ in ()).throw(failure),
    )

    window = _NewDatabaseWindow(tmp_path / "databases")
    with pytest.raises(RuntimeError, match="metadata write failed") as exc_info:
        main_window.BarskyApp.create_new_database(window)

    assert exc_info.value is failure
    assert conn.closed


def test_create_new_database_closes_connection_when_sentence_schema_fails(
    tmp_path, monkeypatch
):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window
    import kgb_srs.schema as schema
    from kgb_srs.catalog import DatabaseType

    conn = _TrackedConnection()
    messages = []
    monkeypatch.setattr(
        main_window,
        "DBCreationDialog",
        _accepted_creation_dialog(DatabaseType.LANGUAGE_SENTENCE),
    )
    monkeypatch.setattr(main_window, "init_db", lambda _path: conn)
    monkeypatch.setattr(main_window, "write_database_type", lambda *_args: None)
    monkeypatch.setattr(
        schema,
        "ensure_sentence_schema",
        lambda _conn: (_ for _ in ()).throw(
            sqlite3.OperationalError("sentence schema failed")
        ),
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        lambda *args: messages.append(args),
    )

    window = _NewDatabaseWindow(tmp_path / "databases")
    main_window.BarskyApp.create_new_database(window)

    assert conn.closed
    assert messages
    assert "sentence schema failed" in messages[0][2]
    assert window.current_db_path == "/existing/database_barsky.db"
    assert window.current_lang == "Knowledge-based/Existing Database"
    assert window.db_btn.text == "📂 Existing Database"


def test_create_new_database_handles_target_directory_failure_without_changing_state(
    tmp_path, monkeypatch
):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window
    from kgb_srs.catalog import DatabaseType

    messages = []
    monkeypatch.setattr(
        main_window,
        "DBCreationDialog",
        _accepted_creation_dialog(DatabaseType.KNOWLEDGE),
    )
    monkeypatch.setattr(
        main_window, "ensure_database_root_structure", lambda _root: None
    )
    monkeypatch.setattr(
        main_window.os,
        "makedirs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("directory access denied")
        ),
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        lambda *args: messages.append(args),
    )

    window = _NewDatabaseWindow(tmp_path / "databases")
    main_window.BarskyApp.create_new_database(window)

    assert messages
    assert "Could not create database" in messages[0][2]
    assert "directory access denied" in messages[0][2]
    assert window.current_db_path == "/existing/database_barsky.db"
    assert window.current_lang == "Knowledge-based/Existing Database"
    assert window.db_btn.text == "📂 Existing Database"


def test_create_new_database_handles_init_db_failure_without_changing_state(
    tmp_path, monkeypatch
):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window
    from kgb_srs.catalog import DatabaseType

    messages = []
    monkeypatch.setattr(
        main_window,
        "DBCreationDialog",
        _accepted_creation_dialog(DatabaseType.KNOWLEDGE),
    )
    monkeypatch.setattr(
        main_window,
        "init_db",
        lambda _path: (_ for _ in ()).throw(
            sqlite3.OperationalError("database locked")
        ),
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        lambda *args: messages.append(args),
    )

    window = _NewDatabaseWindow(tmp_path / "databases")
    main_window.BarskyApp.create_new_database(window)

    assert messages
    assert "Could not create database" in messages[0][2]
    assert "database locked" in messages[0][2]
    assert window.current_db_path == "/existing/database_barsky.db"
    assert window.current_lang == "Knowledge-based/Existing Database"
    assert window.db_btn.text == "📂 Existing Database"


def test_projection_marker_adoption_confirms_and_reports_backup(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window
    from kgb_srs.senses import ProjectionOwnershipConflictError

    source = object()
    source_path = str(tmp_path / "Sentence-based" / "English_barsky.db")
    target_path = str(tmp_path / "WordPhrase-based" / "English_barsky.db")
    backup_path = f"{target_path}.backup"
    calls = []
    messages = []
    window = type("Window", (), {"settings": {"database_root": str(tmp_path)}})()
    conflict = ProjectionOwnershipConflictError(
        {"code": "word_phrase_projection_marker_missing", "message": "marker missing"}
    )

    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        lambda *args: main_window.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "information",
        lambda *args: messages.append(args),
    )
    import kgb_srs.senses as senses

    monkeypatch.setattr(
        senses, "default_word_phrase_path_for_sentence", lambda *_: target_path
    )
    monkeypatch.setattr(
        senses,
        "adopt_canonical_word_phrase_projection",
        lambda conn, path, root: (
            calls.append((conn, path, root))
            or (target_path, {"backup_path": backup_path})
        ),
    )

    assert main_window.BarskyApp._offer_projection_adoption(
        window, source, source_path, conflict
    )
    assert calls == [(source, source_path, str(tmp_path))]
    assert messages and backup_path in messages[0][2]


def test_create_new_database_handles_metadata_failure_and_closes_connection(
    tmp_path, monkeypatch
):
    pytest.importorskip("PyQt6")
    import kgb_srs.main_window as main_window
    from kgb_srs.catalog import DatabaseType

    conn = _TrackedConnection()
    messages = []
    monkeypatch.setattr(
        main_window,
        "DBCreationDialog",
        _accepted_creation_dialog(DatabaseType.KNOWLEDGE),
    )
    monkeypatch.setattr(main_window, "init_db", lambda _path: conn)
    monkeypatch.setattr(
        main_window,
        "write_database_type",
        lambda *_args: (_ for _ in ()).throw(
            sqlite3.OperationalError("metadata locked")
        ),
    )
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        lambda *args: messages.append(args),
    )

    window = _NewDatabaseWindow(tmp_path / "databases")
    main_window.BarskyApp.create_new_database(window)

    assert conn.closed
    assert messages
    assert "Could not create database" in messages[0][2]
    assert "metadata locked" in messages[0][2]
    assert window.current_db_path == "/existing/database_barsky.db"
    assert window.current_lang == "Knowledge-based/Existing Database"
    assert window.db_btn.text == "📂 Existing Database"
