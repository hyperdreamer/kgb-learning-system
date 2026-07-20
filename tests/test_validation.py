"""Tests for kgb_srs.validation — sentence-based item matching."""

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

    def test_inflected_verb_phrase_insist_on(self):
        """Lemma form must match 3rd-person surface form in the sentence."""
        sentence = "He insists on speaking himself."
        items = ["insist on"]
        result = validate_unfamiliar_items(sentence, items)
        assert result.valid is True
        assert result.missing == []

    def test_past_and_ing_forms(self):
        assert validate_unfamiliar_items(
            "She insisted on leaving.", ["insist on"]
        ).valid
        assert validate_unfamiliar_items(
            "They are insisting on a refund.", ["insist on"]
        ).valid

    def test_third_person_go_goes(self):
        assert validate_unfamiliar_items(
            "He goes to school.", ["go to"]
        ).valid

    def test_studies_study(self):
        assert validate_unfamiliar_items(
            "She studies hard every day.", ["study"]
        ).valid

    def test_watched_watch(self):
        assert validate_unfamiliar_items(
            "I watched a film last night.", ["watch"]
        ).valid

    def test_still_rejects_absent(self):
        result = validate_unfamiliar_items(
            "He insists on speaking.", ["absent phrase"]
        )
        assert result.valid is False
        assert "absent phrase" in result.missing

    def test_cjk_still_literal(self):
        assert validate_unfamiliar_items(
            "我喜欢吃中国菜", ["中国菜", "我喜欢"]
        ).valid

    def test_multiword_requires_consecutive(self):
        """Words present but not consecutive as the phrase must fail."""
        result = validate_unfamiliar_items(
            "I insist that we focus on quality.", ["insist on"]
        )
        assert result.valid is False
        assert "insist on" in result.missing

    def test_go_gone_went_irregular(self):
        """Irregular go/went/gone (and going/goes) share a lemma family."""
        assert validate_unfamiliar_items(
            "He has gone home.", ["go"]
        ).valid
        assert validate_unfamiliar_items(
            "They went home early.", ["go"]
        ).valid
        assert validate_unfamiliar_items(
            "She is going home.", ["go"]
        ).valid
        assert validate_unfamiliar_items(
            "He has gone home.", ["go home"]
        ).valid
        assert validate_unfamiliar_items(
            "They went home early.", ["go home"]
        ).valid
        # reverse: surface lemma in item, past in sentence already covered;
        # also item may be the irregular form against a base in sentence
        assert validate_unfamiliar_items(
            "I go there every day.", ["gone"]
        ).valid
        assert validate_unfamiliar_items(
            "I go there every day.", ["went"]
        ).valid

    def test_go_does_not_substring_match_unrelated(self):
        """Short lemma must not match as a substring inside another word."""
        # "go" must not match merely because it is letters inside "cargo"
        # if we only had substring; cargo is unrelated. Token equality fails;
        # irregular family does not include cargo.
        result = validate_unfamiliar_items("The cargo ship left.", ["go"])
        assert result.valid is False
        assert "go" in result.missing


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
