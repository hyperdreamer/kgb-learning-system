"""Tests for global expression-sense inventory and word/phrase derivation."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from kgb_srs.schema import (
    ensure_sentence_schema,
    init_db,
    insert_sentence_card,
    get_sentence_card,
    update_sentence_card,
)
from kgb_srs.senses import (
    create_or_get_sense,
    ensure_expression_senses_table,
    example_sentences_for_sense,
    find_sense_by_meaning,
    get_sense,
    group_senses_by_expression,
    list_all_senses,
    list_senses_for_expression,
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
    def test_read_helpers_do_not_commit_outer_transaction(self, tmp_path):
        path = tmp_path / "read_helpers_transaction.db"
        transaction_conn = init_db(str(path))
        ensure_sentence_schema(transaction_conn)
        card_id = insert_sentence_card(
            transaction_conn, "Known sentence", [("known", "known meaning")]
        )
        sense_id = transaction_conn.execute(
            "SELECT sense_id FROM unfamiliar_items WHERE card_id=?", (card_id,)
        ).fetchone()[0]

        transaction_conn.execute("BEGIN")
        transaction_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('outer_write', 'value')"
        )

        assert get_sentence_card(transaction_conn, card_id) is not None
        assert get_sense(transaction_conn, sense_id) is not None
        assert find_sense_by_meaning(
            transaction_conn, "known", "known meaning"
        ) is not None
        assert list_senses_for_expression(transaction_conn, "known")
        assert list_all_senses(transaction_conn)
        assert group_senses_by_expression(transaction_conn)
        assert example_sentences_for_sense(transaction_conn, sense_id) == [
            "Known sentence"
        ]

        transaction_conn.rollback()
        transaction_conn.close()

        with sqlite3.connect(path) as verify_conn:
            assert verify_conn.execute(
                "SELECT value FROM settings WHERE key='outer_write'"
            ).fetchone() is None

    def test_fetch_expressions_for_card_does_not_commit_outer_transaction(
        self, tmp_path
    ):
        path = tmp_path / "fetch_expressions_transaction.db"
        transaction_conn = init_db(str(path))
        ensure_sentence_schema(transaction_conn)
        card_id = insert_sentence_card(
            transaction_conn, "Known sentence", [("known", "known meaning")]
        )

        transaction_conn.execute("BEGIN")
        transaction_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('outer_write', 'value')"
        )

        from kgb_srs.main_window import _fetch_expressions_for_card

        items = _fetch_expressions_for_card(transaction_conn, card_id)
        assert len(items) == 1
        assert items[0][0] == "known"
        assert items[0][1] == "known meaning"
        assert items[0][2] is not None
        assert items[0][3] == ""

        transaction_conn.rollback()
        transaction_conn.close()

        with sqlite3.connect(path) as verify_conn:
            assert verify_conn.execute(
                "SELECT value FROM settings WHERE key='outer_write'"
            ).fetchone() is None

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

    def test_backfill_without_commit_rolls_back_outer_transaction(self, tmp_path):
        path = tmp_path / "backfill_transaction.db"
        transaction_conn = init_db(str(path))
        ensure_sentence_schema(transaction_conn)

        insert_sentence_card(
            transaction_conn, "known", [("known", "known meaning")]
        )
        transaction_conn.execute(
            "INSERT INTO cards (front, back, box, next_review) "
            "VALUES ('new legacy', '', 1, date('now'))"
        )
        new_card_id = transaction_conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
        transaction_conn.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (?, 'new', 'new meaning')",
            (new_card_id,),
        )
        transaction_conn.commit()

        transaction_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('outer_write', 'value')"
        )
        assert backfill_senses_from_items(transaction_conn, commit=False) == 1
        assert transaction_conn.execute(
            "SELECT sense_id FROM unfamiliar_items WHERE card_id=?",
            (new_card_id,),
        ).fetchone()[0] is not None

        transaction_conn.rollback()
        transaction_conn.close()

        with sqlite3.connect(path) as verify_conn:
            assert verify_conn.execute(
                "SELECT value FROM settings WHERE key='outer_write'"
            ).fetchone() is None
            assert verify_conn.execute(
                "SELECT sense_id FROM unfamiliar_items WHERE card_id=?",
                (new_card_id,),
            ).fetchone()[0] is None
            assert verify_conn.execute(
                "SELECT id FROM expression_senses WHERE expression='new'"
            ).fetchone() is None


class TestSentenceCardSenseAtomicity:
    def test_insert_failure_rolls_back_outer_transaction(self, tmp_path):
        path = tmp_path / "insert_sentence_transaction.db"
        transaction_conn = init_db(str(path))
        ensure_sentence_schema(transaction_conn)
        transaction_conn.execute(
            """
            CREATE TRIGGER abort_second_unfamiliar_item
            BEFORE INSERT ON unfamiliar_items
            WHEN NEW.expression = 'second'
            BEGIN
                SELECT RAISE(ABORT, 'second child rejected');
            END
            """
        )
        transaction_conn.commit()

        transaction_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('outer_write', 'value')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="second child rejected"):
            insert_sentence_card(
                transaction_conn,
                "first second",
                [("first", "first meaning"), ("second", "second meaning")],
            )
        transaction_conn.close()

        with sqlite3.connect(path) as verify_conn:
            assert verify_conn.execute(
                "SELECT value FROM settings WHERE key='outer_write'"
            ).fetchone() is None
            assert verify_conn.execute("SELECT id FROM cards").fetchone() is None
            assert verify_conn.execute(
                "SELECT id FROM unfamiliar_items"
            ).fetchone() is None

    def test_update_failure_rolls_back_outer_transaction(self, tmp_path):
        path = tmp_path / "update_sentence_transaction.db"
        transaction_conn = init_db(str(path))
        card_id = insert_sentence_card(
            transaction_conn, "known", [("known", "known meaning")]
        )
        transaction_conn.execute(
            """
            CREATE TRIGGER abort_second_replacement_item
            BEFORE INSERT ON unfamiliar_items
            WHEN NEW.expression = 'second'
            BEGIN
                SELECT RAISE(ABORT, 'second replacement rejected');
            END
            """
        )
        transaction_conn.commit()

        transaction_conn.execute(
            "INSERT INTO settings (key, value) VALUES ('outer_write', 'value')"
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="second replacement rejected"
        ):
            update_sentence_card(
                transaction_conn,
                card_id,
                front="first second",
                back="replacement back",
                items=[("first", "first meaning"), ("second", "second meaning")],
            )
        transaction_conn.close()

        with sqlite3.connect(path) as verify_conn:
            assert verify_conn.execute(
                "SELECT value FROM settings WHERE key='outer_write'"
            ).fetchone() is None
            assert verify_conn.execute(
                "SELECT front, back FROM cards WHERE id=?", (card_id,)
            ).fetchone() == ("known", "")
            assert verify_conn.execute(
                "SELECT expression, meaning FROM unfamiliar_items WHERE card_id=?",
                (card_id,),
            ).fetchall() == [("known", "known meaning")]

    def test_insert_rolls_back_senses_when_second_child_insert_fails(self, conn):
        ensure_expression_senses_table(conn)
        conn.execute(
            """
            CREATE TRIGGER abort_second_unfamiliar_item
            BEFORE INSERT ON unfamiliar_items
            WHEN NEW.expression = 'second'
            BEGIN
                SELECT RAISE(ABORT, 'second child rejected');
            END
            """
        )
        conn.commit()

        before_cards = conn.execute(
            "SELECT id, front, back, box, next_review FROM cards ORDER BY id"
        ).fetchall()
        before_items = conn.execute(
            "SELECT id, card_id, expression, meaning, sense_id, surface_form "
            "FROM unfamiliar_items ORDER BY id"
        ).fetchall()
        before_senses = conn.execute(
            "SELECT id, expression, meaning, expression_norm, meaning_norm "
            "FROM expression_senses ORDER BY id"
        ).fetchall()

        with pytest.raises(sqlite3.IntegrityError, match="second child rejected"):
            insert_sentence_card(
                conn,
                "first second",
                [("first", "first meaning"), ("second", "second meaning")],
            )

        assert conn.execute(
            "SELECT id, front, back, box, next_review FROM cards ORDER BY id"
        ).fetchall() == before_cards
        assert conn.execute(
            "SELECT id, card_id, expression, meaning, sense_id, surface_form "
            "FROM unfamiliar_items ORDER BY id"
        ).fetchall() == before_items
        assert conn.execute(
            "SELECT id, expression, meaning, expression_norm, meaning_norm "
            "FROM expression_senses ORDER BY id"
        ).fetchall() == before_senses

    def test_update_rolls_back_senses_when_second_child_insert_fails(self, conn):
        card_id = insert_sentence_card(
            conn, "known", [("known", "known meaning")], back="original back"
        )
        conn.execute(
            "UPDATE cards SET box=4, next_review='2035-01-02' WHERE id=?",
            (card_id,),
        )
        conn.commit()

        before_card = conn.execute(
            "SELECT id, front, back, box, next_review FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        before_items = conn.execute(
            "SELECT id, card_id, expression, meaning, sense_id, surface_form "
            "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
            (card_id,),
        ).fetchall()
        before_senses = conn.execute(
            "SELECT id, expression, meaning, expression_norm, meaning_norm "
            "FROM expression_senses ORDER BY id"
        ).fetchall()

        ensure_expression_senses_table(conn)
        conn.execute(
            """
            CREATE TRIGGER abort_second_replacement_item
            BEFORE INSERT ON unfamiliar_items
            WHEN NEW.expression = 'second'
            BEGIN
                SELECT RAISE(ABORT, 'second replacement rejected');
            END
            """
        )
        conn.commit()

        with pytest.raises(
            sqlite3.IntegrityError, match="second replacement rejected"
        ):
            update_sentence_card(
                conn,
                card_id,
                front="first second",
                back="replacement back",
                items=[("first", "first meaning"), ("second", "second meaning")],
            )

        assert conn.execute(
            "SELECT id, front, back, box, next_review FROM cards WHERE id=?",
            (card_id,),
        ).fetchone() == before_card
        assert conn.execute(
            "SELECT id, card_id, expression, meaning, sense_id, surface_form "
            "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
            (card_id,),
        ).fetchall() == before_items
        assert conn.execute(
            "SELECT id, expression, meaning, expression_norm, meaning_norm "
            "FROM expression_senses ORDER BY id"
        ).fetchall() == before_senses

    def test_update_rolls_back_when_orphan_cleanup_fails(self, conn, monkeypatch):
        card_id = insert_sentence_card(
            conn, "known", [("known", "known meaning")], back="original back"
        )
        before_card = conn.execute(
            "SELECT id, front, back, box, next_review FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        before_items = conn.execute(
            "SELECT id, card_id, expression, meaning, sense_id, surface_form "
            "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
            (card_id,),
        ).fetchall()
        before_senses = conn.execute(
            "SELECT id, expression, meaning, expression_norm, meaning_norm "
            "FROM expression_senses ORDER BY id"
        ).fetchall()

        from kgb_srs import senses

        cleanup = senses.purge_orphan_senses

        def fail_after_cleanup(*args, **kwargs):
            cleanup(*args, **kwargs)
            raise RuntimeError("cleanup failed")

        monkeypatch.setattr(senses, "purge_orphan_senses", fail_after_cleanup)

        with pytest.raises(RuntimeError, match="cleanup failed"):
            update_sentence_card(
                conn,
                card_id,
                front="replacement",
                back="replacement back",
                items=[("replacement", "replacement meaning")],
            )

        assert conn.execute(
            "SELECT id, front, back, box, next_review FROM cards WHERE id=?",
            (card_id,),
        ).fetchone() == before_card
        assert conn.execute(
            "SELECT id, card_id, expression, meaning, sense_id, surface_form "
            "FROM unfamiliar_items WHERE card_id=? ORDER BY id",
            (card_id,),
        ).fetchall() == before_items
        assert conn.execute(
            "SELECT id, expression, meaning, expression_norm, meaning_norm "
            "FROM expression_senses ORDER BY id"
        ).fetchall() == before_senses


class TestSenseAssignmentParser:
    @pytest.mark.parametrize("sense_id", [1, 3])
    def test_reuse_json_integer_id_accepted(self, sense_id):
        raw = json.dumps({
            "expression": "bank",
            "action": "reuse",
            "sense_id": sense_id,
            "meaning": "",
        })
        a = parse_sense_assignment(raw, "bank", [1, 3, 5])
        assert a.action == "reuse"
        assert a.sense_id == sense_id

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

    @pytest.mark.parametrize("sense_id", [True, False, 1.0, 1.5])
    def test_reuse_boolean_or_float_id_rejected(self, sense_id):
        raw = json.dumps({
            "expression": "bank",
            "action": "reuse",
            "sense_id": sense_id,
            "meaning": "",
        })
        with pytest.raises(AIValidationError, match="sense_id"):
            parse_sense_assignment(raw, "bank", [1, 3])

    @pytest.mark.parametrize("sense_id", ["1", " 3 "])
    def test_reuse_string_id_rejected(self, sense_id):
        raw = json.dumps({
            "expression": "bank",
            "action": "reuse",
            "sense_id": sense_id,
            "meaning": "",
        })
        with pytest.raises(AIValidationError, match="sense_id"):
            parse_sense_assignment(raw, "bank", [1, 3])

    @pytest.mark.parametrize("sense_id", [True, False, 1.0, 1.5])
    def test_create_ignores_boolean_or_float_preferred_id(self, sense_id):
        raw = json.dumps({
            "expression": "bank",
            "action": "create",
            "sense_id": sense_id,
            "meaning": "side of a river",
        })
        assignment = parse_sense_assignment(raw, "bank", [1, 3])
        assert assignment.sense_id is None

    @pytest.mark.parametrize("value", [[], {}, 1])
    @pytest.mark.parametrize("field", ["expression", "action", "meaning"])
    def test_protocol_text_fields_must_be_strings(self, field, value):
        payload = {
            "expression": "bank",
            "action": "create",
            "sense_id": None,
            "meaning": "side of a river",
        }
        payload[field] = value
        with pytest.raises(AIValidationError, match=field):
            parse_sense_assignment(json.dumps(payload), "bank", [1, 3])

    @pytest.mark.parametrize("preferred_sense_id", [True, False, 1.0, 1.5])
    def test_insert_ignores_boolean_or_float_preferred_sense_id(
        self, conn, preferred_sense_id
    ):
        existing = create_or_get_sense(conn, "world", "old meaning")

        card_id = insert_sentence_card(
            conn,
            "World",
            [("world", "new meaning", preferred_sense_id)],
        )

        _, _, _, items = get_sentence_card(conn, card_id)
        assert items[0][1] == "new meaning"
        assert items[0][2] != existing.id

    @pytest.mark.parametrize("preferred_sense_id", [True, False, 1.0, 1.5])
    def test_update_ignores_boolean_or_float_preferred_sense_id(
        self, conn, preferred_sense_id
    ):
        existing = create_or_get_sense(conn, "world", "old meaning")
        card_id = insert_sentence_card(
            conn, "World", [("world", "initial meaning")]
        )

        update_sentence_card(
            conn,
            card_id,
            front="World",
            back="",
            items=[("world", "new meaning", preferred_sense_id)],
        )

        _, _, _, items = get_sentence_card(conn, card_id)
        assert items[0][1] == "new meaning"
        assert items[0][2] != existing.id

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
    def test_direct_sync_of_explicit_word_phrase_link_projects_data(
        self, conn, tmp_path
    ):
        from kgb_srs.catalog import write_database_type
        from kgb_srs.senses import (
            set_linked_word_phrase_db,
            sync_linked_word_phrase_database,
        )

        insert_sentence_card(
            conn,
            "I went to the bank.",
            [("bank", "financial institution")],
        )
        target_path = tmp_path / "manual_word_phrase_barsky.db"
        target = init_db(str(target_path))
        try:
            write_database_type(target, DatabaseType.LANGUAGE_WORD_PHRASE)
        finally:
            target.close()
        set_linked_word_phrase_db(conn, str(target_path))

        stats = sync_linked_word_phrase_database(conn)

        assert stats is not None
        assert stats["expressions"] == 1
        target = init_db(str(target_path))
        try:
            assert target.execute("SELECT front FROM cards").fetchall() == [
                ("bank",)
            ]
        finally:
            target.close()

    def test_direct_sync_ignores_knowledge_link_without_mutating_it(
        self, conn, tmp_path
    ):
        from kgb_srs.catalog import write_database_type
        from kgb_srs.senses import (
            set_linked_word_phrase_db,
            sync_linked_word_phrase_database,
        )

        insert_sentence_card(
            conn,
            "I went to the bank.",
            [("bank", "financial institution")],
        )
        knowledge_path = tmp_path / "manual_knowledge_barsky.db"
        knowledge = init_db(str(knowledge_path))
        try:
            write_database_type(knowledge, DatabaseType.KNOWLEDGE)
            knowledge.execute(
                "INSERT INTO cards (front, back, box, next_review) "
                "VALUES (?, ?, ?, ?)",
                ("history", "must survive", 3, "2030-01-01"),
            )
            knowledge.commit()
        finally:
            knowledge.close()
        set_linked_word_phrase_db(conn, str(knowledge_path))

        assert sync_linked_word_phrase_database(conn) is None

        knowledge = init_db(str(knowledge_path))
        try:
            assert read_database_type(knowledge) == DatabaseType.KNOWLEDGE
            assert knowledge.execute("SELECT front, back FROM cards").fetchall() == [
                ("history", "must survive")
            ]
        finally:
            knowledge.close()

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

    def test_ensure_replaces_legacy_knowledge_link_without_mutating_it(
        self, conn, tmp_path
    ):
        from kgb_srs.catalog import write_database_type
        from kgb_srs.senses import (
            default_word_phrase_path_for_sentence,
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            set_linked_word_phrase_db,
            sync_linked_word_phrase_database,
        )

        insert_sentence_card(
            conn,
            "He insists on speaking himself.",
            [("insist on", "to demand firmly")],
        )
        db_root = tmp_path / "db"
        sentence_path = (
            db_root / "Language-based" / "Sentence-based" / "English_barsky.db"
        )
        knowledge_path = db_root / "Knowledge-based" / "History_barsky.db"
        knowledge_path.parent.mkdir(parents=True)
        knowledge = init_db(str(knowledge_path))
        try:
            write_database_type(knowledge, DatabaseType.KNOWLEDGE)
            knowledge.execute(
                "INSERT INTO cards (front, back, box, next_review) "
                "VALUES (?, ?, ?, ?)",
                ("distinctive knowledge card", "must survive", 3, "2030-01-01"),
            )
            knowledge.commit()
        finally:
            knowledge.close()

        set_linked_word_phrase_db(conn, str(knowledge_path))
        wp_path, stats = ensure_linked_word_phrase_database(
            conn, str(sentence_path), str(db_root), sync=True
        )

        expected = default_word_phrase_path_for_sentence(
            str(sentence_path), str(db_root)
        )
        assert get_linked_word_phrase_db(conn) == os.path.abspath(expected)
        assert wp_path == expected
        assert stats is not None

        knowledge = init_db(str(knowledge_path))
        try:
            assert read_database_type(knowledge) == DatabaseType.KNOWLEDGE
            assert knowledge.execute(
                "SELECT front, back FROM cards"
            ).fetchall() == [("distinctive knowledge card", "must survive")]
        finally:
            knowledge.close()

        target = init_db(expected)
        try:
            assert target.execute("SELECT front FROM cards").fetchall() == [
                ("insist on",)
            ]
        finally:
            target.close()

    def test_ensure_accepts_canonical_symlink_link(self, conn, tmp_path):
        from kgb_srs.senses import (
            default_word_phrase_path_for_sentence,
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            set_linked_word_phrase_db,
        )

        insert_sentence_card(
            conn,
            "I went to the bank.",
            [("bank", "financial institution")],
        )
        db_root = tmp_path / "db"
        sentence_path = (
            db_root / "Language-based" / "Sentence-based" / "English_barsky.db"
        )
        expected = default_word_phrase_path_for_sentence(
            str(sentence_path), str(db_root)
        )
        os.makedirs(os.path.dirname(expected), exist_ok=True)
        target = init_db(expected)
        target.close()
        link_path = tmp_path / "canonical-projection-link.db"
        try:
            os.symlink(expected, link_path)
        except (AttributeError, NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")

        set_linked_word_phrase_db(conn, str(link_path))
        wp_path, stats = ensure_linked_word_phrase_database(
            conn, str(sentence_path), str(db_root), sync=True
        )

        assert wp_path == os.path.abspath(str(link_path))
        assert get_linked_word_phrase_db(conn) == os.path.abspath(str(link_path))
        assert stats is not None
        target = init_db(expected)
        try:
            assert target.execute("SELECT front FROM cards").fetchall() == [
                ("bank",)
            ]
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

    def test_unicode_casefold_match_updates_existing_card_and_preserves_srs(self, conn):
        from kgb_srs.senses import upsert_word_phrase_card
        import datetime

        future = (datetime.date.today() + datetime.timedelta(days=400)).isoformat()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cards (front, back, box, next_review) "
            "VALUES (?, ?, ?, ?)",
            ("École", "old meaning", 4, future),
        )
        conn.commit()
        card_id = int(cur.lastrowid)

        updated_id, action = upsert_word_phrase_card(conn, "école", "school")

        assert action == "updated"
        assert updated_id == card_id
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1
        front, back, box, next_review = conn.execute(
            "SELECT front, back, box, next_review FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        assert front == "école"
        assert back == "school"
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


# ---------------------------------------------------------------------------
# Projection safety and normalized W/P duplicate conflicts
# ---------------------------------------------------------------------------


class TestProjectionSafety:
    def test_nested_sentence_paths_use_distinct_mirrored_targets_and_do_not_prune(
        self, tmp_path
    ):
        from kgb_srs.senses import ensure_linked_word_phrase_database

        db_root = tmp_path / "db"
        sentence_root = db_root / "Language-based" / "Sentence-based"
        english_path = sentence_root / "English" / "A1_barsky.db"
        french_path = sentence_root / "French" / "A1_barsky.db"
        english_path.parent.mkdir(parents=True)
        french_path.parent.mkdir(parents=True)
        english = init_db(str(english_path))
        french = init_db(str(french_path))
        try:
            ensure_sentence_schema(english)
            ensure_sentence_schema(french)
            insert_sentence_card(
                english, "The English bank.", [("bank", "financial institution")]
            )
            insert_sentence_card(
                french, "La rivière.", [("rivière", "river")]
            )

            english_target, _ = ensure_linked_word_phrase_database(
                english, str(english_path), str(db_root)
            )
            french_target, _ = ensure_linked_word_phrase_database(
                french, str(french_path), str(db_root)
            )
        finally:
            english.close()
            french.close()

        assert english_target == str(
            db_root / "Language-based" / "Word-Phrase-based" / "English" / "A1_barsky.db"
        )
        assert french_target == str(
            db_root / "Language-based" / "Word-Phrase-based" / "French" / "A1_barsky.db"
        )
        assert english_target != french_target
        english_target_conn = init_db(english_target)
        french_target_conn = init_db(french_target)
        try:
            assert english_target_conn.execute("SELECT front FROM cards").fetchall() == [
                ("bank",)
            ]
            assert french_target_conn.execute("SELECT front FROM cards").fetchall() == [
                ("rivière",)
            ]
        finally:
            english_target_conn.close()
            french_target_conn.close()

    def test_canonical_symlink_escape_does_not_mutate_source_or_external_target(
        self, conn, tmp_path
    ):
        from kgb_srs.senses import (
            ProjectionPathSafetyError,
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            set_linked_word_phrase_db,
            sync_linked_word_phrase_database,
        )

        db_root = tmp_path / "db"
        sentence_path = (
            db_root / "Language-based" / "Sentence-based" / "English" / "A1_barsky.db"
        )
        sentence_path.parent.mkdir(parents=True)
        external = tmp_path / "external"
        external.mkdir()
        marker = external / "must-not-change.txt"
        marker.write_text("unchanged", encoding="utf-8")
        word_phrase_root = db_root / "Language-based" / "Word-Phrase-based"
        word_phrase_root.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(external, word_phrase_root)
        except (AttributeError, NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")

        old_link = str(tmp_path / "existing-link.db")
        set_linked_word_phrase_db(conn, old_link)
        settings_before = conn.execute(
            "SELECT key, value FROM settings ORDER BY key"
        ).fetchall()
        external_before = sorted((path.name, path.read_bytes()) for path in external.iterdir())

        with pytest.raises(ProjectionPathSafetyError) as ensure_error:
            ensure_linked_word_phrase_database(
                conn, str(sentence_path), str(db_root), sync=True
            )
        assert ensure_error.value.conflict["code"] == "sentence_projection_target_escapes_root"
        with pytest.raises(ProjectionPathSafetyError):
            sync_linked_word_phrase_database(
                conn, sentence_db_path=str(sentence_path), db_root=str(db_root)
            )

        assert get_linked_word_phrase_db(conn) == os.path.abspath(old_link)
        assert conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall() == settings_before
        assert sorted((path.name, path.read_bytes()) for path in external.iterdir()) == external_before

    def test_populated_legacy_flat_target_is_not_relinked_for_nested_source(
        self, conn, tmp_path
    ):
        from kgb_srs.senses import (
            ProjectionPathSafetyError,
            default_word_phrase_path_for_sentence,
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            set_linked_word_phrase_db,
            sync_linked_word_phrase_database,
        )

        db_root = tmp_path / "db"
        sentence_path = (
            db_root / "Language-based" / "Sentence-based" / "English" / "A1_barsky.db"
        )
        sentence_path.parent.mkdir(parents=True)
        legacy_path = db_root / "Language-based" / "Word-Phrase-based" / "A1_barsky.db"
        legacy_path.parent.mkdir(parents=True)
        legacy = init_db(str(legacy_path))
        try:
            legacy.execute(
                "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
                ("legacy", "must survive", 4, "2030-01-01"),
            )
            legacy.commit()
        finally:
            legacy.close()
        legacy_bytes = legacy_path.read_bytes()
        set_linked_word_phrase_db(conn, str(legacy_path))

        with pytest.raises(ProjectionPathSafetyError) as error:
            ensure_linked_word_phrase_database(
                conn, str(sentence_path), str(db_root), sync=True
            )

        mirrored_path = default_word_phrase_path_for_sentence(
            str(sentence_path), str(db_root)
        )
        assert error.value.conflict["code"] == "legacy_word_phrase_projection_conflict"
        assert get_linked_word_phrase_db(conn) == os.path.abspath(str(legacy_path))
        assert legacy_path.read_bytes() == legacy_bytes
        assert not os.path.exists(mirrored_path)
        with pytest.raises(ProjectionPathSafetyError):
            sync_linked_word_phrase_database(
                conn, sentence_db_path=str(sentence_path), db_root=str(db_root)
            )

    def test_empty_legacy_flat_target_is_replaced_by_nested_mirror(
        self, conn, tmp_path
    ):
        from kgb_srs.senses import (
            default_word_phrase_path_for_sentence,
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            set_linked_word_phrase_db,
        )

        db_root = tmp_path / "db"
        sentence_path = (
            db_root / "Language-based" / "Sentence-based" / "English" / "A1_barsky.db"
        )
        sentence_path.parent.mkdir(parents=True)
        legacy_path = db_root / "Language-based" / "Word-Phrase-based" / "A1_barsky.db"
        legacy_path.parent.mkdir(parents=True)
        legacy = init_db(str(legacy_path))
        legacy.close()
        set_linked_word_phrase_db(conn, str(legacy_path))

        target_path, stats = ensure_linked_word_phrase_database(
            conn, str(sentence_path), str(db_root), sync=True
        )

        expected = default_word_phrase_path_for_sentence(
            str(sentence_path), str(db_root)
        )
        assert target_path == expected
        assert stats is not None
        assert get_linked_word_phrase_db(conn) == expected
        assert os.path.isfile(expected)

    def test_source_outside_sentence_root_is_rejected_before_link_mutation(
        self, conn, tmp_path
    ):
        from kgb_srs.senses import (
            ProjectionPathSafetyError,
            ensure_linked_word_phrase_database,
            get_linked_word_phrase_db,
            set_linked_word_phrase_db,
        )

        old_link = str(tmp_path / "existing-link.db")
        set_linked_word_phrase_db(conn, old_link)
        outside_source = tmp_path / "outside" / "A1_barsky.db"

        with pytest.raises(ProjectionPathSafetyError) as error:
            ensure_linked_word_phrase_database(
                conn, str(outside_source), str(tmp_path / "db"), sync=True
            )

        assert error.value.conflict["code"] == "sentence_projection_source_outside_root"
        assert get_linked_word_phrase_db(conn) == os.path.abspath(old_link)


class TestWordPhraseDuplicateSafety:
    def test_duplicate_scanner_groups_unicode_nfc_nfd_and_casefold(self, conn):
        from kgb_srs.senses import find_normalized_word_phrase_duplicates

        conn.executemany(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, 1, '2030-01-01')",
            [
                ("École", "one"),
                ("e\u0301COLE", "two"),
                ("BANK", "three"),
                ("bank", "four"),
            ],
        )
        conn.commit()

        groups = find_normalized_word_phrase_duplicates(conn)

        assert [(group.normalized_front, group.fronts) for group in groups] == [
            ("bank", ("BANK", "bank")),
            ("école", ("École", "e\u0301COLE")),
        ]
        assert [group.card_ids for group in groups] == [(3, 4), (1, 2)]

    def test_upsert_duplicate_conflict_does_not_mutate_cards(self, conn):
        from kgb_srs.senses import (
            WordPhraseDuplicateConflictError,
            upsert_word_phrase_card,
        )

        conn.executemany(
            "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
            [
                ("École", "first", 2, "2030-01-01"),
                ("e\u0301cole", "second", 5, "2031-01-01"),
            ],
        )
        conn.commit()
        before = conn.execute(
            "SELECT id, front, back, box, next_review FROM cards ORDER BY id"
        ).fetchall()

        with pytest.raises(WordPhraseDuplicateConflictError) as error:
            upsert_word_phrase_card(conn, "ÉCOLE", "new projection")

        assert error.value.conflict["normalized_front"] == "école"
        assert conn.execute(
            "SELECT id, front, back, box, next_review FROM cards ORDER BY id"
        ).fetchall() == before

    def test_derive_conflict_preserves_duplicates_and_projects_other_entries(
        self, conn, tmp_path
    ):
        insert_sentence_card(conn, "The bank is open.", [("bank", "financial institution")])
        insert_sentence_card(conn, "A river runs here.", [("river", "watercourse")])
        target = init_db(str(tmp_path / "word_phrase_barsky.db"))
        try:
            target.executemany(
                "INSERT INTO cards (front, back, box, next_review) VALUES (?, ?, ?, ?)",
                [
                    ("BANK", "first history", 2, "2030-01-01"),
                    ("bank", "second history", 5, "2031-01-01"),
                ],
            )
            target.commit()
            before_duplicates = target.execute(
                "SELECT id, front, back, box, next_review FROM cards ORDER BY id"
            ).fetchall()

            stats = derive_word_phrase_database(conn, target)

            assert stats["inserted"] == 1
            assert stats["updated"] == 0
            assert stats["pruned"] == 0
            assert stats["conflicts"] == [
                {
                    "code": "normalized_word_phrase_front_duplicates",
                    "normalized_front": "bank",
                    "cards": [
                        {"id": before_duplicates[0][0], "front": "BANK"},
                        {"id": before_duplicates[1][0], "front": "bank"},
                    ],
                }
            ]
            assert target.execute(
                "SELECT id, front, back, box, next_review FROM cards WHERE id IN (?, ?) ORDER BY id",
                (before_duplicates[0][0], before_duplicates[1][0]),
            ).fetchall() == before_duplicates
            assert target.execute(
                "SELECT front, back FROM cards WHERE front='river'"
            ).fetchone()[0] == "river"
        finally:
            target.close()


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
        from kgb_srs.senses import create_or_get_sense
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
