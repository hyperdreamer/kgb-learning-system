"""Tests for kgb_srs.ai_parser — AI response parsing and validation."""

import json
import pytest

from kgb_srs.ai_parser import (
    parse_sentence_meanings,
    parse_word_phrase_meanings,
    AIParseError,
    AIValidationError,
    MAX_WORD_PHRASE_MEANINGS,
)


# ---------------------------------------------------------------------------
# parse_sentence_meanings
# ---------------------------------------------------------------------------


class TestParseSentenceMeanings:
    def test_valid_response(self):
        response = {
            "items": [
                {
                    "expression": "suis",
                    "contextual_meaning": "am (1st person singular of être)",
                },
                {"expression": "ici", "contextual_meaning": "here"},
            ]
        }
        result = parse_sentence_meanings(
            json.dumps(response),
            expected_expressions=["suis", "ici"],
        )
        assert len(result) == 2
        assert result[0].expression == "suis"
        assert result[0].contextual_meaning == "am (1st person singular of être)"
        assert result[1].expression == "ici"

    def test_missing_items_key(self):
        with pytest.raises(AIValidationError, match="'items'"):
            parse_sentence_meanings('{"wrong": []}', ["a", "b"])

    def test_not_json(self):
        with pytest.raises(AIParseError, match="JSON"):
            parse_sentence_meanings("not json at all", ["a"])

    def test_wrong_item_count(self):
        response = {
            "items": [
                {"expression": "a", "contextual_meaning": "meaning of a"},
            ]
        }
        with pytest.raises(AIValidationError, match="returned"):
            parse_sentence_meanings(
                json.dumps(response), expected_expressions=["a", "b"]
            )

    def test_extra_items(self):
        response = {
            "items": [
                {"expression": "a", "contextual_meaning": "m1"},
                {"expression": "b", "contextual_meaning": "m2"},
                {"expression": "c", "contextual_meaning": "m3"},
            ]
        }
        with pytest.raises(AIValidationError, match="returned"):
            parse_sentence_meanings(
                json.dumps(response), expected_expressions=["a", "b"]
            )

    def test_missing_expression_field(self):
        response = {
            "items": [
                {"contextual_meaning": "meaning without expression"},
            ]
        }
        with pytest.raises(AIValidationError, match="'expression'"):
            parse_sentence_meanings(json.dumps(response), expected_expressions=["test"])

    def test_missing_contextual_meaning_field(self):
        response = {
            "items": [
                {"expression": "test"},
            ]
        }
        with pytest.raises(AIValidationError, match="'contextual_meaning'"):
            parse_sentence_meanings(json.dumps(response), expected_expressions=["test"])

    def test_empty_items(self):
        result = parse_sentence_meanings(
            json.dumps({"items": []}), expected_expressions=[]
        )
        assert result == []

    def test_whitespace_tolerance(self):
        response = "  \n  " + json.dumps(
            {"items": [{"expression": "x", "contextual_meaning": "y"}]}
        )
        result = parse_sentence_meanings(response, expected_expressions=["x"])
        assert len(result) == 1

    def test_markdown_code_fence_stripping(self):
        """AI sometimes wraps JSON in ```json ... ```."""
        response = (
            "```json\n"
            + json.dumps({"items": [{"expression": "x", "contextual_meaning": "y"}]})
            + "\n```"
        )
        result = parse_sentence_meanings(response, expected_expressions=["x"])
        assert len(result) == 1

    @pytest.mark.parametrize("value", [[], {}, 1])
    @pytest.mark.parametrize("field", ["expression", "contextual_meaning"])
    def test_required_text_fields_must_be_strings(self, field, value):
        item = {"expression": "test", "contextual_meaning": "meaning"}
        item[field] = value
        with pytest.raises(AIValidationError, match=field):
            parse_sentence_meanings(json.dumps({"items": [item]}), ["test"])


# ---------------------------------------------------------------------------
# parse_word_phrase_meanings
# ---------------------------------------------------------------------------


class TestParseWordPhraseMeanings:
    def test_valid_two_meanings(self):
        response = {
            "meanings": [
                {
                    "meaning": "A domestic feline",
                    "example": "The cat sat on the mat.",
                },
                {
                    "meaning": "A malicious woman (slang)",
                    "example": "Don't be such a cat.",
                },
            ]
        }
        result = parse_word_phrase_meanings(json.dumps(response))
        assert len(result) == 2
        assert result[0].expression == ""
        assert "1. A domestic feline" in result[0].contextual_meaning
        assert "*The cat sat on the mat.*" in result[0].contextual_meaning

    def test_valid_one_meaning(self):
        """AI may return only one meaning — that's acceptable."""
        response = {
            "meanings": [
                {"meaning": "A greeting", "example": "Hello there!"},
            ]
        }
        result = parse_word_phrase_meanings(json.dumps(response))
        assert len(result) == 1

    def test_zero_meanings_invalid(self):
        response = {"meanings": []}
        with pytest.raises(AIValidationError, match="at least one"):
            parse_word_phrase_meanings(json.dumps(response))

    def test_missing_meanings_key(self):
        with pytest.raises(AIValidationError, match="'meanings'"):
            parse_word_phrase_meanings('{"wrong": []}')

    def test_missing_meaning_field(self):
        response = {"meanings": [{"example": "no meaning field"}]}
        with pytest.raises(AIValidationError, match="'meaning'"):
            parse_word_phrase_meanings(json.dumps(response))

    def test_example_required(self):
        """Example is now required for every meaning."""
        response = {"meanings": [{"meaning": "Just a meaning"}]}
        with pytest.raises(AIValidationError, match="'example'"):
            parse_word_phrase_meanings(json.dumps(response))

    def test_at_max_meanings_accepted(self):
        """Exactly MAX_WORD_PHRASE_MEANINGS meanings is accepted."""
        response = {
            "meanings": [
                {"meaning": f"m{i}", "example": f"e{i}"}
                for i in range(1, MAX_WORD_PHRASE_MEANINGS + 1)
            ]
        }
        result = parse_word_phrase_meanings(json.dumps(response))
        assert len(result) == MAX_WORD_PHRASE_MEANINGS

    def test_more_than_max_rejected(self):
        """More than MAX_WORD_PHRASE_MEANINGS meanings rejects the response."""
        response = {
            "meanings": [
                {"meaning": f"m{i}", "example": f"e{i}"}
                for i in range(1, MAX_WORD_PHRASE_MEANINGS + 2)
            ]
        }
        with pytest.raises(AIValidationError, match=str(MAX_WORD_PHRASE_MEANINGS)):
            parse_word_phrase_meanings(json.dumps(response))

    def test_code_fence_stripping(self):
        response = (
            "```json\n"
            + json.dumps(
                {"meanings": [{"meaning": "Test", "example": "This is a test."}]}
            )
            + "\n```"
        )
        result = parse_word_phrase_meanings(response)
        assert len(result) == 1

    @pytest.mark.parametrize("value", [[], {}, 1])
    @pytest.mark.parametrize("field", ["meaning", "example"])
    def test_required_text_fields_must_be_strings(self, field, value):
        item = {"meaning": "meaning", "example": "example"}
        item[field] = value
        with pytest.raises(AIValidationError, match=field):
            parse_word_phrase_meanings(json.dumps({"meanings": [item]}))


# ---------------------------------------------------------------------------
# parse_membership_claims
# ---------------------------------------------------------------------------


class TestParseMembershipClaims:
    def test_valid_response(self):
        from kgb_srs.ai_parser import parse_membership_claims

        response = {
            "items": [
                {"expression": "go", "found": True, "surface": "gone"},
                {"expression": "absent", "found": False, "surface": ""},
            ]
        }
        result = parse_membership_claims(
            json.dumps(response),
            expected_expressions=["go", "absent"],
        )
        assert len(result) == 2
        assert result[0].found is True
        assert result[0].surface == "gone"
        assert result[1].found is False
        assert result[1].surface == ""

    def test_found_true_requires_surface(self):
        from kgb_srs.ai_parser import parse_membership_claims

        response = {
            "items": [
                {"expression": "go", "found": True, "surface": ""},
            ]
        }
        with pytest.raises(AIValidationError, match="surface"):
            parse_membership_claims(json.dumps(response), ["go"])

    def test_wrong_order_rejected(self):
        from kgb_srs.ai_parser import parse_membership_claims

        response = {
            "items": [
                {"expression": "b", "found": False, "surface": ""},
                {"expression": "a", "found": False, "surface": ""},
            ]
        }
        with pytest.raises(AIValidationError, match="expected expression"):
            parse_membership_claims(json.dumps(response), ["a", "b"])

    def test_found_must_be_bool(self):
        from kgb_srs.ai_parser import parse_membership_claims

        response = {
            "items": [
                {"expression": "go", "found": "yes", "surface": "gone"},
            ]
        }
        with pytest.raises(AIValidationError, match="boolean"):
            parse_membership_claims(json.dumps(response), ["go"])
