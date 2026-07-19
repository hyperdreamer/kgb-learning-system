"""Sentence-based literal validation — Unicode-safe, case-insensitive,
whitespace-normalized matching of unfamiliar items against a sentence.

All matching is literal — no regex interpretation of item text.
No ASCII word boundaries are used; matching is plain substring after
normalization.
"""

import unicodedata
import re
from dataclasses import dataclass, field


def normalize_sentence(text: str) -> str:
    """Normalize text for searching: NFC, lowercase, collapse whitespace, strip.
    
    This is used on both the sentence and each unfamiliar item before matching.
    """
    if not text:
        return ""
    # Unicode normalization to NFC
    text = unicodedata.normalize("NFC", text)
    # Casefold (more aggressive than lower for Unicode)
    text = text.casefold()
    # Collapse all whitespace to single spaces and strip
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Escape special regex metacharacters in a string so it is treated literally.
_RE_ESCAPE_RE = re.compile(r"([.^$*+?{}[\]\\|()])")


def _escape_regex(text: str) -> str:
    """Escape regex metacharacters in *text*."""
    return _RE_ESCAPE_RE.sub(r"\\\1", text)


def _literal_find(pattern: str, haystack: str) -> bool:
    """Check whether *pattern* (already normalized) is found literally in
    *haystack* (already normalized).

    Uses regex with the pattern escaped, searching for the literal substring
    anywhere in the haystack. No word boundaries — just plain literal match.
    """
    escaped = _escape_regex(pattern)
    return bool(re.search(escaped, haystack))


@dataclass
class ValidationResult:
    valid: bool
    missing: list[str] = field(default_factory=list)


def validate_unfamiliar_items(
    sentence: str,
    unfamiliar_items: list[str],
) -> ValidationResult:
    """Check that every unfamiliar item occurs literally in *sentence*.
    
    Matching is:
    - Case-insensitive (via casefold)
    - Unicode-normalized (NFC)
    - Whitespace-collapsed
    - Literal (regex metacharacters in items are escaped)
    - No word-boundary restriction (pure substring)
    
    Returns a ValidationResult with .valid and .missing fields.
    """
    if not unfamiliar_items:
        return ValidationResult(valid=True, missing=[])

    norm_sentence = normalize_sentence(sentence)
    missing = []

    for item in unfamiliar_items:
        norm_item = normalize_sentence(item)
        if not norm_item:
            continue
        if not _literal_find(norm_item, norm_sentence):
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
