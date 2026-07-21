"""AI response parsing and validation.

Parses structured JSON from AI providers and validates the response shape.
No network calls — pure parsing and validation logic.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .validation import normalize_sentence


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MeaningResult:
    """A single meaning entry returned by the AI."""
    expression: str = ""
    contextual_meaning: str = ""
    meaning: str = ""
    example: str = ""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AIParseError(Exception):
    """The AI response could not be parsed as JSON."""


class AIValidationError(Exception):
    """The AI response JSON had the wrong shape or content."""


# Max meanings for word/phrase cards (UI tabs + AI generation).
MAX_WORD_PHRASE_MEANINGS = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _extract_json(text: str) -> str:
    """Extract a JSON payload from potentially code-fenced text."""
    text = text.strip()
    m = _CODE_FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text


def _parse_json(text: str) -> dict:
    """Parse text as JSON, raising AIParseError on failure."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIParseError(f"Failed to parse AI response as JSON: {e}")


# ---------------------------------------------------------------------------
# Sentence meanings
# ---------------------------------------------------------------------------

def parse_sentence_meanings(
    response_text: str,
    expected_expressions: list[str],
) -> list[MeaningResult]:
    """Parse AI response for sentence-based contextual meanings.

    Expected shape:
        {"items": [{"expression": "...", "contextual_meaning": "..."}, ...]}

    Validates:
      - The number of items matches len(expected_expressions).
      - Returned expressions match expected expressions in order under
        Unicode/case/whitespace normalization.
      - Expressions and contextual_meaning fields are non-empty.

    Raises:
        AIParseError — response is not valid JSON
        AIValidationError — JSON shape, item count, identity/order, or
                            empty-field violation
    """
    json_text = _extract_json(response_text)
    data = _parse_json(json_text)

    if not isinstance(data, dict):
        raise AIValidationError("AI response must be a JSON object")

    items = data.get("items")
    if items is None:
        raise AIValidationError("AI response missing 'items' key")
    if not isinstance(items, list):
        raise AIValidationError("'items' must be a list")

    expected_count = len(expected_expressions)
    if len(items) != expected_count:
        raise AIValidationError(
            f"Expected {expected_count} meaning(s) but AI returned {len(items)}"
        )

    results: list[MeaningResult] = []
    norm_expected = [normalize_sentence(e) for e in expected_expressions]

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise AIValidationError(f"Item {i} must be a JSON object")

        expr = item.get("expression")
        if expr is None:
            raise AIValidationError(
                f"Item {i} missing required 'expression' field"
            )
        if not str(expr).strip():
            raise AIValidationError(
                f"Item {i} has empty 'expression' field"
            )

        meaning = item.get("contextual_meaning")
        if meaning is None:
            raise AIValidationError(
                f"Item {i} missing required 'contextual_meaning' field"
            )
        if not str(meaning).strip():
            raise AIValidationError(
                f"Item {i} has empty 'contextual_meaning' field"
            )

        # Validate identity/order: returned expression must match
        # the expected expression at the same index under normalization.
        norm_returned = normalize_sentence(str(expr))
        if norm_returned != norm_expected[i]:
            raise AIValidationError(
                f"Item {i}: expected expression matching "
                f"'{expected_expressions[i]}' but got '{expr}'. "
                f"AI returned expressions in wrong order or with wrong content."
            )

        results.append(MeaningResult(
            expression=str(expr),
            contextual_meaning=str(meaning),
        ))

    return results


# ---------------------------------------------------------------------------
# Sense assignment (reuse existing sense or create new meaning)
# ---------------------------------------------------------------------------


@dataclass
class SenseAssignment:
    """AI judgment: reuse a prior sense id, or create a new meaning text."""

    expression: str
    action: str  # "reuse" | "create"
    sense_id: int | None = None
    meaning: str = ""


def parse_sense_assignment(
    response_text: str,
    expression: str,
    prior_sense_ids: list[int],
) -> SenseAssignment:
    """Parse AI response for reuse-or-create sense assignment.

    Expected shape:
        {
          "expression": "...",
          "action": "reuse" | "create",
          "sense_id": <int or null>,
          "meaning": "<text; required when action=create>"
        }

    Validates:
      - expression matches expected (normalized)
      - action is reuse or create
      - reuse → sense_id in prior_sense_ids
      - create → non-empty meaning; sense_id ignored
    """
    json_text = _extract_json(response_text)
    data = _parse_json(json_text)

    if not isinstance(data, dict):
        raise AIValidationError("AI response must be a JSON object")

    expr = data.get("expression")
    if expr is None or not str(expr).strip():
        raise AIValidationError("Missing non-empty 'expression' field")
    if normalize_sentence(str(expr)) != normalize_sentence(expression):
        raise AIValidationError(
            f"Expected expression matching {expression!r} but got {expr!r}"
        )

    action = data.get("action")
    if action is None or not str(action).strip():
        raise AIValidationError("Missing non-empty 'action' field")
    action_norm = str(action).strip().lower()
    if action_norm not in ("reuse", "create"):
        raise AIValidationError(
            f"action must be 'reuse' or 'create', got {action!r}"
        )

    raw_sense_id = data.get("sense_id", None)
    sense_id: int | None = None
    if raw_sense_id is not None and str(raw_sense_id).strip() != "":
        try:
            sense_id = int(raw_sense_id)
        except (TypeError, ValueError):
            # Treat non-integer values (including literal "null") as None.
            sense_id = None

    meaning = data.get("meaning", "")
    meaning_text = str(meaning).strip() if meaning is not None else ""

    allowed = set(prior_sense_ids)

    if action_norm == "reuse":
        if not prior_sense_ids:
            raise AIValidationError(
                "action=reuse is invalid when no prior senses exist"
            )
        if sense_id is None:
            raise AIValidationError("action=reuse requires sense_id")
        if sense_id not in allowed:
            raise AIValidationError(
                f"sense_id {sense_id} is not one of the prior senses "
                f"{sorted(allowed)}"
            )
        return SenseAssignment(
            expression=expression,
            action="reuse",
            sense_id=sense_id,
            meaning="",
        )

    # create
    if not meaning_text:
        raise AIValidationError(
            "action=create requires non-empty 'meaning'"
        )
    return SenseAssignment(
        expression=expression,
        action="create",
        sense_id=None,
        meaning=meaning_text,
    )


# ---------------------------------------------------------------------------
# Membership fallback (local-first residual)
# ---------------------------------------------------------------------------

@dataclass
class MembershipClaim:
    """One AI membership judgment for a residual unfamiliar item."""
    expression: str
    found: bool
    surface: str = ""


def parse_membership_claims(
    response_text: str,
    expected_expressions: list[str],
) -> list[MembershipClaim]:
    """Parse AI membership fallback response.

    Expected shape:
        {"items": [{"expression": "...", "found": true/false, "surface": "..."}, ...]}

    Validates count, order/identity under normalize_sentence, and types.
    Does NOT verify that surface actually occurs in the sentence — that is
    a separate local check (AI cannot be trusted alone).
    """
    json_text = _extract_json(response_text)
    data = _parse_json(json_text)

    if not isinstance(data, dict):
        raise AIValidationError("AI response must be a JSON object")

    items = data.get("items")
    if items is None:
        raise AIValidationError("AI response missing 'items' key")
    if not isinstance(items, list):
        raise AIValidationError("'items' must be a list")

    expected_count = len(expected_expressions)
    if len(items) != expected_count:
        raise AIValidationError(
            f"Expected {expected_count} membership item(s) but AI returned {len(items)}"
        )

    results: list[MembershipClaim] = []
    norm_expected = [normalize_sentence(e) for e in expected_expressions]

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise AIValidationError(f"Item {i} must be a JSON object")

        expr = item.get("expression")
        if expr is None or not str(expr).strip():
            raise AIValidationError(
                f"Item {i} missing required non-empty 'expression' field"
            )

        norm_returned = normalize_sentence(str(expr))
        if norm_returned != norm_expected[i]:
            raise AIValidationError(
                f"Item {i}: expected expression matching "
                f"'{expected_expressions[i]}' but got '{expr}'."
            )

        found_raw = item.get("found")
        if not isinstance(found_raw, bool):
            raise AIValidationError(
                f"Item {i} 'found' must be a boolean"
            )

        surface = item.get("surface", "")
        if surface is None:
            surface = ""
        if not isinstance(surface, str):
            raise AIValidationError(
                f"Item {i} 'surface' must be a string"
            )
        surface = surface.strip()
        if found_raw and not surface:
            raise AIValidationError(
                f"Item {i}: found=true requires a non-empty 'surface' span"
            )
        if not found_raw:
            surface = ""

        results.append(MembershipClaim(
            expression=str(expr),
            found=found_raw,
            surface=surface,
        ))

    return results


# ---------------------------------------------------------------------------
# Word/phrase meanings
# ---------------------------------------------------------------------------

def parse_word_phrase_meanings(
    response_text: str,
) -> list[MeaningResult]:
    """Parse AI response for word/phrase meanings with examples.

    Expected shape:
        {"meanings": [{"meaning": "...", "example": "..."}, ...]}

    Validates:
      - At least 1 meaning is required; at most MAX_WORD_PHRASE_MEANINGS.
      - More than the max → rejects the entire response.
      - Every meaning must have a non-empty 'meaning' field.
      - Every meaning must have a non-empty 'example' field.

    Returns MeaningResult objects where:
      - expression is empty (not used for word/phrase cards)
      - contextual_meaning is formatted as "N. meaning\\n   *example*"

    Raises:
        AIParseError — response is not valid JSON
        AIValidationError — JSON shape is wrong, no meanings, over-limit,
                             or missing/empty required fields
    """
    json_text = _extract_json(response_text)
    data = _parse_json(json_text)

    if not isinstance(data, dict):
        raise AIValidationError("AI response must be a JSON object")

    meanings = data.get("meanings")
    if meanings is None:
        raise AIValidationError("AI response missing 'meanings' key")
    if not isinstance(meanings, list):
        raise AIValidationError("'meanings' must be a list")
    if len(meanings) == 0:
        raise AIValidationError("AI response must contain at least one meaning")

    # Reject over-limit: more than max meanings is malformed, not truncated.
    if len(meanings) > MAX_WORD_PHRASE_MEANINGS:
        raise AIValidationError(
            f"AI returned {len(meanings)} meanings — at most "
            f"{MAX_WORD_PHRASE_MEANINGS} are accepted. "
            "The response is malformed; rejecting the entire output."
        )

    results: list[MeaningResult] = []
    for i, item in enumerate(meanings):
        if not isinstance(item, dict):
            raise AIValidationError(f"Meaning {i} must be a JSON object")

        meaning_text = item.get("meaning")
        if meaning_text is None:
            raise AIValidationError(
                f"Meaning {i} missing required 'meaning' field"
            )
        if not str(meaning_text).strip():
            raise AIValidationError(
                f"Meaning {i} has empty 'meaning' field"
            )

        example = item.get("example")
        if example is None:
            raise AIValidationError(
                f"Meaning {i} missing required 'example' field"
            )
        if not str(example).strip():
            raise AIValidationError(
                f"Meaning {i} has empty 'example' field"
            )

        formatted = f"{i + 1}. {meaning_text}"
        formatted += f"\n   *{example}*"

        results.append(MeaningResult(
            expression="",
            contextual_meaning=formatted,
            meaning=str(meaning_text).strip(),
            example=str(example).strip(),
        ))

    return results
