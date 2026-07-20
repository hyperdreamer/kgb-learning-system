"""Tests for global expression-sense inventory and word/phrase derivation."""

from __future__ import annotations

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
        expr, meaning, sense_id = result[3][0]
        assert expr == "insist on"
        assert meaning == "to demand firmly"
        assert sense_id is not None
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
