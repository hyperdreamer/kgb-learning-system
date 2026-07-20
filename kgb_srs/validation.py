"""Sentence-based unfamiliar-item validation.

Matching is Unicode-safe, case-insensitive, and whitespace-normalized.
Primary path is literal substring match. When that fails, a flexible
token-sequence path accepts common English inflections (tense / number)
so a lemma like ``insist on`` matches surface forms such as ``insists on``.

All matching treats item text as literal content (regex metacharacters are
escaped on the literal path). No network / AI is used.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def normalize_sentence(text: str) -> str:
    """Normalize text for searching: NFC, casefold, collapse whitespace, strip.

    Used on both the sentence and each unfamiliar item before matching.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Escape special regex metacharacters so item text is treated literally.
_RE_ESCAPE_RE = re.compile(r"([.^$*+?{}[\]\\|()])")

# Leading/trailing punctuation stripped from tokens for flex matching.
_PUNCT_STRIP = ".,!?;:\"'“”‘’()[]{}…—–-«»"

# Common irregular English verb forms → shared lemma.
# Keep this small and high-value; regular -s/-ed/-ing stay on the stemmer.
_IRREGULAR_VERB_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"be", "am", "is", "are", "was", "were", "been", "being"}),
    frozenset({"have", "has", "had", "having"}),
    frozenset({"do", "does", "did", "done", "doing"}),
    frozenset({"go", "goes", "going", "went", "gone"}),
    frozenset({"come", "comes", "coming", "came"}),
    frozenset({"see", "sees", "seeing", "saw", "seen"}),
    frozenset({"get", "gets", "getting", "got", "gotten"}),
    frozenset({"make", "makes", "making", "made"}),
    frozenset({"take", "takes", "taking", "took", "taken"}),
    frozenset({"give", "gives", "giving", "gave", "given"}),
    frozenset({"find", "finds", "finding", "found"}),
    frozenset({"think", "thinks", "thinking", "thought"}),
    frozenset({"say", "says", "saying", "said"}),
    frozenset({"tell", "tells", "telling", "told"}),
    frozenset({"know", "knows", "knowing", "knew", "known"}),
    frozenset({"feel", "feels", "feeling", "felt"}),
    frozenset({"leave", "leaves", "leaving", "left"}),
    frozenset({"keep", "keeps", "keeping", "kept"}),
    frozenset({"begin", "begins", "beginning", "began", "begun"}),
    frozenset({"run", "runs", "running", "ran"}),
    frozenset({"write", "writes", "writing", "wrote", "written"}),
    frozenset({"speak", "speaks", "speaking", "spoke", "spoken"}),
    frozenset({"break", "breaks", "breaking", "broke", "broken"}),
    frozenset({"choose", "chooses", "choosing", "chose", "chosen"}),
    frozenset({"drive", "drives", "driving", "drove", "driven"}),
    frozenset({"eat", "eats", "eating", "ate", "eaten"}),
    frozenset({"fall", "falls", "falling", "fell", "fallen"}),
    frozenset({"fly", "flies", "flying", "flew", "flown"}),
    frozenset({"grow", "grows", "growing", "grew", "grown"}),
    frozenset({"hide", "hides", "hiding", "hid", "hidden"}),
    frozenset({"hold", "holds", "holding", "held"}),
    frozenset({"read", "reads", "reading"}),  # past "read" same spelling
    frozenset({"rise", "rises", "rising", "rose", "risen"}),
    frozenset({"send", "sends", "sending", "sent"}),
    frozenset({"sing", "sings", "singing", "sang", "sung"}),
    frozenset({"sit", "sits", "sitting", "sat"}),
    frozenset({"sleep", "sleeps", "sleeping", "slept"}),
    frozenset({"stand", "stands", "standing", "stood"}),
    frozenset({"swim", "swims", "swimming", "swam", "swum"}),
    frozenset({"teach", "teaches", "teaching", "taught"}),
    frozenset({"throw", "throws", "throwing", "threw", "thrown"}),
    frozenset({"understand", "understands", "understanding", "understood"}),
    frozenset({"wear", "wears", "wearing", "wore", "worn"}),
    frozenset({"win", "wins", "winning", "won"}),
    frozenset({"buy", "buys", "buying", "bought"}),
    frozenset({"bring", "brings", "bringing", "brought"}),
    frozenset({"build", "builds", "building", "built"}),
    frozenset({"catch", "catches", "catching", "caught"}),
    frozenset({"draw", "draws", "drawing", "drew", "drawn"}),
    frozenset({"drink", "drinks", "drinking", "drank", "drunk"}),
    frozenset({"forget", "forgets", "forgetting", "forgot", "forgotten"}),
    frozenset({"forgive", "forgives", "forgiving", "forgave", "forgiven"}),
    frozenset({"hear", "hears", "hearing", "heard"}),
    frozenset({"pay", "pays", "paying", "paid"}),
    frozenset({"sell", "sells", "selling", "sold"}),
    frozenset({"shut", "shuts", "shutting"}),
    frozenset({"spend", "spends", "spending", "spent"}),
    frozenset({"wake", "wakes", "waking", "woke", "woken", "awake", "awoke"}),
)

_IRREGULAR_LOOKUP: dict[str, frozenset[str]] = {
    form: group for group in _IRREGULAR_VERB_GROUPS for form in group
}


def _escape_regex(text: str) -> str:
    """Escape regex metacharacters in *text*."""
    return _RE_ESCAPE_RE.sub(r"\\\1", text)


def _literal_find(pattern: str, haystack: str) -> bool:
    """Check whether normalized *pattern* occurs literally in *haystack*.

    For single-token alphabetic patterns, require whole-token equality so
    short lemmas like ``go`` do not accidentally match inside ``gone`` /
    ``going`` via pure substring. Multi-word / non-alpha patterns keep
    plain substring matching.
    """
    if not pattern:
        return False
    # Single simple alphabetic token → whole-token only (not substring).
    if re.fullmatch(r"[a-z]+", pattern):
        return any(_strip_token_punct(tok) == pattern for tok in haystack.split(" "))
    # Phrases and patterns with punctuation/metacharacters: literal substring.
    escaped = _escape_regex(pattern)
    return bool(re.search(escaped, haystack))


def _strip_token_punct(token: str) -> str:
    return token.strip(_PUNCT_STRIP)


def _stem_candidates(token: str) -> set[str]:
    """Return a small set of stem-like candidates for *token*.

    Enough for common English tense/number variants; not a full stemmer.
    Always includes the token itself. Irregular verb groups (go/went/gone)
    are expanded via a compact lookup. Suffix stripping usually requires a
    base of length ≥ 3; short irregular bases like go/do (from goes/does)
    are allowed at length ≥ 2 for -s/-es only.
    """
    t = _strip_token_punct(token)
    if not t:
        return set()

    cands: set[str] = {t}
    n = len(t)

    # Irregular verb family (go ↔ went ↔ gone, etc.)
    irregular = _IRREGULAR_LOOKUP.get(t)
    if irregular is not None:
        cands.update(irregular)

    def add_base(base: str, min_len: int = 3) -> None:
        if len(base) >= min_len:
            cands.add(base)
            # If the derived base is itself irregular, expand that family too.
            group = _IRREGULAR_LOOKUP.get(base)
            if group is not None:
                cands.update(group)

    # studies / tries → study / try
    if n >= 5 and t.endswith("ies"):
        add_base(t[:-3] + "y")
        add_base(t[:-3])

    # tried / carried → try / carry
    if n >= 5 and t.endswith("ied"):
        add_base(t[:-3] + "y")
        add_base(t[:-3])

    # insisting / running / lying
    if n >= 5 and t.endswith("ing"):
        base = t[:-3]
        add_base(base)
        if len(base) >= 4 and base[-1] == base[-2] and base[-1].isalpha():
            add_base(base[:-1])  # running → run
        if base.endswith("i") and len(base) >= 3:
            add_base(base[:-1] + "y")  # lying → ly / y form

    # insisted / stopped / liked
    if n >= 4 and t.endswith("ed"):
        base = t[:-2]
        add_base(base)
        if len(base) >= 4 and base[-1] == base[-2] and base[-1].isalpha():
            add_base(base[:-1])  # stopped → stop
        if base.endswith("i") and len(base) >= 3:
            add_base(base[:-1] + "y")
        # liked → like (restore silent e)
        add_base(base + "e")

    # goes / does / watches / boxes
    if n >= 4 and t.endswith("es"):
        base = t[:-2]
        # Allow short bases (goes→go, does→do); longer bases keep min 3.
        add_base(base, min_len=2)
        if base.endswith("i"):
            add_base(base[:-1] + "y")

    # insists / cats — single trailing s (not ss)
    if n >= 4 and t.endswith("s") and not t.endswith("ss"):
        add_base(t[:-1], min_len=2)

    # taller / biggest / quickly (light extras)
    if n >= 5 and t.endswith("er"):
        add_base(t[:-2])
    if n >= 6 and t.endswith("est"):
        add_base(t[:-3])
    if n >= 5 and t.endswith("ly"):
        add_base(t[:-2])

    return cands


def _tokens_flex_equal(a: str, b: str) -> bool:
    """True if two tokens are equal or share an inflection stem candidate."""
    sa = _strip_token_punct(a)
    sb = _strip_token_punct(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    # Direct irregular-family membership (fast path).
    group_a = _IRREGULAR_LOOKUP.get(sa)
    if group_a is not None and sb in group_a:
        return True
    group_b = _IRREGULAR_LOOKUP.get(sb)
    if group_b is not None and sa in group_b:
        return True
    return bool(_stem_candidates(sa) & _stem_candidates(sb))


def _tokenize(text: str) -> list[str]:
    """Whitespace-tokenize a normalized string; drop empty after punct strip."""
    if not text:
        return []
    raw = text.split(" ")
    return [t for t in raw if _strip_token_punct(t)]


def _flexible_phrase_match(norm_item: str, norm_sentence: str) -> bool:
    """Match item as a consecutive token sequence under flex token equality.

    Requires both sides to yield at least one whitespace-separated token so
    continuous CJK strings stay on the literal-only path.
    """
    # Need real whitespace separation for this path to be meaningful.
    if " " not in norm_item and " " not in norm_sentence:
        # Single-token item vs multi-token sentence still allowed.
        if " " not in norm_sentence:
            return False

    item_tokens = _tokenize(norm_item)
    sent_tokens = _tokenize(norm_sentence)
    if not item_tokens or not sent_tokens:
        return False
    if len(item_tokens) > len(sent_tokens):
        return False

    k = len(item_tokens)
    for i in range(0, len(sent_tokens) - k + 1):
        window = sent_tokens[i : i + k]
        if all(_tokens_flex_equal(it, st) for it, st in zip(item_tokens, window)):
            return True
    return False


@dataclass
class ValidationResult:
    valid: bool
    missing: list[str] = field(default_factory=list)


def validate_unfamiliar_items(
    sentence: str,
    unfamiliar_items: list[str],
) -> ValidationResult:
    """Check that every unfamiliar item occurs in *sentence*.

    Matching is:
    - Case-insensitive (casefold)
    - Unicode-normalized (NFC)
    - Whitespace-collapsed
    - Literal substring first (regex metacharacters in items are escaped)
    - Then inflection-tolerant consecutive token match for spaced languages
      (e.g. ``insist on`` ↔ ``insists on``)
    - Continuous scripts without spaces stay on the literal path

    Returns a ValidationResult with .valid and .missing fields.
    """
    if not unfamiliar_items:
        return ValidationResult(valid=True, missing=[])

    norm_sentence = normalize_sentence(sentence)
    missing: list[str] = []

    for item in unfamiliar_items:
        norm_item = normalize_sentence(item)
        if not norm_item:
            continue
        if _literal_find(norm_item, norm_sentence):
            continue
        if _flexible_phrase_match(norm_item, norm_sentence):
            continue
        missing.append(item)

    return ValidationResult(
        valid=len(missing) == 0,
        missing=missing,
    )


def deduplicate_unfamiliar_items(items: list[str]) -> list[str]:
    """Remove duplicate unfamiliar items, considering normalization.

    Two items that normalize to the same string are considered duplicates.
    The first occurrence is kept.
    """
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        key = normalize_sentence(item)
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(item)

    return result
