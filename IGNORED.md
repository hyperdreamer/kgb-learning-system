# IGNORED.md — Deferred audit findings

Findings left unfixed after the audit→fix loop on `dev` (through commits
`876a37d` and `0eb2055`). These were **not** applied because they either
risk user-visible regressions, require careful UX/test rework, or are
optional cleanup rather than correctness bugs.

Re-open only with an explicit product decision and regression tests.

---

## 1. AI Generate Meaning materializes senses before Save

| Field | Value |
|-------|--------|
| **Severity** | Medium |
| **Files** | `kgb_srs/forms.py` (~739–741) — AI “create” path calls `create_or_get_sense(..., commit=True)` |
| **Behavior** | Generate Meaning with `action=create` inserts into `expression_senses` immediately. Cancel / reject leaves an unreferenced sense until a later insert/update runs `purge_orphan_senses`. |
| **Why deferred** | Changing commit timing can break sense_id wiring and the “Reused sense #N” / create UX. Needs coordinated dialog + Save tests. |
| **Risk if fixed** | Medium — preferred-sense identity and Save resolution must stay correct. |
| **Mitigation already in place** | W/P projection filters to senses with `unfamiliar_items` references (`group_senses_by_expression` / `sense_has_item_references`), so orphan catalog rows do **not** appear in the dictionary. Insert/update/delete paths purge orphans. |
| **Suggested fix (when ready)** | Create with `commit=False` and only persist on Save via existing `insert_sentence_card` / `update_sentence_card`, **or** call `purge_orphan_senses` on dialog reject/close when no save occurred. |

---

## 2. `WordPhraseCardDialog` dead code still present

| Field | Value |
|-------|--------|
| **Severity** | Low (maintainability) |
| **Files** | `kgb_srs/forms.py` (`WordPhraseCardDialog`); many tests in `tests/test_regression.py` |
| **Behavior** | Full manual W/P authoring dialog remains implemented and tested, but the main app is projection-only (no Add Entry / Edit / Delete for W/P; create dialog no longer offers W/P DBs). |
| **Why deferred** | Pure dead-code cleanup with large test churn; no runtime defect for users. |
| **Risk if fixed** | Low–medium test maintenance cost; risk of breaking residual helper paths if removed carelessly. |
| **Suggested fix (when ready)** | Gate or remove the dialog and migrate/delete only the tests that exclusively cover the dead UI path; keep any shared helpers still used by sentence flows. |

---

## 3. `find_duplicate_sentence_card` linear-scans all cards (O(n))

| Field | Value |
|-------|--------|
| **Severity** | Low (efficiency) |
| **Files** | `kgb_srs/schema.py:328-333` |
| **Behavior** | Fetches every card row then re-queries child items per candidate. O(n) with nested queries. |
| **Why deferred** | Performance optimization, not a correctness bug. Changing query logic risks breaking the duplicate-detection UX (edit-offer instead of silent duplicate). |
| **Risk if fixed** | Medium — duplicate detection is a user-facing gate. A missed duplicate silently creates a redundant card. |
| **Suggested fix (when ready)** | Push normalization into SQLite via generated column or registered collation, or filter candidates with SQL WHERE before Python-side loop. Needs duplicate-detection regression tests. |

---

## 4. QThread subclasses redefined on every worker creation

| Field | Value |
|-------|--------|
| **Severity** | Low (efficiency) |
| **Files** | `kgb_srs/ai_provider.py:640-739` |
| **Behavior** | `_get_ai_worker_class()` and siblings define new QThread subclasses on every invocation, re-executing the `class AIWorker(QThread):` block. |
| **Why deferred** | No correctness impact — signal binding is per-instance. Moving classes to module level risks breaking closure-variable capture or lazy-import ordering. |
| **Risk if fixed** | Medium — QThread with pyqtSignal requires QApplication to exist. Module-level class definitions at import time could fail if Qt isn't initialized yet. |
| **Suggested fix (when ready)** | Define classes once at module level, guarding with a lazy-init pattern or deferring the PyQt6 import to after QApplication creation. |

---

## 5. `parse_sense_assignment` silently swallows invalid `sense_id` (behavior change)

| Field | Value |
|-------|--------|
| **Severity** | Low (observation, not a bug) |
| **Files** | `kgb_srs/ai_parser.py:215-221` |
| **Behavior** | Previously raised `AIValidationError` for non-integer `sense_id` values (e.g., literal `"null"` string from AI). Now silently falls back to `sense_id = None`. |
| **Why deferred** | Intentional trade-off in round 3 fix. The old behavior was overly strict for rare AI output quirks. The silent fallback is safer for end users but could mask future AI parsing regressions. |
| **Risk if changed back** | Low — restoring the error would re-break on literal `"null"` strings from certain LLMs. |

---

## Not ignored (fixed on `dev`)

See commits:

- `876a37d` — audit round 1 (W/P SRS preserve, orphan purge on write, review grading, SAVEPOINT, AI sense_id, TTS signal/lifetime, membership close guard, no manual W/P create, resume queue)
- `0eb2055` — audit round 2 (delete → purge + W/P re-sync, history scrub, Previous skips ghosts, TTS temp unlink, CHANGELOG count)
- `f48b8f3` — audit round 3 (meaning/example split via MeaningResult fields, atomic W/P derivation commit, DB-open error handling + conn close, search function registration cache, DB/TTS file permissions 0o600, non-integer sense_id silent fallback, 60+ irregular noun plurals)

Round 4 re-audit: **CLEAN** — no actionable findings remain (473 tests).

---

## 6. `_REGISTERED_CONNS` global set retains connection references (minor memory leak)

| Field | Value |
|-------|--------|
| **Severity** | Low (efficiency) |
| **Files** | `kgb_srs/search.py:36` |
| **Behavior** | Module-level `set()` accumulates every `sqlite3.Connection` for which `kgb_contains` was registered. Closed connections are never removed, preventing GC. Accumulation across `load_database` calls is unbounded. |
| **Why deferred** | `sqlite3.Connection` objects are not hashable for `weakref.WeakSet` — the natural fix is incompatible. In practice connections are long-lived, making the leak negligible. |
| **Risk if fixed** | Low — using `weakref` or connection-id-based schemes would work but adds complexity for minimal practical benefit. |
| **Suggested fix (when ready)** | Use `id(conn)` as the cache key instead of `conn` itself, or accept the leak as de minimis.

---

## Round 4+ fixes (applied on `dev`)

See commits:

- `c9b89f7` — audit round 4 (15 findings: M1 duplicate test class, M2 dead _split_meaning_example, M4 92 ruff → 0, M5 double TTS signal wiring, L1-L12 unused imports/variables, _make_http_call inlined)
- `641d0e2` — re-audit regression (C1 _clear_all_rows method restored, L1 dead getattr removed, L2 unused logic param documented)

Round 5 final re-audit: **CLEAN** — 472 tests, ruff 0 violations, ACTIONABLE FINDINGS: None.
