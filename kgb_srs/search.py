"""Database search with AND/OR logic across card types.

Supports sentence-based cards (searching sentence, expression,
contextual-meaning/back, and child meaning fields) and word/phrase-based
cards (searching front and back).

The parse_search_tokens function returns OR groups of AND operands as
list[list[str]].

- If no AND/OR keyword is present, the entire query is one literal
  substring operand: [["new york"]]
- "alpha AND beta OR gamma" → [["alpha", "beta"], ["gamma"]]
- "alpha OR beta AND gamma" → [["alpha"], ["beta", "gamma"]]

SQL tests each AND operand independently across all fields/child rows
and ORs the groups together.
"""

import re
import unicodedata
from typing import Optional


def _normalize_search_text(s: str) -> str:
    """Normalize text for case- and accent-insensitive substring search.

    NFKD decomposes accented characters (é → e + combining acute),
    then we strip combining marks and casefold, so é maps to e.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(ch)
    ).casefold()


_REGISTERED_CONNS: set = set()


def _register_search_functions(conn):
    """Register Unicode-aware search helpers for casefolded substring match.

    kgb_contains(haystack, needle) returns 1 if needle is a normalized
    substring of haystack, 0 otherwise.  %, _, backslash, and diacritics
    are all treated literally — no LIKE wildcards and no accent
    sensitivity.

    Registration is idempotent; only performed once per connection.
    """
    if conn in _REGISTERED_CONNS:
        return
    _REGISTERED_CONNS.add(conn)

    def _contains(haystack, needle):
        if haystack is None or needle is None:
            return 0
        return 1 if _normalize_search_text(needle) in _normalize_search_text(haystack) else 0

    conn.create_function("kgb_contains", 2, _contains)



# ---------------------------------------------------------------------------
# Tokenization — OR groups of AND operands
# ---------------------------------------------------------------------------

def parse_search_tokens(query: str) -> list[list[str]]:
    """Parse a search query into OR groups of AND operands.

    Returns list[list[str]] where each outer element is an OR group
    and each inner element is an AND operand. Adjacent non-operator
    words are joined into a single phrase operand.

    - No AND/OR keyword → one literal operand: [["new york"]]
    - "new york AND city" → [["new york", "city"]]
    - "new york OR los angeles" → [["new york"], ["los angeles"]]
    - "alpha AND beta OR gamma" → [["alpha", "beta"], ["gamma"]]
    - "alpha OR beta AND gamma" → [["alpha"], ["beta", "gamma"]]
    - "alpha AND beta" → [["alpha", "beta"]]
    - "alpha OR beta" → [["alpha"], ["beta"]]
    """
    if not query or not query.strip():
        return []

    parts = query.split()

    has_operators = any(p.upper() in ("AND", "OR") for p in parts)

    if not has_operators:
        # Plain multi-word query: single literal phrase operand
        return [[" ".join(parts)]]

    # Split into OR groups; within each group, collect non-AND terms
    # Adjacent non-operator tokens accumulate as a single phrase operand
    or_groups: list[list[str]] = []
    current_group: list[str] = []
    phrase_parts: list[str] = []

    def _flush_phrase():
        nonlocal phrase_parts
        if phrase_parts:
            current_group.append(" ".join(phrase_parts))
            phrase_parts = []

    for part in parts:
        upper = part.upper()
        if upper == "OR":
            _flush_phrase()
            if current_group:
                or_groups.append(current_group)
                current_group = []
        elif upper == "AND":
            _flush_phrase()
        else:
            phrase_parts.append(part)

    _flush_phrase()
    if current_group:
        or_groups.append(current_group)

    # Filter empty groups
    or_groups = [g for g in or_groups if g]
    return or_groups


# ---------------------------------------------------------------------------
# Common SQL helpers
# ---------------------------------------------------------------------------

_SENTENCE_BASE_SQL = """\
    SELECT DISTINCT c.id, c.front, c.back, c.box, c.next_review
    FROM cards c
"""


def _search_term(term: str) -> str:
    """Return the raw search term.

    %, _, and backslash are treated literally — no LIKE escaping needed
    because kgb_contains uses Python casefolded substring matching.
    """
    return term


def _build_sentence_search_cond(
    term: str, field_filter: Optional[str]
) -> tuple[str, list]:
    if field_filter == "sentence":
        return "kgb_contains(c.front, ?)", [term]
    elif field_filter == "expression":
        return (
            "EXISTS (SELECT 1 FROM unfamiliar_items ui2"
            " WHERE ui2.card_id = c.id"
            " AND kgb_contains(ui2.expression, ?))",
            [term],
        )
    elif field_filter == "meaning":
        return (
            "(kgb_contains(c.back, ?)"
            " OR EXISTS (SELECT 1 FROM unfamiliar_items ui2"
            " WHERE ui2.card_id = c.id"
            " AND kgb_contains(ui2.meaning, ?)))",
            [term, term],
        )
    else:
        return (
            "(kgb_contains(c.front, ?)"
            " OR kgb_contains(c.back, ?)"
            " OR EXISTS (SELECT 1 FROM unfamiliar_items ui2"
            " WHERE ui2.card_id = c.id"
            " AND (kgb_contains(ui2.expression, ?)"
            " OR kgb_contains(ui2.meaning, ?)))) ",
            [term, term, term, term],
        )


def _build_word_search_cond(
    term: str, field_filter: Optional[str]
) -> tuple[str, list]:
    if field_filter == "front":
        return "kgb_contains(front, ?)", [term]
    elif field_filter == "back":
        return "kgb_contains(back, ?)", [term]
    else:
        return ("(kgb_contains(front, ?)"
                " OR kgb_contains(back, ?))"), [term, term]


def _build_search_sql(
    groups: list[list[str]],
    base_sql: str,
    cond_builder,
    select_cols: str,
) -> tuple[str, list]:
    """Build OR-of-AND query.

    - Single group, single term: simple LIKE.
    - Single group, multi-term: EXISTS per term, AND'd.
    - Multi-group: EXISTS per term inside each group, OR'd across groups.
    """
    if not groups:
        return f"SELECT {select_cols} FROM cards ORDER BY id", []

    if len(groups) == 1 and len(groups[0]) == 1:
        cond, params = cond_builder(groups[0][0], None)
        if "FROM cards c" in base_sql:
            sql = f"{base_sql} WHERE {cond} ORDER BY c.id"
        else:
            sql = f"{base_sql} WHERE {cond} ORDER BY id"
        return sql, params

    # Multi-group or single-group multi-term: OR of AND groups
    group_cond_parts: list[str] = []
    all_params: list = []

    for group in groups:
        and_parts: list[str] = []
        for term in group:
            cond, params = cond_builder(term, None)
            and_parts.append(f"({cond})")
            all_params.extend(params)
        group_cond_parts.append("(" + " AND ".join(and_parts) + ")")

    where = " OR ".join(group_cond_parts)
    if "FROM cards c" in base_sql:
        sql = f"{base_sql} WHERE {where} ORDER BY c.id"
    else:
        sql = f"{base_sql} WHERE {where} ORDER BY id"
    return sql, all_params


# ---------------------------------------------------------------------------
# Sentence-card search
# ---------------------------------------------------------------------------

def search_sentence_cards(
    conn,
    query: str,
    logic: str = "AND",
    field_filter: Optional[str] = None,
) -> list[dict]:
    """Search sentence-based cards.

    Searches the sentence (front), back (contextual meanings), and
    unfamiliar_items.expression AND unfamiliar_items.meaning fields.

    If field_filter is 'sentence', 'expression', or 'meaning', restricts
    to that field.  Otherwise (None or 'all') searches all fields.
    """
    _register_search_functions(conn)
    groups = parse_search_tokens(query)
    cur = conn.cursor()

    if not groups:
        cur.execute(_SENTENCE_BASE_SQL + " ORDER BY c.id")
        rows = cur.fetchall()
        return [_make_result(conn, r) for r in rows]

    def cond_builder(term, _flt):
        return _build_sentence_search_cond(term, field_filter)

    sql, params = _build_search_sql(
        groups, _SENTENCE_BASE_SQL, cond_builder,
        select_cols="DISTINCT c.id, c.front, c.back, c.box, c.next_review",
    )
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [_make_result(conn, r) for r in rows]


# ---------------------------------------------------------------------------
# Word/phrase-card search
# ---------------------------------------------------------------------------

def search_word_phrase_cards(
    conn,
    query: str,
    logic: str = "AND",
    field_filter: Optional[str] = None,
) -> list[dict]:
    """Search word/phrase-based cards.

    Searches front and back fields.

    If field_filter is 'front' or 'back', restricts to that field.
    """
    _register_search_functions(conn)
    groups = parse_search_tokens(query)
    cur = conn.cursor()

    base_sql = "SELECT id, front, back, box, next_review FROM cards"

    if not groups:
        cur.execute(base_sql + " ORDER BY id")
        rows = cur.fetchall()
        return [_make_wp_result(r) for r in rows]

    def cond_builder(term, _flt):
        return _build_word_search_cond(term, field_filter)

    sql, params = _build_search_sql(
        groups, base_sql, cond_builder,
        select_cols="id, front, back, box, next_review",
    )
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [_make_wp_result(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_expressions(conn, card_id: int) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT expression FROM unfamiliar_items WHERE card_id=? ORDER BY id",
        (card_id,),
    )
    return [r[0] for r in cur.fetchall()]


def _make_result(conn, r):
    return {
        "id": r[0],
        "front": r[1],
        "back": r[2],
        "box": r[3],
        "next_review": r[4],
        "expressions": _fetch_expressions(conn, r[0]),
    }


def _make_wp_result(r):
    return {
        "id": r[0],
        "front": r[1],
        "back": r[2],
        "box": r[3],
        "next_review": r[4],
    }
