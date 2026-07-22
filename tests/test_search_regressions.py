"""Regression tests for search tokenization and result matching."""

import json
import sqlite3

import pytest

from kgb_srs.ai_parser import (
    AIValidationError,
    MAX_WORD_PHRASE_MEANINGS,
    parse_sentence_meanings,
    parse_word_phrase_meanings,
)
from kgb_srs.schema import ensure_unfamiliar_items_table, init_db
from kgb_srs.search import (
    parse_search_tokens,
    search_sentence_cards,
    search_word_phrase_cards,
)


class TestSentenceParserValidation:
    """Sentence parser must validate identity, order, and non-emptiness."""

    def test_expressions_must_match_order(self):
        """Returned expressions must match expected in order."""
        response = json.dumps(
            {
                "items": [
                    {"expression": "ici", "contextual_meaning": "here"},
                    {"expression": "suis", "contextual_meaning": "am"},
                ]
            }
        )
        with pytest.raises(AIValidationError, match="order|match|expected"):
            parse_sentence_meanings(response, expected_expressions=["suis", "ici"])

    def test_expressions_must_be_nonempty(self):
        """Empty expression string must be rejected."""
        response = json.dumps(
            {
                "items": [
                    {"expression": "", "contextual_meaning": "something"},
                ]
            }
        )
        with pytest.raises(AIValidationError, match="empty|non-empty"):
            parse_sentence_meanings(response, expected_expressions=[""])

    def test_meanings_must_be_nonempty(self):
        """Empty meaning string must be rejected."""
        response = json.dumps(
            {
                "items": [
                    {"expression": "test", "contextual_meaning": ""},
                ]
            }
        )
        with pytest.raises(AIValidationError, match="empty|non-empty"):
            parse_sentence_meanings(response, expected_expressions=["test"])

    def test_unicode_normalized_matching(self):
        """Expression matching must use Unicode/case/whitespace normalization."""
        response = json.dumps(
            {
                "items": [
                    {"expression": "  HELLO  ", "contextual_meaning": "greeting"},
                ]
            }
        )
        result = parse_sentence_meanings(response, expected_expressions=["hello"])
        assert len(result) == 1
        assert result[0].contextual_meaning == "greeting"

    def test_empty_expected_with_empty_items(self):
        """Empty expected list with empty items list is valid."""
        result = parse_sentence_meanings(
            json.dumps({"items": []}), expected_expressions=[]
        )
        assert result == []


class TestWordParserValidation:
    """Word parser must require non-empty example for every meaning."""

    def test_missing_example_rejected(self):
        """Every meaning must have a non-empty example."""
        response = json.dumps(
            {
                "meanings": [
                    {"meaning": "A greeting", "example": ""},
                ]
            }
        )
        with pytest.raises(AIValidationError, match="example"):
            parse_word_phrase_meanings(response)

    def test_absent_example_rejected(self):
        """Meanings without example field must be rejected."""
        response = json.dumps(
            {
                "meanings": [
                    {"meaning": "A greeting"},
                ]
            }
        )
        with pytest.raises(AIValidationError, match="example"):
            parse_word_phrase_meanings(response)

    def test_over_max_meanings_rejected(self):
        """More than MAX_WORD_PHRASE_MEANINGS must reject the response."""
        response = json.dumps(
            {
                "meanings": [
                    {"meaning": f"m{i}", "example": f"e{i}"}
                    for i in range(1, MAX_WORD_PHRASE_MEANINGS + 2)
                ]
            }
        )
        with pytest.raises(AIValidationError, match=str(MAX_WORD_PHRASE_MEANINGS)):
            parse_word_phrase_meanings(response)

    def test_valid_with_examples(self):
        """Valid response with non-empty examples must be accepted."""
        response = json.dumps(
            {
                "meanings": [
                    {"meaning": "A greeting", "example": "Hello there!"},
                ]
            }
        )
        result = parse_word_phrase_meanings(response)
        assert len(result) == 1
        assert "A greeting" in result[0].contextual_meaning
        assert "Hello there!" in result[0].contextual_meaning


class TestSearchORGroupsAND:
    """Search must support OR groups containing AND terms."""

    def test_parse_or_groups_with_and_terms(self):
        """'math AND theorem OR topology' -> OR groups with AND terms inside."""
        groups = parse_search_tokens("math AND theorem OR topology")
        assert groups == [["math", "theorem"], ["topology"]]

    def test_plain_multiword_is_literal_substring(self):
        """A plain multi-word query (no AND/OR) is one literal substring."""
        groups = parse_search_tokens("quick brown fox")
        assert groups == [["quick brown fox"]]


class TestSearchSentenceAcrossFields:
    """Sentence search must find: sentence, child expression, child meaning.
    AND terms may match different fields or different child rows."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning

        migrate_unfamiliar_items_meaning(c)
        # Insert a sentence card with multiple child rows
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'The quick brown fox', 'Rendered back', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'quick', 'fast')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'brown', 'a color')"
        )
        c.commit()
        yield c
        c.close()

    def test_percent_and_underscore_are_literal(self, conn):
        assert search_sentence_cards(conn, "%") == []
        assert search_sentence_cards(conn, "_") == []

    def test_search_matches_meaning_field(self, conn):
        """Search must match child row meaning."""
        results = search_sentence_cards(conn, "color")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_and_terms_across_different_child_rows(self, conn):
        """AND terms may match different child rows of same card."""
        results = search_sentence_cards(conn, "fast AND color", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_and_terms_across_fields(self, conn):
        """AND terms may match different fields (sentence + expression)."""
        results = search_sentence_cards(conn, "fox AND fast", "AND")
        assert len(results) == 1
        assert results[0]["id"] == 1

    def test_or_terms_with_and_subgroups(self, conn):
        """OR groups with AND inside: 'fast brown OR nonexistent' -> match."""
        c2 = sqlite3.connect(":memory:")
        init_db(c2)
        ensure_unfamiliar_items_table(c2)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning

        migrate_unfamiliar_items_meaning(c2)
        c2.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'Hello world', 'Greeting back', 1, '2026-01-01')"
        )
        c2.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'hello', 'greeting'), (1, 'world', 'the earth')"
        )
        c2.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'Topology basics', 'Math back', 1, '2026-01-01')"
        )
        c2.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (2, 'topology', 'study of shapes')"
        )
        c2.commit()

        # 'hello AND world OR topology': OR group of
        #   group1: ('hello' AND 'world') -> match card 1
        #   group2: ('topology') -> match card 2
        results = search_sentence_cards(c2, "hello AND world OR topology")
        ids = [r["id"] for r in results]
        assert 1 in ids
        assert 2 in ids
        c2.close()


class TestExplicitANDORMultiword:
    """parse_search_tokens must join adjacent non-operator words."""

    def test_and_with_multiword_operand(self):
        groups = parse_search_tokens("new york AND city")
        assert groups == [["new york", "city"]], (
            f"Expected [['new york', 'city']], got {groups}"
        )

    def test_or_with_multiword_operands(self):
        groups = parse_search_tokens("new york OR los angeles")
        assert groups == [["new york"], ["los angeles"]], (
            f"Expected [['new york'], ['los angeles']], got {groups}"
        )

    def test_preserves_literal_plain_multiword(self):
        groups = parse_search_tokens("new york")
        assert groups == [["new york"]]

    def test_mixed_or_and_multiword(self):
        groups = parse_search_tokens("big apple AND city OR small town")
        assert groups == [["big apple", "city"], ["small town"]], (
            f"Expected [['big apple', 'city'], ['small town']], got {groups}"
        )

    def test_case_insensitive_operators_multiword(self):
        groups = parse_search_tokens("new york and city")
        assert groups == [["new york", "city"]]


class TestUnicodeCaseInsensitiveSearch:
    """SQLite LIKE is ASCII-only; search must support Unicode casefolding."""

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        init_db(c)
        ensure_unfamiliar_items_table(c)
        from kgb_srs.schema import migrate_unfamiliar_items_meaning

        migrate_unfamiliar_items_meaning(c)

        # Sentence card with accented text
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (1, 'Je vais à l''école', 'I go to school', 1, '2026-01-01')"
        )
        c.execute(
            "INSERT INTO unfamiliar_items (card_id, expression, meaning) "
            "VALUES (1, 'école', 'school')"
        )

        # Word/phrase card with accented text
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (2, 'ÉCOLE', 'school - an educational institution', 1, '2026-01-01')"
        )

        c.commit()
        yield c
        c.close()

    def test_sentence_search_casefold_front(self, conn):
        """Searching lowercase 'école' matches 'ÉCOLE' in sentence field."""
        results = search_sentence_cards(conn, "école")
        assert len(results) >= 1
        ids = {r["id"] for r in results}
        assert 1 in ids

    def test_sentence_search_casefold_expression(self, conn):
        """Searching 'ÉCOLE' (uppercase accented) matches 'école' in child expression."""
        results = search_sentence_cards(conn, "ÉCOLE")
        assert len(results) >= 1
        ids = {r["id"] for r in results}
        assert 1 in ids

    def test_word_phrase_search_casefold_front(self, conn):
        """Searching 'école' matches 'ÉCOLE' in word/phrase front."""
        results = search_word_phrase_cards(conn, "école")
        assert len(results) >= 1
        ids = {r["id"] for r in results}
        assert 2 in ids

    def test_literal_percent_underscore_backslash_preserved(self, conn):
        """% and _ must still be treated literally after casefolding."""
        # Insert a card with literal % and _ in back
        c = conn.cursor()
        c.execute(
            "INSERT INTO cards (id, front, back, box, next_review) "
            "VALUES (3, 'test', '50% off and low_price', 1, '2026-01-01')"
        )
        conn.commit()

        # Search for the literal '%' — should match card 3
        results = search_word_phrase_cards(conn, "%")
        ids = {r["id"] for r in results}
        assert 3 in ids, f"Expected card 3 with literal %, got {ids}"

        # Search for literal '_' — should match card 3
        results2 = search_word_phrase_cards(conn, "_")
        ids2 = {r["id"] for r in results2}
        assert 3 in ids2, f"Expected card 3 with literal _, got {ids2}"

    def test_no_match_when_different(self, conn):
        """Non-matching queries should return empty."""
        results = search_sentence_cards(conn, "xyznotfound")
        assert len(results) == 0
