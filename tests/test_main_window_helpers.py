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
    def setText(self, _text):
        pass


class _NewDatabaseWindow:
    def __init__(self, database_root):
        self.settings = {"database_root": str(database_root)}
        self.db_btn = _FakeButton()
        self.current_db_path = None
        self.current_lang = None

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
    failure = RuntimeError("sentence schema failed")
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
        lambda _conn: (_ for _ in ()).throw(failure),
    )

    window = _NewDatabaseWindow(tmp_path / "databases")
    with pytest.raises(RuntimeError, match="sentence schema failed") as exc_info:
        main_window.BarskyApp.create_new_database(window)

    assert exc_info.value is failure
    assert conn.closed
