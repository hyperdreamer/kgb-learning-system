"""Tests for kgb_srs.search — database search across card types."""

import gc
import sqlite3
import weakref
import pytest

from kgb_srs import search
from kgb_srs.search import (
    search_sentence_cards,
    search_word_phrase_cards,
    parse_search_tokens,
)
from kgb_srs.schema import (
    init_db,
    ensure_unfamiliar_items_table,
)


# ---------------------------------------------------------------------------
# Search-function registration
# ---------------------------------------------------------------------------

class TestSearchFunctionRegistration:
    def test_weakrefable_connection_is_registered_once_and_not_retained(self):
        class TrackingConnection(sqlite3.Connection):
            registrations = 0

            def create_function(self, *args, **kwargs):
                type(self).registrations += 1
                return super().create_function(*args, **kwargs)

        search._REGISTERED_CONNS.clear()
        conn = sqlite3.connect(":memory:", factory=TrackingConnection)
        search._register_search_functions(conn)
        search._register_search_functions(conn)

        assert TrackingConnection.registrations == 1
        ref = weakref.ref(conn)
        conn.close()
        del conn
        gc.collect()
        assert ref() is None
        assert not search._REGISTERED_CONNS

    def test_standard_connection_is_not_retained_and_search_function_works(self):
        search._REGISTERED_CONNS.clear()
        conn = sqlite3.connect(":memory:")
        try:
            search._register_search_functions(conn)
            search._register_search_functions(conn)
            assert not search._REGISTERED_CONNS
            assert conn.execute(
                "SELECT kgb_contains('Caf\u00e9', 'cafe')"
            ).fetchone() == (1,)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# parse_search_tokens
# ---------------------------------------------------------------------------

class TestParseSearchTokens:
    def test_simple_and(self):
        """Plain multi-word query is a single literal phrase operand."""
        groups = parse_search_tokens("hello world")
        assert groups == [["hello world"]]

    def test_explicit_and(self):
        groups = parse_search_tokens("foo AND bar")
        assert groups == [["foo", "bar"]]

    def test_explicit_or(self):
        groups = parse_search_tokens("foo OR bar")
        assert groups == [["foo"], ["bar"]]

    def test_mixed_and_or(self):
        """'math AND theorem OR topology' -> two OR groups with AND inside."""
        groups = parse_search_tokens("math AND theorem OR topology")
        assert groups == [["math", "theorem"], ["topology"]]

    def test_mixed_or_and(self):
        """'alpha OR beta AND gamma' -> OR-first groups."""
        groups = parse_search_tokens("alpha OR beta AND gamma")
        assert groups == [["alpha"], ["beta", "gamma"]]

    def test_first_operator_creates_or_groups(self):
        groups = parse_search_tokens("x AND y OR z")
        assert groups == [["x", "y"], ["z"]]

    def test_empty_input(self):
        groups = parse_search_tokens("")
        assert groups == []

    def test_case_insensitive_operators(self):
        groups = parse_search_tokens("x and y")
        assert groups == [["x", "y"]]
        groups2 = parse_search_tokens("x or y")
        assert groups2 == [["x"], ["y"]]

    def test_operator_as_literal_when_not_alone(self):
        """'AND' or 'OR' inside a larger word should not trigger logic change."""
        groups = parse_search_tokens("SANDWICH ORANGE")
        assert groups == [["SANDWICH ORANGE"]]


# ============================================================================
# Mixed search semantics — OR groups of AND operands
# ============================================================================

class TestMixedSearchSemantics:
    """Tests for mixed AND/OR search with sentence cards."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning
        migrate_unfamiliar_items_meaning(c)
        # Card 1: alpha + nope together
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'alpha and nope together', 'back1', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'alpha', 'first'), (1, 'nope', 'second')"
        )
        # Card 2: beta only
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'beta only', 'back2', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (2, 'beta', 'just beta')"
        )
        # Card 3: alpha and beta together
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (3, 'alpha beta together', 'back3', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (3, 'alpha', 'a'), (3, 'beta', 'b')"
        )
        c.commit()
        yield c
        c.close()

    def test_alpha_and_nope_or_beta_matches_beta(self, conn):
        """(alpha AND nope) OR beta -> should match card 2 (beta only)."""
        results = search_sentence_cards(conn, "alpha AND nope OR beta")
        ids = {r["id"] for r in results}
        # Card 2 has beta but not alpha+nope; card 3 has alpha+beta
        assert 2 in ids

    def test_alpha_and_beta_or_nope_matches_alpha_beta_card(self, conn):
        """alpha AND beta OR nope -> card 3 has alpha+beta."""
        results = search_sentence_cards(conn, "alpha AND beta OR nope")
        ids = {r["id"] for r in results}
        assert 3 in ids

    def test_plain_new_york_is_literal(self, conn):
        """Plain query 'new york' is one literal operand."""
        groups = parse_search_tokens("new york")
        assert groups == [["new york"]]

    def test_and_terms_can_match_different_child_rows(self, conn):
        """AND terms can match different child rows of same card.
        Card 1 has alpha and nope in separate rows."""
        results = search_sentence_cards(conn, "alpha AND nope")
        ids = {r["id"] for r in results}
        assert 1 in ids


# ---------------------------------------------------------------------------
# search_sentence_cards — with DB
# ---------------------------------------------------------------------------

class TestSearchSentenceCards:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        # Insert test data
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'Bonjour le monde', 'Hello world', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression) VALUES (1, 'Bonjour')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression) VALUES (1, 'monde')"
        )
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'Merci beaucoup', 'Thank you very much', 2, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression) VALUES (2, 'Merci')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression) VALUES (2, 'beaucoup')"
        )
        c.commit()
        yield c
        c.close()

    def test_search_sentence_field(self, conn):
        results = search_sentence_cards(conn, "Bonjour", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_search_expression_field(self, conn):
        results = search_sentence_cards(conn, "beaucoup", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 2

    def test_search_back_field(self, conn):
        results = search_sentence_cards(conn, "Thank you", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 2

    def test_and_logic(self, conn):
        results = search_sentence_cards(conn, "Bonjour AND monde", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_or_logic(self, conn):
        results = search_sentence_cards(conn, "Bonjour OR Merci", "OR")
        assert len(results) == 2

    def test_no_match(self, conn):
        results = search_sentence_cards(conn, "xyznotfound", "AND")
        assert len(results) == 0

    def test_empty_query_returns_all(self, conn):
        results = search_sentence_cards(conn, "", "AND")
        assert len(results) == 2

    def test_result_structure(self, conn):
        results = search_sentence_cards(conn, "Bonjour", "AND")
        r = results[0]
        assert "id" in r
        assert "front" in r
        assert "back" in r
        assert "box" in r
        assert "next_review" in r
        assert "expressions" in r
        assert "Bonjour" in r["expressions"]


# ---------------------------------------------------------------------------
# search_word_phrase_cards — with DB
# ---------------------------------------------------------------------------

class TestSearchWordPhraseCards:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'chat', 'cat - a domestic feline', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'chien', 'dog', 2, '2026-01-01')"
        )
        c.commit()
        yield c
        c.close()

    def test_search_front(self, conn):
        results = search_word_phrase_cards(conn, "chat", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_search_back(self, conn):
        results = search_word_phrase_cards(conn, "feline", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_and_logic(self, conn):
        results = search_word_phrase_cards(conn, "chat AND feline", "AND")
        assert len(results) == 1

    def test_or_logic(self, conn):
        results = search_word_phrase_cards(conn, "chat OR chien", "OR")
        assert len(results) == 2
