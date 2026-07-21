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
