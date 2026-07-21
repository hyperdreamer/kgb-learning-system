"""Tests for global expression-sense inventory and word/phrase derivation."""

from __future__ import annotations

import os
import sqlite3

import pytest

from kgb_srs.schema import (
    ensure_sentence_schema,
    init_db,
    insert_sentence_card,
    get_sentence_card,
)
from kgb_srs.senses import (
    create_or_get_sense,
    list_senses_for_expression,
    find_sense_by_meaning,
    backfill_senses_from_items,
    derive_word_phrase_database,
    derive_word_phrase_entries,
)
from kgb_srs.ai_parser import parse_sense_assignment, AIValidationError
from kgb_srs.ai_provider import build_sense_assignment_prompt
from kgb_srs.catalog import DatabaseType, read_database_type


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "senses_barsky.db"
    c = init_db(str(path))
    ensure_sentence_schema(c)
    yield c
    c.close()


class TestSenseInventory:
    def test_create_and_reuse_exact_meaning(self, conn):
        s1 = create_or_get_sense(conn, "insist on", "to demand firmly")
        s2 = create_or_get_sense(conn, "Insist On", "to demand firmly")
        assert s1.id == s2.id
        senses = list_senses_for_expression(conn, "insist on")
        assert len(senses) == 1

    def test_different_meanings_are_different_senses(self, conn):
        s1 = create_or_get_sense(conn, "bank", "financial institution")
        s2 = create_or_get_sense(conn, "bank", "side of a river")
        assert s1.id != s2.id
        senses = list_senses_for_expression(conn, "bank")
        assert len(senses) == 2

    def test_insert_sentence_card_links_sense(self, conn):
        cid = insert_sentence_card(
            conn,
            "He insists on speaking himself.",
            [("insist on", "to demand firmly")],
        )
        result = get_sentence_card(conn, cid)
        expr, meaning, sense_id, surface = result[3][0]
        assert expr == "insist on"
        assert meaning == "to demand firmly"
        assert sense_id is not None
        assert surface == ""
        found = find_sense_by_meaning(conn, "insist on", "to demand firmly")
        assert found is not None
        assert found.id == sense_id

    def test_second_card_reuses_same_sense(self, conn):
        insert_sentence_card(
            conn,
            "He insists on speaking himself.",
            [("insist on", "to demand firmly")],
        )
        insert_sentence_card(
            conn,
            "She insists on silence.",
            [("insist on", "to demand firmly")],
        )
        senses = list_senses_for_expression(conn, "insist on")
        assert len(senses) == 1

    def test_backfill_from_legacy_items(self, conn):
        # Simulate a legacy item without sense_id by direct insert.
        ensure_sentence_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) "
            "VALUES ('Hello world', '', 1, date('now'))"
        )
        card_id = cur.lastrowid
        cur.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (?, 'Hello', 'a greeting')",
            (card_id,),
        )
        conn.commit()
        linked = backfill_senses_from_items(conn)
        assert linked >= 1
        senses = list_senses_for_expression(conn, "Hello")
        assert len(senses) == 1
        assert senses[0].meaning == "a greeting"


class TestSenseAssignmentParser:
    def test_reuse(self):
        raw = (
            '{"expression": "bank", "action": "reuse", '
            '"sense_id": 3, "meaning": ""}'
        )
        a = parse_sense_assignment(raw, "bank", [1, 3, 5])
        assert a.action == "reuse"
        assert a.sense_id == 3

    def test_create(self):
        raw = (
            '{"expression": "bank", "action": "create", '
            '"sense_id": null, "meaning": "side of a river"}'
        )
        a = parse_sense_assignment(raw, "bank", [1, 3])
        assert a.action == "create"
        assert a.sense_id is None
        assert a.meaning == "side of a river"

    def test_reuse_unknown_id_rejected(self):
        raw = (
            '{"expression": "bank", "action": "reuse", '
            '"sense_id": 99, "meaning": ""}'
        )
        with pytest.raises(AIValidationError):
            parse_sense_assignment(raw, "bank", [1, 3])

    def test_create_empty_meaning_rejected(self):
        raw = (
            '{"expression": "bank", "action": "create", '
            '"sense_id": null, "meaning": ""}'
        )
        with pytest.raises(AIValidationError):
            parse_sense_assignment(raw, "bank", [])

    def test_prompt_lists_prior_senses(self):
        prompt = build_sense_assignment_prompt(
            "He went to the bank.",
            "bank",
            [(1, "financial institution"), (2, "river side")],
            explanation_language="English",
        )
        assert "id=1" in prompt
        assert "financial institution" in prompt
        assert "bank" in prompt


class TestDeriveWordPhrase:
    def test_derive_projects_unique_senses(self, conn, tmp_path):
        insert_sentence_card(
            conn,
            "He insists on speaking himself.",
            [("insist on", "to demand firmly")],
        )
        insert_sentence_card(
            conn,
            "She insists on silence.",
            [("insist on", "to demand firmly")],
        )
        insert_sentence_card(
            conn,
            "The bank of the river is steep.",
            [("bank", "side of a river")],
        )
        insert_sentence_card(
            conn,
            "I went to the bank.",
            [("bank", "financial institution")],
        )

        entries = derive_word_phrase_entries(conn)
        by_expr = {e[0].lower(): e for e in entries}
        assert "insist on" in by_expr
        assert "bank" in by_expr
        # bank has two senses
        assert len(by_expr["bank"][2]) == 2
        # insist on has one sense despite two sentences
        assert len(by_expr["insist on"][2]) == 1

        # Back layout: meaning, then indented example with bold surface form.
        insist_back = by_expr["insist on"][1]
        assert "to demand firmly" in insist_back
        assert "\u2003\u2003He **insists on** speaking himself." in insist_back
        assert "*" not in insist_back.replace("**", "")  # no italic-only wrapping

        target_path = tmp_path / "derived_barsky.db"
        target = init_db(str(target_path))
        try:
            stats = derive_word_phrase_database(conn, target)
            assert stats["expressions"] == 2
            assert stats["senses"] == 3
            assert stats["inserted"] == 2
            assert read_database_type(target) == DatabaseType.LANGUAGE_WORD_PHRASE
            cur = target.cursor()
            cur.execute("SELECT front, back FROM cards ORDER BY front")
            rows = cur.fetchall()
            fronts = {r[0].lower() for r in rows}
            assert fronts == {"bank", "insist on"}
            bank_back = next(r[1] for r in rows if r[0].lower() == "bank")
            assert "financial institution" in bank_back
            assert "side of a river" in bank_back
        finally:
            target.close()

    def test_back_highlights_surface_and_indents_example(self):
        from kgb_srs.senses import Sense, build_word_phrase_back_from_senses

        sense = Sense(
            id=1,
            expression="insist on",
            meaning="to demand firmly",
            expression_norm="insist on",
            meaning_norm="to demand firmly",
        )
        back = build_word_phrase_back_from_senses(
            [sense],
            {1: ["He insists on speaking himself."]},
        )
        assert back.startswith("1. to demand firmly")
        assert "\n\n\u2003\u2003He **insists on** speaking himself." in back


class TestLinkedWordPhraseSync:
    def test_ensure_linked_creates_and_syncs(self, conn, tmp_path):
        from kgb_srs.senses import (
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            default_word_phrase_path_for_sentence,
        )
        from kgb_srs.catalog import DB_DIR_LANGUAGE_WORD_PHRASE

        insert_sentence_card(
            conn,
            "He insists on speaking himself.",
            [("insist on", "to demand firmly")],
        )
        # Mimic sentence DB path under a fake root.
        db_root = tmp_path / "db"
        sent_dir = db_root / "Language-based" / "Sentence-based"
        sent_dir.mkdir(parents=True)
        sentence_path = str(sent_dir / "English_barsky.db")

        wp_path, stats = ensure_linked_word_phrase_database(
            conn, sentence_path, str(db_root), sync=True
        )
        assert stats is not None
        assert stats["expressions"] == 1
        assert get_linked_word_phrase_db(conn) is not None
        assert os.path.isfile(wp_path)
        expected = default_word_phrase_path_for_sentence(sentence_path, str(db_root))
        assert os.path.abspath(wp_path) == os.path.abspath(expected)
        assert DB_DIR_LANGUAGE_WORD_PHRASE in wp_path

        # Add another sense and re-ensure (sync)
        insert_sentence_card(
            conn,
            "I went to the bank.",
            [("bank", "financial institution")],
        )
        wp_path2, stats2 = ensure_linked_word_phrase_database(
            conn, sentence_path, str(db_root), sync=True
        )
        assert wp_path2 == wp_path
        assert stats2 is not None
        assert stats2["expressions"] == 2

        target = init_db(wp_path)
        try:
            cur = target.cursor()
            cur.execute("SELECT front FROM cards ORDER BY front")
            fronts = {r[0].lower() for r in cur.fetchall()}
            assert fronts == {"bank", "insist on"}
        finally:
            target.close()

    def test_startup_backfill_links_old_sentence_dbs(self, tmp_path):
        from kgb_srs.senses import (
            ensure_all_sentence_databases_linked,
            get_linked_word_phrase_db,
        )
        from kgb_srs.catalog import (
            DatabaseType,
            write_database_type,
            DB_DIR_LANGUAGE_SENTENCE,
            DB_DIR_LANGUAGE_WORD_PHRASE,
        )
        from kgb_srs.config import ensure_database_root_structure
        from kgb_srs.schema import ensure_sentence_schema

        db_root = tmp_path / "db"
        ensure_database_root_structure(str(db_root))
        sent_path = (
            db_root / DB_DIR_LANGUAGE_SENTENCE / "Legacy_barsky.db"
        )
        conn = init_db(str(sent_path))
        try:
            write_database_type(conn, DatabaseType.LANGUAGE_SENTENCE)
            ensure_sentence_schema(conn)
            insert_sentence_card(
                conn,
                "I went to the bank.",
                [("bank", "financial institution")],
            )
            assert get_linked_word_phrase_db(conn) is None
        finally:
            conn.close()

        results = ensure_all_sentence_databases_linked(str(db_root))
        assert len(results) == 1
        assert results[0]["stats"]["expressions"] == 1
        wp = results[0]["word_phrase_path"]
        assert os.path.isfile(wp)
        assert DB_DIR_LANGUAGE_WORD_PHRASE in wp

        reopened = init_db(str(sent_path))
        try:
            assert get_linked_word_phrase_db(reopened) is not None
        finally:
            reopened.close()

    def test_sync_without_link_returns_none(self, conn):
        from kgb_srs.senses import sync_linked_word_phrase_database

        assert sync_linked_word_phrase_database(conn) is None


# ---------------------------------------------------------------------------
# Audit fixes: W/P upsert SRS, orphan purge, savepoint nesting
# ---------------------------------------------------------------------------


class TestUpsertPreservesSrs:
    """FIX 1: updating W/P projection must not wipe box/next_review."""

    def test_update_preserves_box_and_next_review(self, conn):
        from kgb_srs.senses import upsert_word_phrase_card
        import datetime

        future = (datetime.date.today() + datetime.timedelta(days=400)).isoformat()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) "
            "VALUES (?, ?, ?, ?)",
            ("bank", "old meaning", 4, future),
        )
        conn.commit()
        card_id = int(cur.lastrowid)

        updated_id, action = upsert_word_phrase_card(
            conn, "bank", "financial institution"
        )
        assert action == "updated"
        assert updated_id == card_id

        cur.execute(
            "SELECT front, back, box, next_review FROM cards WHERE id=?",
            (card_id,),
        )
        front, back, box, next_review = cur.fetchone()
        assert front == "bank"
        assert back == "financial institution"
        assert box == 4
        assert next_review == future

    def test_insert_still_starts_box_one_today(self, conn):
        from kgb_srs.senses import upsert_word_phrase_card
        import datetime

        today = datetime.date.today().isoformat()
        card_id, action = upsert_word_phrase_card(conn, "river", "stream of water")
        assert action == "inserted"
        cur = conn.cursor()
        cur.execute(
            "SELECT box, next_review FROM cards WHERE id=?", (card_id,)
        )
        box, next_review = cur.fetchone()
        assert box == 1
        assert next_review == today


class TestOrphanSensePurge:
    """FIX 2: obsolete senses must not stay in W/P projection."""

    def test_update_meaning_purges_old_sense(self, conn):
        from kgb_srs.schema import update_sentence_card
        from kgb_srs.senses import get_sense, derive_word_phrase_entries

        cid = insert_sentence_card(
            conn, "Hello world", [("world", "the earth")]
        )
        old_sid = conn.execute(
            "SELECT sense_id FROM unfamiliar_items WHERE card_id=?",
            (cid,),
        ).fetchone()[0]
        assert get_sense(conn, old_sid) is not None

        update_sentence_card(
            conn,
            cid,
            front="Hello world",
            back="updated",
            items=[("world", "the planet")],
        )

        assert get_sense(conn, old_sid) is None
        entries = derive_word_phrase_entries(conn)
        assert len(entries) == 1
        display, back, senses = entries[0]
        assert display.lower() == "world"
        assert len(senses) == 1
        assert senses[0].meaning == "the planet"
        assert "the earth" not in back
        assert "the planet" in back

    def test_referenced_sense_not_deleted(self, conn):
        from kgb_srs.schema import update_sentence_card
        from kgb_srs.senses import get_sense

        cid1 = insert_sentence_card(
            conn, "Hello world", [("world", "the earth")]
        )
        sid = conn.execute(
            "SELECT sense_id FROM unfamiliar_items WHERE card_id=?",
            (cid1,),
        ).fetchone()[0]
        # Second card still uses the same sense meaning.
        insert_sentence_card(
            conn, "World peace", [("World", "the earth")]
        )
        update_sentence_card(
            conn,
            cid1,
            front="Hello world",
            back="x",
            items=[("world", "other meaning")],
        )
        # Sense still referenced by second card.
        assert get_sense(conn, sid) is not None


class TestCreateOrGetSenseSavepoint:
    """FIX 5: unique conflict must not rollback outer transaction."""

    def test_conflict_preserves_outer_card_row(self, conn):
        from kgb_srs.senses import create_or_get_sense, find_sense_by_meaning
        import kgb_srs.senses as senses_mod

        # Outer uncommitted work.
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) "
            "VALUES ('outer', 'row', 1, '2020-01-01')"
        )
        outer_id = int(cur.lastrowid)

        # Seed a sense so the INSERT path can hit UNIQUE.
        first = create_or_get_sense(conn, "bank", "river side", commit=False)
        assert first.id is not None

        # Force the insert branch even though a row already exists.
        original_find = senses_mod.find_sense_by_meaning

        def find_then_existing(*args, **kwargs):
            # First call inside create_or_get_sense: pretend missing.
            # Subsequent call after conflict: real lookup.
            if getattr(find_then_existing, "_calls", 0) == 0:
                find_then_existing._calls = 1
                return None
            return original_find(*args, **kwargs)

        find_then_existing._calls = 0
        senses_mod.find_sense_by_meaning = find_then_existing
        try:
            recovered = create_or_get_sense(
                conn, "bank", "river side", commit=False
            )
        finally:
            senses_mod.find_sense_by_meaning = original_find

        assert recovered.id == first.id
        still = conn.execute(
            "SELECT id FROM cards WHERE id=?", (outer_id,)
        ).fetchone()
        assert still is not None, "outer transaction row must survive conflict"
