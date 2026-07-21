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

    def test_hyphen_compound_lemma_matches_segment(self):
        """Lemma staple is found inside non-staple (hyphen segment, not substring)."""
        from kgb_srs.validation import (
            highlight_unfamiliar_in_sentence,
            locate_item_surface_span,
            surface_form_in_sentence,
        )

        sentence = (
            "Third, the pressure to supply rations, salt, and non-staple "
            "foods to millions of engineering troops and civilian laborers "
            "along the line was immense."
        )
        result = validate_unfamiliar_items(sentence, ["staple"])
        assert result.valid is True
        assert result.missing == []

        span = locate_item_surface_span(sentence, "staple")
        assert span is not None
        assert sentence[span[0]:span[1]] == "staple"

        assert surface_form_in_sentence(sentence, "staple") is True
        bolded = highlight_unfamiliar_in_sentence(sentence, ["staple"])
        assert "non-**staple**" in bolded

        # Inflected segment inside compound
        assert validate_unfamiliar_items(
            "They bought non-staples yesterday.", ["staple"]
        ).valid
        # Still reject solid-word letter substrings
        assert not surface_form_in_sentence("The cargo ship left.", "go")

    def test_choose_chose_chosen_irregular(self):
        """Irregular choose/chose/chosen share a lemma family."""
        assert validate_unfamiliar_items(
            "She chose a book.", ["choose"]
        ).valid
        assert validate_unfamiliar_items(
            "He has chosen wisely.", ["choose"]
        ).valid
        assert validate_unfamiliar_items(
            "They are choosing now.", ["choose"]
        ).valid
        assert validate_unfamiliar_items(
            "She chooses carefully.", ["choose"]
        ).valid
        assert validate_unfamiliar_items(
            "I choose tea.", ["chose"]
        ).valid
        assert validate_unfamiliar_items(
            "I choose tea.", ["chosen"]
        ).valid
        assert validate_unfamiliar_items(
            "She chose a book.", ["choose a book"]
        ).valid
        # Unrelated word must still fail.
        result = validate_unfamiliar_items(
            "The chocolate is sweet.", ["choose"]
        )
        assert result.valid is False
        assert "choose" in result.missing

    def test_expanded_irregular_map_samples(self):
        """Broader irregular coverage from the comprehensive map."""
        samples = [
            ("They stole the car.", "steal"),
            ("He has stolen it.", "steal"),
            ("I lost my keys.", "lose"),
            ("The lake froze overnight.", "freeze"),
            ("She has frozen the leftovers.", "freeze"),
            ("He bought a book.", "buy"),
            ("She taught math.", "teach"),
            ("They sought help.", "seek"),
            ("He has written a letter.", "write"),
            ("She rewrote the essay.", "rewrite"),
            ("He underwent surgery.", "undergo"),
            ("They misunderstood the question.", "understand"),
            ("She overcame her fear.", "overcome"),
            ("He has forgotten the password.", "forget"),
            ("They fled the scene.", "flee"),
            ("The sun shone brightly.", "shine"),
            ("He knelt down.", "kneel"),
            ("She dreamt of home.", "dream"),
            ("He leapt over the fence.", "leap"),
            ("They forwent dessert.", "forgo"),
        ]
        for sentence, item in samples:
            result = validate_unfamiliar_items(sentence, [item])
            assert result.valid is True, f"{item!r} should match in {sentence!r}"

    def test_become_come_and_overcome_are_independent_irregular_families(self):
        assert validate_unfamiliar_items("He became angry.", ["become"]).valid
        assert validate_unfamiliar_items("He came home.", ["come"]).valid
        assert validate_unfamiliar_items("She overcame her fear.", ["overcome"]).valid
        assert not validate_unfamiliar_items("He became angry.", ["come"]).valid
        assert not validate_unfamiliar_items("He came home.", ["become"]).valid

    def test_surface_form_in_sentence(self):
        from kgb_srs.validation import surface_form_in_sentence
        assert surface_form_in_sentence("He has gone home.", "gone")
        assert surface_form_in_sentence("He has gone home.", "Gone")
        assert not surface_form_in_sentence("He has gone home.", "went")
        assert not surface_form_in_sentence("The cargo ship left.", "go")

    def test_apply_ai_membership_claims_requires_real_surface(self):
        from kgb_srs.validation import apply_ai_membership_claims
        from kgb_srs.ai_parser import MembershipClaim

        sentence = "He has gone home."
        missing = ["go"]
        # Hallucinated surface must be rejected.
        bad = [MembershipClaim(expression="go", found=True, surface="went")]
        r = apply_ai_membership_claims(sentence, missing, bad)
        assert r.valid is False
        assert "go" in r.missing
        # Real surface from the sentence is accepted.
        good = [MembershipClaim(expression="go", found=True, surface="gone")]
        r = apply_ai_membership_claims(sentence, missing, good)
        assert r.valid is True
        assert r.missing == []
        assert r.accepted_surfaces.get("go") == "gone"
        # found=false keeps missing.
        no = [MembershipClaim(expression="go", found=False, surface="")]
        r = apply_ai_membership_claims(sentence, missing, no)
        assert r.valid is False

    def test_ai_residual_surfaces_allow_insert_for_irregular_lie(self, tmp_path):
        """AI residual surface (lay) must survive insert re-validation for lie."""
        from kgb_srs.ai_parser import MembershipClaim
        from kgb_srs.schema import init_db, insert_sentence_card
        from kgb_srs.validation import apply_ai_membership_claims, validate_unfamiliar_items

        sentence = "He lay down to rest."
        # Local rules intentionally do not map recline-lie ↔ lay.
        local = validate_unfamiliar_items(sentence, ["lie"])
        assert local.valid is False
        assert "lie" in local.missing

        residual = apply_ai_membership_claims(
            sentence,
            ["lie"],
            [MembershipClaim(expression="lie", found=True, surface="lay")],
        )
        assert residual.valid is True
        assert residual.accepted_surfaces.get("lie") == "lay"

        db = tmp_path / "lie.db"
        conn = init_db(str(db))

        with pytest.raises(ValueError, match="lie"):
            insert_sentence_card(conn, sentence, [("lie", "recline")], "")

        card_id = insert_sentence_card(
            conn,
            sentence,
            [("lie", "recline")],
            "",
            verified_surfaces=residual.accepted_surfaces,
        )
        assert card_id > 0

        from kgb_srs.schema import get_sentence_card
        from kgb_srs.validation import highlight_unfamiliar_in_sentence

        loaded = get_sentence_card(conn, card_id)
        assert loaded is not None
        front, _back, _box, items = loaded
        assert front == sentence
        assert items[0][0] == "lie"
        assert items[0][3] == "lay"  # persisted AI surface
        # Highlight must bold the stored surface, not fail on local flex alone.
        out = highlight_unfamiliar_in_sentence(sentence, items)
        assert "**lay**" in out
        conn.close()

    def test_highlight_uses_preferred_surface_for_lie_lay(self):
        """Preferred surface bolds lay when lemma is recline-lie."""
        from kgb_srs.validation import highlight_unfamiliar_in_sentence

        s = (
            "Behind every official document, every ban, and every "
            "seemingly casual dispatch lay a much larger chessboard."
        )
        # Without preferred surface: local flex does not map lie ↔ lay.
        bare = highlight_unfamiliar_in_sentence(s, ["dispatch", "lie"])
        assert "**dispatch**" in bare
        assert "**lay**" not in bare
        # With structured preferred surface (as stored after AI residual).
        items = [
            ("dispatch", "message", None, ""),
            ("lie", "exist", None, "lay"),
        ]
        out = highlight_unfamiliar_in_sentence(s, items)
        assert "**dispatch**" in out
        assert "**lay**" in out


# ---------------------------------------------------------------------------
# Surface location + in-sentence highlight
# ---------------------------------------------------------------------------

class TestHighlightUnfamiliarInSentence:
    def test_bold_inflected_surface_not_lemma(self):
        from kgb_srs.validation import highlight_unfamiliar_in_sentence
        out = highlight_unfamiliar_in_sentence(
            "He insists on speaking himself.",
            ["insist on"],
        )
        assert out == "He **insists on** speaking himself."
        assert "Unfamiliar" not in out
        # Whole sentence must not be bolded.
        assert not out.startswith("**He")

    def test_bold_irregular_surface(self):
        from kgb_srs.validation import highlight_unfamiliar_in_sentence
        assert highlight_unfamiliar_in_sentence(
            "He has gone home.", ["go"]
        ) == "He has **gone** home."
        assert highlight_unfamiliar_in_sentence(
            "She chose a book.", ["choose"]
        ) == "She **chose** a book."

    def test_multiple_items(self):
        from kgb_srs.validation import highlight_unfamiliar_in_sentence
        out = highlight_unfamiliar_in_sentence(
            "She went home and bought milk.",
            ["go", "buy"],
        )
        assert out == "She **went** home and **bought** milk."

    def test_no_match_returns_original(self):
        from kgb_srs.validation import highlight_unfamiliar_in_sentence
        s = "The cargo ship left."
        assert highlight_unfamiliar_in_sentence(s, ["go"]) == s

    def test_locate_item_surface_span(self):
        from kgb_srs.validation import locate_item_surface_span
        s = "He insists on speaking himself."
        span = locate_item_surface_span(s, "insist on")
        assert span is not None
        assert s[span[0]:span[1]] == "insists on"

    def test_japanese_literal_highlight(self):
        from kgb_srs.validation import highlight_unfamiliar_in_sentence
        s = "私は日本語を勉強しています"
        out = highlight_unfamiliar_in_sentence(s, ["日本語"])
        assert out == "私は**日本語**を勉強しています"

    def test_bold_capitalized_inflected_surface(self):
        """Lemma ``exact`` must bold capitalized ``Exacted`` (case-insensitive flex)."""
        from kgb_srs.validation import (
            highlight_unfamiliar_in_sentence,
            locate_item_surface_span,
        )

        s = "Revenge for a Grievance of a Hundred Generations May Still Be Exacted!"
        assert locate_item_surface_span(s, "exact") is not None
        out = highlight_unfamiliar_in_sentence(s, ["exact", "grievance"])
        assert out == (
            "Revenge for a **Grievance** of a Hundred Generations "
            "May Still Be **Exacted**!"
        )

    def test_trailing_punctuation_not_inside_bold(self):
        from kgb_srs.validation import highlight_unfamiliar_in_sentence

        assert highlight_unfamiliar_in_sentence(
            "Exacted!", ["exact"]
        ) == "**Exacted**!"


# ---------------------------------------------------------------------------
# Sentence-order sort + numbered meaning lines
# ---------------------------------------------------------------------------

class TestSentenceMeaningDisplayHelpers:
    def test_sort_items_by_sentence_order(self):
        from kgb_srs.validation import sort_items_by_sentence_order

        s = "Revenge for a Grievance of a Hundred Generations May Still Be Exacted!"
        # Insert order is reverse of sentence order.
        items = [("exact", "demand"), ("grievance", "wrong")]
        ordered = sort_items_by_sentence_order(s, items)
        assert [e for e, _m in ordered] == ["grievance", "exact"]

    def test_format_sentence_meaning_lines_single_unnumbered(self):
        from kgb_srs.validation import format_sentence_meaning_lines

        assert format_sentence_meaning_lines([("exact", "demand")]) == [
            "**exact**: demand"
        ]

    def test_format_sentence_meaning_lines_multiple_numbered(self):
        from kgb_srs.validation import format_sentence_meaning_lines

        lines = format_sentence_meaning_lines(
            [("grievance", "wrong"), ("exact", "demand")]
        )
        assert lines == [
            "1. **grievance**: wrong",
            "2. **exact**: demand",
        ]


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
