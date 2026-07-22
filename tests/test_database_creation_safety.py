"""Safety boundaries for exclusively publishing new SQLite databases."""

import sqlite3

import pytest


def test_exclusive_creation_preserves_a_competitor_database(tmp_path):
    """A target published by another creator is never replaced."""
    from kgb_srs.schema import create_database_exclusively

    target = tmp_path / "knowledge_barsky.db"

    def publish_competitor(_staging_conn):
        competitor = sqlite3.connect(target)
        try:
            competitor.execute("CREATE TABLE competitor_marker (value TEXT)")
            competitor.execute("INSERT INTO competitor_marker VALUES ('keep me')")
            competitor.commit()
        finally:
            competitor.close()

    with pytest.raises(FileExistsError):
        create_database_exclusively(target, publish_competitor)

    with sqlite3.connect(target) as competitor:
        assert competitor.execute("SELECT value FROM competitor_marker").fetchone() == (
            "keep me",
        )
    assert list(tmp_path.glob(".knowledge_barsky.db.staging-*")) == []


def test_exclusive_creation_removes_staging_file_when_initializer_fails(tmp_path):
    """A failed initializer cannot leave a partial database visible or staged."""
    from kgb_srs.schema import create_database_exclusively

    target = tmp_path / "knowledge_barsky.db"

    with pytest.raises(sqlite3.OperationalError, match="metadata write failed"):
        create_database_exclusively(
            target,
            lambda _conn: (_ for _ in ()).throw(
                sqlite3.OperationalError("metadata write failed")
            ),
        )

    assert not target.exists()
    assert list(tmp_path.glob(".knowledge_barsky.db.staging-*")) == []


def test_exclusive_creation_publishes_a_valid_initialized_database(tmp_path):
    """The published file is a complete database, not an empty reservation."""
    from kgb_srs.schema import create_database_exclusively

    target = tmp_path / "knowledge_barsky.db"

    create_database_exclusively(
        target,
        lambda conn: conn.execute(
            "INSERT INTO settings (key, value) VALUES ('database_type', 'knowledge')"
        ),
    )

    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone() == (0,)
        assert conn.execute(
            "SELECT value FROM settings WHERE key = 'database_type'"
        ).fetchone() == ("knowledge",)
    assert list(tmp_path.glob(".knowledge_barsky.db.staging-*")) == []
