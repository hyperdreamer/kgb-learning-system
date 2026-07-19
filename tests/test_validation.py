"""Tests for kgb_srs.validation — sentence-based literal validation."""

import pytest

from kgb_srs.validation import (
    normalize_sentence,
    validate_unfamiliar_items,
    deduplicate_unfamiliar_items,
)


# ---------------------------------------------------------------------------
# normalize_sentence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_text,expected", [
    ("Hello  world", "hello world"),
    ("Héllo  Wörld", "héllo wörld"),
    ("  leading  spaces  ", "leading spaces"),
    ("tab\there", "tab here"),
    ("new\nline\r", "new line"),
    ("\u00e9", "\u00e9"),        # NFC é, stays NFC
    ("e\u0301", "\u00e9"),       # NFD é → NFC
    ("\u212b", "\u00e5"),        # ANGSTROM SIGN → å
])
def test_normalize_sentence(input_text, expected):
    result = normalize_sentence(input_text)
    assert result == expected


def test_normalize_sentence_preserves_empty():
    assert normalize_sentence("") == ""
    assert normalize_sentence("   ") == ""


# ---------------------------------------------------------------------------
# validate_unfamiliar_items
# ---------------------------------------------------------------------------

class TestValidateUnfamiliarItems:
    def test_all_items_found_literally(self):
        sentence = "The quick brown fox"
        items = ["quick", "brown"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True
        assert result.missing == []

    def test_case_insensitive_match(self):
        sentence = "Hello World"
        items = ["hello", "WORLD"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True

    def test_unicode_normalized_match(self):
        # NFC vs NFD forms of the same character
        sentence = "café résumé"  # NFC
        items = ["cafe\u0301", "résumé"]  # NFD é
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True

    def test_missing_item_reported(self):
        sentence = "The quick brown fox"
        items = ["quick", "absent"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is False
        assert "absent" in result.missing

    def test_regex_metacharacters_treated_literally(self):
        sentence = "What is 2+2? Or a*b? [test] maybe?"
        items = ["2+2?", "a*b", "[test]", "."]
        result = validate_unfamiliar_items(sentence, items)
        # "." is not in the sentence (we treat it literally as a dot character)
        assert result.valid is False
        assert "." in result.missing
        # [test] as literal brackets should be found
        assert "[test]" not in result.missing

    def test_regex_star_dot_plus(self):
        sentence = "Patterns: a.*b and c.+d"
        items = ["a.*b", "c.+d"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True

    def test_whitespace_collapsed_matching(self):
        sentence = "hello   world\t\ttest"
        items = ["hello world", "world test"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True

    def test_multiword_phrase_matching(self):
        sentence = "The quick brown fox jumps over the lazy dog"
        items = ["quick brown fox", "lazy dog"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True

    def test_non_space_language(self):
        sentence = "我喜欢吃中国菜"
        items = ["我喜欢", "中国菜"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True

    def test_japanese_mixed(self):
        sentence = "私は日本語を勉強しています"
        items = ["日本語", "勉強"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True


# ---------------------------------------------------------------------------
# deduplicate_unfamiliar_items
# ---------------------------------------------------------------------------

class TestDeduplicateUnfamiliarItems:
    def test_removes_exact_duplicates(self):
        items = ["hello", "world", "hello"]
        result = deduplicate_unfamiliar_items(items)
        assert result == ["hello", "world"]

    def test_normalized_duplicates_removed(self):
        # Different Unicode representations of the same text
        items = ["café", "cafe\u0301"]  # NFC vs NFD
        result = deduplicate_unfamiliar_items(items)
        assert len(result) == 1

    def test_case_variant_duplicates(self):
        items = ["Hello", "hello", "HELLO"]
        result = deduplicate_unfamiliar_items(items)
        assert len(result) == 1

    def test_whitespace_variant_duplicates(self):
        items = ["hello  world", "hello world"]
        result = deduplicate_unfamiliar_items(items)
        assert len(result) == 1

    def test_preserves_order_of_first_occurrence(self):
        items = ["zebra", "alpha", "ZEBRA", "beta"]
        result = deduplicate_unfamiliar_items(items)
        assert result[0] == "zebra"
        assert result[1] == "alpha"
        assert result[2] == "beta"

    def test_empty_list(self):
        assert deduplicate_unfamiliar_items([]) == []
