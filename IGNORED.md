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

## Not ignored (fixed on `dev`)

See commits:

- `876a37d` — audit round 1 (W/P SRS preserve, orphan purge on write, review grading, SAVEPOINT, AI sense_id, TTS signal/lifetime, membership close guard, no manual W/P create, resume queue)
- `0eb2055` — audit round 2 (delete → purge + W/P re-sync, history scrub, Previous skips ghosts, TTS temp unlink, CHANGELOG count)

Round 3 re-audit: **CLEAN** of remaining actionable safe findings (437 tests).
