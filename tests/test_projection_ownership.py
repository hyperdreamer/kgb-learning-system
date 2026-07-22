"""SQLite integration tests for sentence-to-word/phrase projection ownership."""

from __future__ import annotations

import os
import sqlite3

import pytest

from kgb_srs.catalog import DatabaseType, write_database_type
from kgb_srs.schema import ensure_sentence_schema, init_db, insert_sentence_card
from kgb_srs.senses import (
    ProjectionOwnershipConflictError,
    adopt_canonical_word_phrase_projection,
    default_word_phrase_path_for_sentence,
    ensure_linked_word_phrase_database,
    get_linked_word_phrase_db,
    set_linked_word_phrase_db,
)


def _sentence_database(tmp_path, name="English_barsky.db"):
    root = tmp_path / "db"
    source_path = root / "Language-based" / "Sentence-based" / name
    source_path.parent.mkdir(parents=True)
    source = init_db(str(source_path))
    ensure_sentence_schema(source)
    insert_sentence_card(source, "I went to the bank.", [("bank", "financial")])
    return root, source_path, source


def test_missing_canonical_target_is_marked_before_first_sync(tmp_path):
    root, source_path, source = _sentence_database(tmp_path)
    try:
        target_path, stats = ensure_linked_word_phrase_database(
            source, str(source_path), str(root), sync=True
        )

        assert stats is not None
        assert (
            source.execute(
                "SELECT value FROM settings WHERE key='projection_source_uuid'"
            ).fetchone()
            is not None
        )
        with sqlite3.connect(target_path) as target:
            marker = dict(
                target.execute(
                    "SELECT key, value FROM settings "
                    "WHERE key LIKE 'projection_owner_%'"
                )
            )
        assert marker["projection_owner_version"] == "1"
        assert marker["projection_owner_source_path"] == os.path.normpath(
            os.path.realpath(source_path)
        )
        assert (
            marker["projection_owner_source_uuid"]
            == source.execute(
                "SELECT value FROM settings WHERE key='projection_source_uuid'"
            ).fetchone()[0]
        )
    finally:
        source.close()


def test_competing_projection_creator_is_never_adopted_or_overwritten(
    tmp_path, monkeypatch
):
    """A race winner without our marker remains untouched and raises conflict."""
    import kgb_srs.senses as senses

    root, source_path, source = _sentence_database(tmp_path)
    target_path = default_word_phrase_path_for_sentence(str(source_path), str(root))

    def publish_competitor(path, _initializer):
        competitor = init_db(path)
        try:
            write_database_type(competitor, DatabaseType.LANGUAGE_WORD_PHRASE)
            competitor.execute(
                "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
                ("private", "must survive", 4, "2030-01-01"),
            )
            competitor.commit()
        finally:
            competitor.close()
        raise FileExistsError(path)

    monkeypatch.setattr(senses, "create_database_exclusively", publish_competitor)
    try:
        with pytest.raises(ProjectionOwnershipConflictError):
            ensure_linked_word_phrase_database(
                source, str(source_path), str(root), sync=True
            )

        with sqlite3.connect(target_path) as competitor:
            assert competitor.execute("SELECT front FROM cards").fetchall() == [
                ("private",)
            ]
            assert (
                competitor.execute(
                    "SELECT value FROM settings WHERE key LIKE 'projection_owner_%'"
                ).fetchall()
                == []
            )
        assert get_linked_word_phrase_db(source) is None
        assert (
            source.execute(
                "SELECT value FROM settings WHERE key='projection_source_uuid'"
            ).fetchone()
            is None
        )
    finally:
        source.close()


def test_matching_owner_is_reused_for_later_sync(tmp_path):
    root, source_path, source = _sentence_database(tmp_path)
    try:
        target_path, first_stats = ensure_linked_word_phrase_database(
            source, str(source_path), str(root), sync=True
        )
        source_uuid = source.execute(
            "SELECT value FROM settings WHERE key='projection_source_uuid'"
        ).fetchone()[0]
        insert_sentence_card(source, "The river bank.", [("river", "watercourse")])

        repeated_path, second_stats = ensure_linked_word_phrase_database(
            source, str(source_path), str(root), sync=True
        )

        assert repeated_path == target_path
        assert first_stats is not None
        assert second_stats is not None
        assert second_stats["expressions"] == 2
        assert (
            source.execute(
                "SELECT value FROM settings WHERE key='projection_source_uuid'"
            ).fetchone()[0]
            == source_uuid
        )
        with sqlite3.connect(target_path) as target:
            assert target.execute(
                "SELECT front FROM cards ORDER BY front"
            ).fetchall() == [
                ("bank",),
                ("river",),
            ]
    finally:
        source.close()


def test_different_sentence_source_cannot_claim_existing_projection(tmp_path):
    root, source_path, source_one = _sentence_database(tmp_path)
    source_two_path = tmp_path / "other-source_barsky.db"
    source_two = init_db(str(source_two_path))
    ensure_sentence_schema(source_two)
    insert_sentence_card(source_two, "A different card.", [("different", "other")])
    try:
        target_path, _ = ensure_linked_word_phrase_database(
            source_one, str(source_path), str(root), sync=True
        )
        target_before = open(target_path, "rb").read()
        source_two_before = source_two.execute(
            "SELECT key, value FROM settings ORDER BY key"
        ).fetchall()

        with pytest.raises(ProjectionOwnershipConflictError) as error:
            ensure_linked_word_phrase_database(
                source_two, str(source_path), str(root), sync=True
            )

        assert error.value.conflict["code"] == "word_phrase_projection_owner_mismatch"
        assert open(target_path, "rb").read() == target_before
        assert (
            source_two.execute(
                "SELECT key, value FROM settings ORDER BY key"
            ).fetchall()
            == source_two_before
        )
        assert get_linked_word_phrase_db(source_two) is None
    finally:
        source_one.close()
        source_two.close()


def test_markerless_empty_canonical_target_is_not_adopted(tmp_path):
    root, source_path, source = _sentence_database(tmp_path)
    target_path = default_word_phrase_path_for_sentence(str(source_path), str(root))
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    target = init_db(target_path)
    write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    target.close()
    source_before = source.execute(
        "SELECT key, value FROM settings ORDER BY key"
    ).fetchall()
    try:
        with pytest.raises(ProjectionOwnershipConflictError):
            ensure_linked_word_phrase_database(
                source, str(source_path), str(root), sync=True
            )

        with sqlite3.connect(target_path) as unchanged:
            assert unchanged.execute("SELECT COUNT(*) FROM cards").fetchone() == (0,)
            assert (
                unchanged.execute(
                    "SELECT value FROM settings WHERE key LIKE 'projection_owner_%'"
                ).fetchall()
                == []
            )
        assert (
            source.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
            == source_before
        )
        assert get_linked_word_phrase_db(source) is None
    finally:
        source.close()


def test_markerless_populated_canonical_target_is_not_adopted_or_pruned(tmp_path):
    root, source_path, source = _sentence_database(tmp_path)
    target_path = default_word_phrase_path_for_sentence(str(source_path), str(root))
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    target = init_db(target_path)
    write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    target.execute(
        "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
        ("legacy", "must survive", 4, "2030-01-01"),
    )
    target.commit()
    target.close()
    set_linked_word_phrase_db(source, str(tmp_path / "old-link_barsky.db"))
    target_before = open(target_path, "rb").read()
    source_before = source.execute(
        "SELECT key, value FROM settings ORDER BY key"
    ).fetchall()
    try:
        with pytest.raises(ProjectionOwnershipConflictError) as error:
            ensure_linked_word_phrase_database(
                source, str(source_path), str(root), sync=True
            )

        assert error.value.conflict["code"] == "word_phrase_projection_marker_missing"
        assert open(target_path, "rb").read() == target_before
        assert (
            source.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
            == source_before
        )
        assert get_linked_word_phrase_db(source) == os.path.abspath(
            str(tmp_path / "old-link_barsky.db")
        )
    finally:
        source.close()


def test_explicit_adoption_backs_up_then_claims_and_syncs(tmp_path):
    root, source_path, source = _sentence_database(tmp_path)
    target_path = default_word_phrase_path_for_sentence(str(source_path), str(root))
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    target = init_db(target_path)
    write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    target.execute(
        "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
        ("legacy", "backup me", 4, "2030-01-01"),
    )
    target.commit()
    target.close()
    before = open(target_path, "rb").read()
    try:
        adopted_path, stats = adopt_canonical_word_phrase_projection(
            source, str(source_path), str(root)
        )

        assert adopted_path == target_path
        assert open(stats["backup_path"], "rb").read() == before
        assert get_linked_word_phrase_db(source) == target_path
        with sqlite3.connect(target_path) as adopted:
            assert adopted.execute(
                "SELECT value FROM settings WHERE key='projection_owner_version'"
            ).fetchone() == ("1",)
            assert adopted.execute("SELECT front FROM cards").fetchall() == [("bank",)]
    finally:
        source.close()


def test_adoption_sync_failure_rolls_back_marker_and_preserves_source_link(tmp_path):
    root, source_path, source = _sentence_database(tmp_path)
    target_path = default_word_phrase_path_for_sentence(str(source_path), str(root))
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    target = init_db(target_path)
    write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    target.execute(
        "CREATE TRIGGER reject_projection BEFORE INSERT ON cards "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    target.commit()
    target.close()
    old_link = str(tmp_path / "old-link_barsky.db")
    set_linked_word_phrase_db(source, old_link)
    source_before = source.execute(
        "SELECT key, value FROM settings ORDER BY key"
    ).fetchall()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="blocked"):
            adopt_canonical_word_phrase_projection(source, str(source_path), str(root))

        with sqlite3.connect(target_path) as unchanged:
            assert (
                unchanged.execute(
                    "SELECT value FROM settings WHERE key LIKE 'projection_owner_%'"
                ).fetchall()
                == []
            )
            assert unchanged.execute("SELECT front FROM cards").fetchall() == []
        assert (
            source.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
            == source_before
        )
        assert get_linked_word_phrase_db(source) == os.path.abspath(old_link)
    finally:
        source.close()


def test_adoption_backup_failure_leaves_markerless_target_and_link_unchanged(
    tmp_path, monkeypatch
):
    root, source_path, source = _sentence_database(tmp_path)
    target_path = default_word_phrase_path_for_sentence(str(source_path), str(root))
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    target = init_db(target_path)
    write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
    target.close()
    old_link = str(tmp_path / "old-link_barsky.db")
    set_linked_word_phrase_db(source, old_link)
    monkeypatch.setattr(
        "kgb_srs.senses._backup_projection_database",
        lambda _path: (_ for _ in ()).throw(OSError("backup blocked")),
    )
    try:
        with pytest.raises(OSError, match="backup blocked"):
            adopt_canonical_word_phrase_projection(source, str(source_path), str(root))

        with sqlite3.connect(target_path) as unchanged:
            assert (
                unchanged.execute(
                    "SELECT value FROM settings WHERE key LIKE 'projection_owner_%'"
                ).fetchall()
                == []
            )
        assert get_linked_word_phrase_db(source) == os.path.abspath(old_link)
    finally:
        source.close()


def test_same_uuid_with_different_real_source_path_conflicts(tmp_path):
    root, first_path, first = _sentence_database(tmp_path, "English/First_barsky.db")
    second_path = (
        root / "Language-based" / "Sentence-based" / "French" / "Second_barsky.db"
    )
    second_path.parent.mkdir(parents=True)
    second = init_db(str(second_path))
    ensure_sentence_schema(second)
    insert_sentence_card(second, "Je vais.", [("vais", "go")])
    try:
        _, _ = ensure_linked_word_phrase_database(first, str(first_path), str(root))
        shared_uuid = first.execute(
            "SELECT value FROM settings WHERE key='projection_source_uuid'"
        ).fetchone()[0]
        second.execute(
            "INSERT INTO settings (key, value) VALUES ('projection_source_uuid', ?)",
            (shared_uuid,),
        )
        second.commit()
        second_target = default_word_phrase_path_for_sentence(
            str(second_path), str(root)
        )
        os.makedirs(os.path.dirname(second_target), exist_ok=True)
        target = init_db(second_target)
        write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
        target.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            [
                ("projection_owner_version", "1"),
                ("projection_owner_source_uuid", shared_uuid),
                (
                    "projection_owner_source_path",
                    os.path.normpath(os.path.realpath(first_path)),
                ),
            ],
        )
        target.commit()
        target.close()

        with pytest.raises(ProjectionOwnershipConflictError) as error:
            ensure_linked_word_phrase_database(
                second, str(second_path), str(root), sync=True
            )

        assert error.value.conflict["code"] == "word_phrase_projection_owner_mismatch"
        assert get_linked_word_phrase_db(second) is None
    finally:
        first.close()
        second.close()
